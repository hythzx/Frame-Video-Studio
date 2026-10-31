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


def test_format_fallback_via_download_video(store, monkeypatch):
    # YouTube 格式 ID 在多次提取间漂移：worker 不做精确校验，
    # 由 download_video 内部按分辨率就近降级。
    task = store.create(kind="video", url="u", platform="youtube", format_id="136+140")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "Big Buck Bunny"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [
        {"id": "135+140", "height": 480}, {"id": "134+140", "height": 360}])
    captured = {}

    def fake_download(url, format_id, folder, platform):
        captured["format"] = format_id
        output = folder / "video.mp4"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    worker.process_next_task(store)
    assert captured["format"] == "136+140"
    assert store.get(task["id"])["status"] == "ready"


def test_cleanup_removes_expired_files_only(store, monkeypatch):
    import sqlite3
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def fake_download(url, format_id, folder, platform):
        output = folder / "video.mp4"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    worker.process_next_task(store)
    assert store.file_path(task["id"]).exists()

    # 回拨完成时间到 7 小时前
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE tasks SET updated_at = datetime('now', '-7 hours') WHERE id = ?", (task["id"],))
    conn.commit()

    removed = worker.cleanup_expired_files(store, ttl_hours=6)
    assert removed == 1
    assert not store.file_path(task["id"]).exists()
    assert store.get(task["id"])["status"] == "expired"

    # 再次清理：无可清理
    assert worker.cleanup_expired_files(store, ttl_hours=6) == 0


def test_cleanup_keeps_recent_files(store, monkeypatch):
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "t"})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])
    def fake_download(url, format_id, folder, platform):
        output = folder / "video.mp4"
        output.write_bytes(b"x")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    worker.process_next_task(store)
    assert worker.cleanup_expired_files(store, ttl_hours=6) == 0
    assert store.get(task["id"])["status"] == "ready"
