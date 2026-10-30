"""Worker 离线测试：领取执行、状态流转、失败分类与产物落位（mock 下载器）。"""
import pytest

from app import main
from app.tasks import worker
from app.tasks.store import TaskStore


@pytest.fixture()
def store(tmp_path):
    return TaskStore(tmp_path / "db.sqlite", tmp_path)


def test_no_task_returns_false(store):
    assert worker.process_next_task(store) is False


def test_successful_task_flows_to_ready(store, monkeypatch):
    task = store.create(kind="video", url="https://x.com/i/status/123",
                        platform="x", format_id="http-2176")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [
        {"id": "http-2176", "label": "720p"}])

    def fake_download(url, format_id, folder, platform):
        output = folder / "video.mp4"
        output.write_bytes(b"video-bytes")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    assert worker.process_next_task(store) is True
    done = store.get(task["id"])
    assert done["status"] == "ready" and done["file_size"] == 11
    assert store.file_path(task["id"]).read_bytes() == b"video-bytes"


def test_unavailable_format_fails_fast_without_download(store, monkeypatch):
    task = store.create(kind="video", url="u", platform="x", format_id="gone")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [
        {"id": "http-2176", "label": "720p"}])
    downloaded = []
    monkeypatch.setattr(main, "download_video",
                        lambda *a, **k: downloaded.append(a) or (_ for _ in ()).throw(AssertionError("不应下载")))
    assert worker.process_next_task(store) is True
    failed = store.get(task["id"])
    assert failed["status"] == "failed" and "画质" in failed["error"]
    assert not downloaded


def test_download_error_marks_failed(store, monkeypatch):
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def broken_download(url, format_id, folder, platform):
        raise ValueError("网络超时")

    monkeypatch.setattr(main, "download_video", broken_download)
    assert worker.process_next_task(store) is True
    failed = store.get(task["id"])
    assert failed["status"] == "failed" and "网络超时" in failed["error"]


def test_recovered_task_is_claimable_again(store, monkeypatch):
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    store.update_status(task["id"], "downloading")
    store.recover_running()
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def fake_download(url, format_id, folder, platform):
        output = folder / "video.mp4"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    assert worker.process_next_task(store) is True
    assert store.get(task["id"])["status"] == "ready"


def test_title_persisted_from_extract(store, monkeypatch):
    task = store.create(kind="video", url="u", platform="youtube", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "Big Buck Bunny"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def fake_download(url, format_id, folder, platform):
        output = folder / "video.mp4"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    worker.process_next_task(store)
    assert store.get(task["id"])["title"] == "Big Buck Bunny"
