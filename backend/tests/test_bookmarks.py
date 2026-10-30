"""Offline tests: never access X or require real account credentials."""
from types import SimpleNamespace
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from app import bookmarks, main

client = TestClient(main.app)
TOKEN = "test-secret-value-longer-than-twenty-characters"


def setup_env(monkeypatch):
    monkeypatch.setenv("FRAME_ACCESS_TOKEN", TOKEN)
    monkeypatch.setenv("TWITTER_COOKIES", "auth_token=unit-test; ct0=unit-csrf")


def test_bookmark_api_disabled_without_access_secret(monkeypatch):
    monkeypatch.delenv("FRAME_ACCESS_TOKEN", raising=False)
    assert client.get("/api/bookmarks").status_code == 503


def test_bookmark_api_rejects_unauthorized(monkeypatch):
    setup_env(monkeypatch)
    assert client.get("/api/bookmarks").status_code == 401
    assert client.get("/api/bookmarks", headers={"X-Frame-Token": "incorrect"}).status_code == 401


def test_bookmark_api_requires_x_cookies(monkeypatch):
    setup_env(monkeypatch)
    monkeypatch.delenv("TWITTER_COOKIES")
    assert client.get("/api/bookmarks", headers={"X-Frame-Token": TOKEN}).status_code == 503


def test_cookie_parser_never_exposes_cookies_in_response(monkeypatch):
    setup_env(monkeypatch)
    assert bookmarks._cookie_pairs() == {"auth_token": "unit-test", "ct0": "unit-csrf"}


def test_serialize_video_tweet():
    result = {
        "rest_id": "123456789012345",
        "legacy": {"full_text": "hello", "created_at": "today",
                   "extended_entities": {"media": [
                       {"type": "video", "media_url": "https://pbs.twimg.com/media/abc.jpg"}]}},
        "core": {"user_results": {"result": {"legacy": {"name": "Example", "screen_name": "example"}}}},
    }
    item = bookmarks._serialize_tweet_result(result)
    assert item["id"] == "123456789012345"
    assert item["has_video"]
    assert item["url"] == "https://x.com/i/status/123456789012345"
    assert item["author"] == "Example"


def test_serialize_text_only_tweet():
    result = {"rest_id": "1234567890", "legacy": {"full_text": "text"},
              "core": {"user_results": {"result": {"legacy": {}}}}}
    assert bookmarks._serialize_tweet_result(result)["has_video"] is False


def test_bookmark_list_endpoint_with_mock(monkeypatch):
    setup_env(monkeypatch)

    async def fake_page(cursor, count):
        return {"items": [{"id": "1234567890", "has_video": True}], "next_cursor": "next-token"}

    monkeypatch.setattr(bookmarks, "_fetch_page", fake_page)
    response = client.get("/api/bookmarks", headers={"X-Frame-Token": TOKEN})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["next_cursor"] == "next-token"


def test_batch_validates_ids_and_limits(monkeypatch):
    setup_env(monkeypatch)
    headers = {"X-Frame-Token": TOKEN}
    assert client.post("/api/bookmarks/download", headers=headers, json={"ids": ["../../../etc/passwd"]}).status_code == 422
    assert client.post("/api/bookmarks/download", headers=headers, json={"ids": ["1234567890"] * 6}).status_code == 422
    assert client.post("/api/bookmarks/download", headers=headers, json={"ids": []}).status_code == 422


def test_batch_archive_with_mock_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract", lambda url: {"formats": []})
    monkeypatch.setattr(main, "choices", lambda data, platform: [{"id": "test"}])

    def fake_download(url, format_id, destination, platform):
        output = destination / "video.mp4"
        output.write_bytes(b"fake media data")
        return output

    monkeypatch.setattr(main, "download_video", fake_download)
    output = bookmarks._download_bookmarks(["1234567890", "9876543210"], tmp_path)
    assert output.suffix == ".zip"
    with zipfile.ZipFile(output) as archive:
        assert set(archive.namelist()) == {"x-1234567890.mp4", "x-9876543210.mp4"}

def test_fetch_page_uses_cookies_and_cursor(monkeypatch):
    import asyncio
    setup_env(monkeypatch)
    monkeypatch.setattr(bookmarks, "_LAST_PAGE_REQUEST", 0.0)
    captured = {}

    def fake_request(cursor, count):
        captured["cursor"] = cursor
        captured["count"] = count
        headers = bookmarks._bookmarks_headers()
        captured["cookie_has_auth"] = "auth_token=unit-test" in headers["cookie"]
        captured["csrf"] = headers["x-csrf-token"]
        return {"data": {"bookmark_timeline_v2": {"timeline": {"instructions": [
            {"type": "TimelineAddEntries", "entries": [
                {"entryId": "tweet-1234567890", "content": {"entryType": "TimelineTimelineItem",
                 "itemContent": {"tweet_results": {"result": {
                     "rest_id": "1234567890",
                     "legacy": {"full_text": "Test", "created_at": "now"},
                     "core": {"user_results": {"result": {"legacy": {
                         "name": "Tester", "screen_name": "tester"}}}}}}}}},
                {"entryId": "cursor-bottom-x", "content": {"entryType": "TimelineTimelineCursor",
                 "value": "cursor-two"}},
            ]}]}}}}

    monkeypatch.setattr(bookmarks, "_request_bookmarks_page", fake_request)
    result = asyncio.run(bookmarks._fetch_page("cursor-one", 15))
    assert result["next_cursor"] == "cursor-two"
    assert result["items"][0]["id"] == "1234567890"
    assert captured["cursor"] == "cursor-one"
    assert captured["count"] == 15
    assert captured["cookie_has_auth"] is True
    assert captured["csrf"] == "unit-csrf"


def test_fetch_page_cooldown(monkeypatch):
    import asyncio
    import pytest
    setup_env(monkeypatch)
    monkeypatch.setattr(bookmarks, "_LAST_PAGE_REQUEST", float("inf"))
    with pytest.raises(Exception) as error:
        asyncio.run(bookmarks._fetch_page(None, 20))
    assert getattr(error.value, "status_code", None) == 429
