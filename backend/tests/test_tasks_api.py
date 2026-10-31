"""任务 API 离线测试：创建（202/幂等/校验）、状态查询与原生文件下载（Range）。"""
import pytest
from fastapi.testclient import TestClient

from app import main
from app.tasks import routes as task_routes
from app.tasks.store import TaskStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(task_routes, "_store", TaskStore(tmp_path / "db.sqlite", tmp_path))
    return TestClient(main.app)


def test_create_task_returns_202(client):
    res = client.post("/api/tasks", json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "http-2176"})
    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "queued" and body["id"]
    assert body["status_url"] == f"/api/tasks/{body['id']}"


def test_create_rejects_invalid_url(client):
    res = client.post("/api/tasks", json={
        "url": "https://evil.example/video", "kind": "video", "format_id": "f"})
    assert res.status_code == 422


def test_create_rejects_unsupported_kind(client):
    res = client.post("/api/tasks", json={
        "url": "https://x.com/i/status/123", "kind": "bookmark_batch", "ids": ["1234567890"]})
    assert res.status_code == 422


def test_idempotency_key_returns_same_task(client):
    headers = {"Idempotency-Key": "client-key-1"}
    first = client.post("/api/tasks", headers=headers, json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "f"})
    again = client.post("/api/tasks", headers=headers, json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "f"})
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["reused"] is True


def test_get_unknown_task_returns_404(client):
    assert client.get("/api/tasks/no-such-id").status_code == 404


def test_get_task_status(client):
    created = client.post("/api/tasks", json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "f"}).json()
    status = client.get(f"/api/tasks/{created['id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"


def test_file_requires_ready(client):
    created = client.post("/api/tasks", json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "f"}).json()
    assert client.get(f"/api/tasks/{created['id']}/file").status_code == 409


def test_file_download_supports_range(client, tmp_path):
    store = task_routes._store
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    output = store.file_path(task["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"0123456789")
    store.complete(task["id"], file_size=10)

    full = client.get(f"/api/tasks/{task['id']}/file")
    assert full.status_code == 200
    assert full.headers["content-type"].startswith("video/mp4")
    assert "attachment" in full.headers.get("content-disposition", "")

    partial = client.get(f"/api/tasks/{task['id']}/file",
                         headers={"Range": "bytes=2-5"})
    assert partial.status_code == 206
    assert partial.content == b"2345"

    invalid = client.get(f"/api/tasks/{task['id']}/file",
                         headers={"Range": "bytes=99-200"})
    assert invalid.status_code in (416, 200)


def test_file_named_after_video_title(client):
    store = task_routes._store
    task = store.create(kind="video", url="u", platform="youtube", format_id="f")
    store.set_title(task["id"], "Big Buck Bunny: 完整版 1080p")
    output = store.file_path(task["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"data")
    store.complete(task["id"], file_size=4)

    res = client.get(f"/api/tasks/{task['id']}/file")
    assert res.status_code == 200
    disposition = res.headers["content-disposition"]
    from urllib.parse import unquote
    assert "Big Buck Bunny" in unquote(disposition)
    assert "1080p" in unquote(disposition)
    assert "/" not in unquote(disposition).split("''")[-1]


def test_file_falls_back_to_task_id_without_title(client):
    store = task_routes._store
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    output = store.file_path(task["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"data")
    store.complete(task["id"], file_size=4)
    res = client.get(f"/api/tasks/{task['id']}/file")
    assert f"video-{task['id']}.mp4" in res.headers["content-disposition"]


def test_history_endpoint_lists_recorded_extracts(client):
    client.post("/api/tasks", json={
        "url": "https://x.com/i/status/123", "kind": "video", "format_id": "f"})
    history = client.get("/api/history")
    assert history.status_code == 200
    assert isinstance(history.json(), list)


def test_cover_endpoint_serves_local_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FRAME_DATA_DIR", str(tmp_path))
    task_routes._store = TaskStore(tmp_path / "db.sqlite", tmp_path)
    cover = tmp_path / "covers" / "BV1abc.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\xff\xd8jpegdata")

    ok = client.get("/api/covers/BV1abc.jpg")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/jpeg")

    assert client.get("/api/covers/no-such.jpg").status_code == 404
    assert client.get("/api/covers/..%2Fescape").status_code == 404


def test_thumbnail_localized_to_own_origin(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FRAME_DATA_DIR", str(tmp_path))
    task_routes._store = TaskStore(tmp_path / "db.sqlite", tmp_path)
    captured_thumb = {}

    def fake_record(url, platform, data, formats, thumbnail=None):
        captured_thumb["value"] = thumbnail

    monkeypatch.setattr(main, "_record_history", fake_record)

    payload = {"url": "https://www.bilibili.com/video/BV1GJ411x7h7",
               "kind": "video", "format_id": "30064+30280"}
    monkeypatch.setattr(main, "canonical_url",
                        lambda u: ("https://x.com/i/status/1", "bilibili"))
    monkeypatch.setattr(main, "extract", lambda u: {
        "id": "BV1GJ411x7h7", "title": "T", "thumbnail": "https://i1.hdslb.com/x.jpg",
        "formats": [{"id": "30064+30280", "height": 720}],
        "audio_available": True, "subtitles": []})
    monkeypatch.setattr(main, "choices", lambda d, p: [{"id": "30064+30280"}])
    monkeypatch.setattr(main, "subtitle_choices", lambda d: [])
    monkeypatch.setattr(main, "audio_available", lambda d: True)

    def fake_fetch(req, timeout=15):
        import urllib.request as ur
        return _FakeCoverResponse(b"jpeg-bytes")

    import urllib.request as ur
    class _FakeCoverResponse:
        def __init__(self, data): self._d = data
        def read(self, n=-1): return self._d if n == -1 else self._d[:n]
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class FakeOpener:
        def open(self, req, timeout=15):
            return fake_fetch(req, timeout=timeout)

    monkeypatch.setattr(ur, "build_opener", lambda *handlers: FakeOpener())
    monkeypatch.setattr(main, "validate_media_url", lambda url, **kwargs: url)

    res = client.post("/api/extract", json=payload)
    assert res.status_code == 200
    assert res.json()["thumbnail"] == "/api/covers/BV1GJ411x7h7.jpg"
    assert captured_thumb["value"] == "/api/covers/BV1GJ411x7h7.jpg"
    assert (tmp_path / "covers" / "BV1GJ411x7h7.jpg").read_bytes() == b"jpeg-bytes"


def test_cover_endpoint_serves_local_file(client, tmp_path, monkeypatch):
    monkeypatch.setenv("FRAME_DATA_DIR", str(tmp_path))
    task_routes._store = TaskStore(tmp_path / "db.sqlite", tmp_path)
    cover = tmp_path / "covers" / "BV1abc.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\xff\xd8jpegdata")

    ok = client.get("/api/covers/BV1abc.jpg")
    assert ok.status_code == 200
    assert ok.headers["content-type"].startswith("image/jpeg")
    assert client.get("/api/covers/no-such.jpg").status_code == 404
    assert client.get("/api/covers/..%2Fescape").status_code == 404


def test_expired_task_file_returns_410(client, tmp_path):
    store = task_routes._store
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    output = store.file_path(task["id"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(b"data")
    store.mark_expired(task["id"])
    res = client.get(f"/api/tasks/{task['id']}/file")
    assert res.status_code == 410
