from fastapi.testclient import TestClient
from fastapi import HTTPException
from app import main

client = TestClient(main.app)

def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}

def test_reject_invalid_urls():
    for url in [
        "http://x.com/user/status/123",
        "https://evil.example/user/status/123",
        "https://x.com.evil.example/user/status/123",
        "https://x.com/user/status/not-a-number",
        "https://x.com:444/user/status/123",
        "https://user:pass@x.com/user/status/123",
    ]:
        response = client.post("/api/extract", json={"url": url})
        assert response.status_code == 422

def test_normalizes_supported_post_urls():
    assert main.canonical_url("https://twitter.com/alice/status/123456?s=20") == "https://x.com/i/status/123456"
    assert main.canonical_url("https://x.com/i/status/456") == "https://x.com/i/status/456"
    assert main.canonical_url("https://x.com/i/web/status/789") == "https://x.com/i/status/789"

def test_formats_allows_unreported_codecs():
    # X 不再为渐进式直链上报编解码信息：vcodec/acodec 为 None 表示未知而非无流。
    formats = main.choices({"formats": [
        {"format_id": "http-2176", "ext": "mp4", "vcodec": None, "acodec": None,
         "protocol": "https", "height": 1280, "width": 720},
        {"format_id": "videoonly", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "protocol": "https", "height": 720},
    ]})
    assert [x["id"] for x in formats] == ["http-2176"]

def test_formats_only_progressive_mp4():
    formats = main.choices({"formats": [
        {"format_id": "720", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "protocol": "https", "height": 720, "width": 1280},
        {"format_id": "audio", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "protocol": "https"},
        {"format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "protocol": "m3u8_native"},
        {"format_id": "videoonly", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "protocol": "https"},
    ]})
    assert [x["id"] for x in formats] == ["720"]

def test_cookie_jar_from_env(monkeypatch):
    monkeypatch.setenv("TWITTER_COOKIES", "auth_token=abc; ct0=def")
    jar = main._twitter_cookie_jar()
    cookies = {c.name: c.value for c in jar}
    assert cookies == {"auth_token": "abc", "ct0": "def"}
    # X 的接口分布在 x.com 与 twitter.com，两个域都要携带。
    assert {c.domain for c in jar} == {".x.com", ".twitter.com"}
    assert all(c.secure for c in jar)

def test_cookie_jar_unset(monkeypatch):
    monkeypatch.delenv("TWITTER_COOKIES", raising=False)
    assert main._twitter_cookie_jar() is None

def test_cookies_applied_to_ydl(monkeypatch):
    # 新版 yt-dlp 不再支持 cookiejar 选项，必须在构造后注入其全局 jar。
    monkeypatch.setenv("TWITTER_COOKIES", "auth_token=abc; ct0=def")
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True}) as ydl:
        main._apply_twitter_cookies(ydl)
        injected = {c.name: c.value for c in ydl.cookiejar}
    assert injected.get("auth_token") == "abc"
    assert injected.get("ct0") == "def"

def test_extract_with_fake_data(monkeypatch):
    monkeypatch.setattr(main, "extract", lambda url: {
        "id": "123", "title": "Test Video", "duration": 20, "thumbnail": None,
        "formats": [{"format_id": "one", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "https", "height": 360}],
    })
    response = client.post("/api/extract", json={"url": "https://x.com/me/status/123"})
    assert response.status_code == 200
    assert response.json()["formats"][0]["id"] == "one"

def test_download_rejects_bad_url():
    response = client.post("/api/download", json={"url": "https://example.com/", "format_id": "bad"})
    assert response.status_code == 422
