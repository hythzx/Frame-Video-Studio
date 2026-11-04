"""X download filename tests (offline; no real X API or video requests)."""
import json
import zipfile
from pathlib import Path
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app import main, bookmarks
from app.filenames import tweet_body, tweet_id_from_url, tweet_video_stem, unique_video_name
from app.tasks import routes, worker
from app.tasks.store import TaskStore


def test_x_filename_first_20_after_removing_all_whitespace():
    text = "这 是\n一 段\t中文 测试标题内容以及更多的文字内容!"
    cleaned = "".join(c for c in text if not c.isspace())
    assert tweet_video_stem(text, "1234567890") == cleaned[:20]
    assert len(tweet_video_stem(text, "1234567890")) == 20


@pytest.mark.parametrize("text", ["", None, "  \n\t  ", "/\\:*?<>|  "])
def test_x_filename_only_falls_back_for_missing_usable_body(text):
    assert tweet_video_stem(text, "1234567890") == "1234567890"


def test_x_filename_strips_unsafe_path_chars_and_unicode_controls():
    assert tweet_video_stem("  你好 / \\ : ? 世界 \r\n", "1234567890") == "你好世界"
    assert tweet_video_stem("CON", "1234567890") == "_CON"
    assert tweet_video_stem("保存\u202e名称", "1234567890") == "保存名称"
    assert tweet_id_from_url("https://x.com/i/status/1234567890") == "1234567890"
    assert tweet_id_from_url("https://x.com/user/status/1234567890") == "1234567890"


def test_x_tweet_body_prefers_body_not_yt_dlp_title():
    assert tweet_body({"title": "Video by user", "description": "正文 内容"}) == "正文 内容"
    assert tweet_body({"title": "Video by user"}) == ""
    assert tweet_body({"full_text": "原创 正文", "description": "另一描述"}) == "原创 正文"


def test_zip_same_title_gets_numbered_suffix_not_id():
    names = set()
    assert unique_video_name("中文标题", names) == "中文标题.mp4"
    assert unique_video_name("中文标题", names) == "中文标题(2).mp4"
    assert unique_video_name("中文标题", names) == "中文标题(3).mp4"


def test_direct_x_download_uses_post_text_but_youtube_name_is_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract", lambda url: {
        "id": "1234567890", "description": "这是 一段 测试正文，来自推文",
        "title": "Alice - Video", "formats": []})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f", "height": 720}])

    class DummyYDL:
        def __init__(self, opts):
            self.opts = opts
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def download(self, urls):
            Path(self.opts["outtmpl"].replace("%(ext)s", "mp4")).write_bytes(b"mock-video")
    monkeypatch.setattr(main, "YoutubeDL", DummyYDL)
    monkeypatch.setattr(main, "_apply_cookies", lambda ydl: None)
    output = main.download_video("https://x.com/i/status/1234567890", "f", tmp_path, "x")
    assert output.name == "这是一段测试正文，来自推文.mp4"
    assert output.read_bytes() == b"mock-video"

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = main.download_video("https://www.youtube.com/watch?v=abcdefghijk", "f",
                                other_dir, "youtube")
    assert other.name == "video.mp4"


def test_bookmark_batch_zip_uses_text_from_bookmark_api(monkeypatch, tmp_path):
    ids = ["1234567890", "9876543210", "1234509876"]
    monkeypatch.setattr(main, "extract", lambda url: {"formats": [], "description": ""})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def fake_download(url, format_id, destination, platform):
        file = destination / "video.mp4"
        file.write_bytes(b"video")
        return file
    monkeypatch.setattr(main, "download_video", fake_download)

    texts = {ids[0]: "相同 标题", ids[1]: "相同标题", ids[2]: ""}
    result = bookmarks._download_bookmarks(ids, tmp_path, texts=texts)
    with zipfile.ZipFile(result) as zf:
        assert zf.namelist() == ["相同标题.mp4", "相同标题(2).mp4", ids[2] + ".mp4"]


def test_bookmark_single_file_uses_text_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract", lambda url: {"formats": [], "description": ""})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "f"}])

    def fake_download(url, format_id, destination, platform):
        file = destination / "video.mp4"
        file.write_bytes(b"video")
        return file
    monkeypatch.setattr(main, "download_video", fake_download)

    x = bookmarks._download_bookmarks(["1234567890"], tmp_path, texts={"1234567890": "这 是 正文"})
    assert x.name == "这是正文.mp4"

    other = tmp_path / "other"
    other.mkdir()
    x2 = bookmarks._download_bookmarks(["9876543210"], other)
    assert x2.name == "9876543210.mp4"


def test_x_worker_and_file_response_use_full_post_body(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite", tmp_path)
    task = store.create(kind="video", url="https://x.com/i/status/1234567890",
                        platform="x", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {
        "title": "Alice - NOT the tweet body",
        "description": "这是 推文的 真正内容！",
    })
    def fake_download(url, format_id, folder, platform):
        f = folder / "video.mp4"
        f.write_bytes(b"video")
        return f
    monkeypatch.setattr(main, "download_video", fake_download)
    assert worker.process_next_task(store) is True
    monkeypatch.setattr(routes, "_store", store)
    resp = TestClient(main.app).get(f"/api/tasks/{task['id']}/file")
    assert resp.status_code == 200
    assert "这是推文的真正内容！.mp4" in unquote(resp.headers["content-disposition"])


def test_x_worker_fallback_uses_tweet_id_not_task_uuid(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite", tmp_path)
    task = store.create(kind="video", url="https://x.com/i/status/1234567890",
                        platform="x", format_id="f")
    monkeypatch.setattr(main, "extract", lambda url: {"title": "Uploader video"})
    def fake_download(url, format_id, folder, platform):
        f = folder / "video.mp4"
        f.write_bytes(b"video")
        return f
    monkeypatch.setattr(main, "download_video", fake_download)
    worker.process_next_task(store)
    monkeypatch.setattr(routes, "_store", store)
    resp = TestClient(main.app).get(f"/api/tasks/{task['id']}/file")
    assert resp.status_code == 200
    assert "1234567890.mp4" in resp.headers["content-disposition"]


def test_bookmark_worker_preserves_text_in_single_file_response(monkeypatch, tmp_path):
    store = TaskStore(tmp_path / "tasks.sqlite", tmp_path)
    tweet_id = "1234567890"
    task = store.create(kind="bookmark_batch", url="", platform="x", format_id="",
                        params_json=json.dumps({"ids": [tweet_id],
                                                "texts": {tweet_id: "保 存 这 条 推文"}}))
    def fake_download(ids, folder, texts=None):
        assert texts[tweet_id] == "保 存 这 条 推文"
        f = folder / "保存这条推文.mp4"
        f.write_bytes(b"video")
        return f
    monkeypatch.setattr(bookmarks, "_download_bookmarks", fake_download)
    assert worker.process_next_task(store) is True
    monkeypatch.setattr(routes, "_store", store)
    resp = TestClient(main.app).get(f"/api/tasks/{task['id']}/file")
    assert resp.status_code == 200
    assert "保存这条推文.mp4" in unquote(resp.headers["content-disposition"])
