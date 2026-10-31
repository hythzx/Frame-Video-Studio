"""Offline YouTube channel browser tests. No external requests or real cookies."""
import pytest
from fastapi.testclient import TestClient

from app import main, youtube_channels
from app.tasks import routes as task_routes
from app.tasks.store import TaskStore

client = TestClient(main.app)


@pytest.mark.parametrize("value,expected", [
    ("https://www.youtube.com/@somecreator", "https://www.youtube.com/@somecreator/videos"),
    ("https://youtube.com/@somecreator/shorts", "https://www.youtube.com/@somecreator/videos"),
    ("https://www.youtube.com/channel/UC" + "a" * 22, "https://www.youtube.com/channel/UC" + "a" * 22 + "/videos"),
    ("https://www.youtube.com/c/MyCreator", "https://www.youtube.com/c/MyCreator/videos"),
    ("https://www.youtube.com/user/MyCreator", "https://www.youtube.com/user/MyCreator/videos"),
])
def test_channel_normalization(value, expected):
    assert youtube_channels.canonical_channel_url(value) == expected


def test_channel_shorts_and_streams():
    assert youtube_channels.canonical_channel_url(
        "https://www.youtube.com/@someone/videos", "shorts"
    ) == "https://www.youtube.com/@someone/shorts"
    assert youtube_channels.canonical_channel_url(
        "https://www.youtube.com/@someone", "streams"
    ) == "https://www.youtube.com/@someone/streams"


@pytest.mark.parametrize("value", [
    "https://www.youtube.com/watch?v=12345678901",
    "https://www.youtube.com/playlist?list=abcdefgh",
    "https://youtu.be/12345678901",
    "https://youtube.com.evil.net/@creator",
    "http://www.youtube.com/@creator",
    "https://www.youtube.com:8080/@creator",
    "https://localhost/@creator",
    "https://www.youtube.com/@creator/../../admin",
])
def test_channel_rejects_non_channel_or_unsafe_links(value):
    with pytest.raises(ValueError):
        youtube_channels.canonical_channel_url(value)


def test_channel_page_flat_and_has_more(monkeypatch):
    called = {}

    class FakeYoutubeDL:
        def __init__(self, opts):
            called.update(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def extract_info(self, url, download=False):
            called["url"] = url
            called["download"] = download
            return {
                "_type": "playlist", "channel": "Example Creator",
                "entries": [
                    {"id": f"V{i:010d}", "title": f"Upload {i}",
                     "duration": 120, "upload_date": "20261030",
                     "thumbnail": f"https://i.ytimg.com/vi/V{i:010d}/hqdefault.jpg"}
                    for i in range(25)
                ],
            }
    monkeypatch.setattr(youtube_channels, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(main, "_apply_cookies", lambda ydl: None)
    result = youtube_channels._channel_page("https://www.youtube.com/@creator/videos", 1)
    assert called["extract_flat"] == "in_playlist"
    assert called["playliststart"] == 1
    assert called["playlistend"] == 25
    assert called["skip_download"] is True
    assert called["download"] is False
    assert len(result["items"]) == 24
    assert result["next_start"] == 25
    assert result["channel_title"] == "Example Creator"
    assert result["items"][0]["url"] == "https://www.youtube.com/watch?v=V0000000000"


def test_channel_end_and_video_url_fallback(monkeypatch):
    class FakeYoutubeDL:
        def __init__(self, opts):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def extract_info(self, url, download=False):
            return {"entries": [
                {"url": "https://www.youtube.com/watch?v=abcdefghi_1",
                 "title": "One"},
                {"id": "not-valid", "title": "Skip"},
            ]}
    monkeypatch.setattr(youtube_channels, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(main, "_apply_cookies", lambda ydl: None)
    result = youtube_channels._channel_page("https://www.youtube.com/@creator/videos", 25)
    assert [x["id"] for x in result["items"]] == ["abcdefghi_1"]
    assert result["next_start"] is None


def test_channel_api_validates_section():
    r = client.get("/api/youtube/channels", params={
        "url": "https://www.youtube.com/@creator", "section": "private"})
    assert r.status_code == 422


def test_channel_api_returns_page(monkeypatch):
    monkeypatch.setattr(youtube_channels, "_channel_page", lambda url, start: {
        "channel_title": "Example Creator",
        "items": [{"id": "abcdefghijk", "title": "Upload"}],
        "next_start": 25,
    })
    res = client.get("/api/youtube/channels", params={
        "url": "https://www.youtube.com/@creator", "section": "videos", "start": "1"})
    assert res.status_code == 200
    assert res.json()["items"][0]["id"] == "abcdefghijk"
    assert res.json()["next_start"] == 25
    assert res.json()["url"] == "https://www.youtube.com/@creator/videos"


def test_channel_video_task_automatically_selects_highest_mp4(tmp_path, monkeypatch):
    monkeypatch.setattr(task_routes, "_store", TaskStore(tmp_path / "tasks.sqlite", tmp_path))
    formats = [{"id": "hd", "height": 1080}, {"id": "sd", "height": 720}]
    assert main.resolve_requested_format("best", formats) == "hd"
    assert main.resolve_requested_format("best", []) is None
    res = client.post("/api/tasks", json={
        "url": "https://www.youtube.com/watch?v=abcdefghijk",
        "kind": "video", "format_id": "best",
    })
    assert res.status_code == 202
    task = task_routes._store.get(res.json()["id"])
    assert task["platform"] == "youtube"
    assert task["format_id"] == "best"
