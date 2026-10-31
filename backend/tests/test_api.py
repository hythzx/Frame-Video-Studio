from fastapi.testclient import TestClient
from fastapi import HTTPException
from app import bookmarks, main

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
    assert main.canonical_url("https://twitter.com/alice/status/123456?s=20") == ("https://x.com/i/status/123456", "x")
    assert main.canonical_url("https://x.com/i/status/456") == ("https://x.com/i/status/456", "x")
    assert main.canonical_url("https://x.com/i/web/status/789") == ("https://x.com/i/status/789", "x")

def test_canonical_url_multi_platform():
    assert main.canonical_url("https://www.youtube.com/watch?v=abc123&s=1")[1] == "youtube"
    assert main.canonical_url("https://youtu.be/abc123")[1] == "youtube"
    assert main.canonical_url("https://www.youtube.com/shorts/abc123")[1] == "youtube"
    assert main.canonical_url("https://www.bilibili.com/video/BV1GJ411x7h7")[1] == "bilibili"
    assert main.canonical_url("https://b23.tv/ab12cd3")[1] == "bilibili"

def test_rejects_multi_platform_bad_urls():
    for url in [
        "http://youtube.com/watch?v=abc",
        "https://youtube.evil.example/watch?v=abc",
        "https://user:pass@bilibili.com/video/BV1x",
        "https://bilibili.com:8443/video/BV1x",
    ]:
        response = client.post("/api/extract", json={"url": url})
        assert response.status_code == 422

def test_formats_allows_unreported_codecs():
    # X 不再为渐进式直链上报编解码信息：vcodec/acodec 为 None 表示未知而非无流。
    formats = main.choices({"formats": [
        {"format_id": "http-2176", "ext": "mp4", "vcodec": None, "acodec": None,
         "protocol": "https", "height": 1280, "width": 720},
        {"format_id": "videoonly", "ext": "mp4", "vcodec": "avc1", "acodec": "none",
         "protocol": "https", "height": 720},
    ]}, "x")
    assert [x["id"] for x in formats] == ["http-2176"]

def test_dash_choices_pairs_video_with_best_audio():
    # B 站/YouTube 均为 DASH 分离流：avc1 视频流配最高码率音频，ffmpeg 合并。
    formats = main.choices({"formats": [
        {"format_id": "30280", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "protocol": "https", "tbr": 204},
        {"format_id": "30232", "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2",
         "protocol": "https", "tbr": 103},
        {"format_id": "30016", "ext": "mp4", "vcodec": "avc1.64001E", "acodec": "none",
         "protocol": "https", "height": 360, "width": 640, "tbr": 350},
        {"format_id": "100024", "ext": "mp4", "vcodec": "av01.0.08M.08", "acodec": "none",
         "protocol": "https", "height": 720, "width": 1280, "tbr": 292},
        {"format_id": "30064", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
         "protocol": "https", "height": 720, "width": 1280, "tbr": 1753},
        {"format_id": "30080", "ext": "mp4", "vcodec": "avc1.640032", "acodec": "none",
         "protocol": "https", "height": 1080, "width": 1920, "tbr": 2629},
        {"format_id": "m3u8dup", "ext": "mp4", "vcodec": "avc1.640028", "acodec": "none",
         "protocol": "m3u8_native", "height": 720, "tbr": 3000},
    ]}, "bilibili")
    assert [f["id"] for f in formats] == ["30080+30280", "30064+30280", "30016+30280"]
    assert formats[0]["label"] == "1080p"

def test_formats_only_progressive_mp4():
    formats = main.choices({"formats": [
        {"format_id": "720", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "protocol": "https", "height": 720, "width": 1280},
        {"format_id": "audio", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "protocol": "https"},
        {"format_id": "hls", "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a", "protocol": "m3u8_native"},
        {"format_id": "videoonly", "ext": "mp4", "vcodec": "avc1", "acodec": "none", "protocol": "https"},
    ]}, "x")
    assert [x["id"] for x in formats] == ["720", "hls"]

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
    monkeypatch.setenv("BILI_COOKIES", "SESSDATA=xyz; bili_jct=tok")
    from yt_dlp import YoutubeDL
    with YoutubeDL({"quiet": True}) as ydl:
        main._apply_cookies(ydl)
        injected = {c.name: c.value for c in ydl.cookiejar}
    assert injected.get("auth_token") == "abc"
    assert injected.get("ct0") == "def"
    assert injected.get("SESSDATA") == "xyz"
    assert injected.get("bili_jct") == "tok"

def test_prefer_official_cdn_replaces_mcdn():
    # B 站 playurl 的 base_url 常指向 P2P(mcdn) 节点，部分网络不可达；
    # 应替换为 backupUrl 中的官方 CDN，无可用 backup 时保持原样。
    data = {"data": {"dash": {"video": [
        {"id": 30064,
         "baseUrl": "https://xy1x2x3x4xy.mcdn.bilivideo.cn:8082/v/a.m4s?sig=1",
         "backupUrl": ["https://edge.thirdparty.example:4483/v/a.m4s",
                        "https://upos-sz-mirrorcoso1.bilivideo.com/v/a.m4s"]}],
        "audio": [
        {"id": 30280, "baseUrl": "https://xy5x6x7x8xy.mcdn.bilivideo.cn:8082/a.m4s",
         "backupUrl": []}]}}}
    main._prefer_official_cdn(data)
    v = data["data"]["dash"]["video"][0]
    a = data["data"]["dash"]["audio"][0]
    assert "mirrorcoso1.bilivideo.com" in v["baseUrl"]
    assert "mcdn" in a["baseUrl"]

def test_is_playurl_api_by_path():
    # 只按路径判断；下载直链的 query 含 gen=playurlv3，子串匹配会误伤 m4s 下载。
    assert main._is_playurl_api("https://api.bilibili.com/x/player/wbi/playurl?bvid=1&cid=2")
    assert main._is_playurl_api("https://api.bilibili.com/x/player/playurl?bvid=1")
    assert not main._is_playurl_api(
        "https://upos-sz-mirrorcoso1.bilivideo.com/upgcxcode/99/91/a.m4s?gen=playurlv3&os=coso1bv")

def test_rewrite_playinfo_html():
    # 登录态下 yt-dlp 直接使用网页内嵌的 __playinfo__（不请求 API），需在 HTML 层改写。
    html = ('<script>window.__playinfo__={"code":0,"data":{"dash":{"video":['
            '{"id":30064,"baseUrl":"https://xy1x2x3x4xy.mcdn.bilivideo.cn:8082/v/a.m4s",'
            '"backupUrl":["https://upos-sz-mirrorcoso1.bilivideo.com/v/a.m4s"]}]}}}</script>')
    out = main._rewrite_playinfo_html(html)
    assert "mcdn" not in out
    assert "mirrorcoso1.bilivideo.com" in out

def test_extract_with_fake_data(monkeypatch):
    monkeypatch.setattr(main, "extract", lambda url: {
        "id": "123", "title": "Test Video", "duration": 20, "thumbnail": None,
        "formats": [{"format_id": "one", "ext": "mp4", "vcodec": "h264", "acodec": "aac", "protocol": "https", "height": 360}],
    })
    response = client.post("/api/extract", json={"url": "https://x.com/me/status/123"})
    assert response.status_code == 200
    assert response.json()["formats"][0]["id"] == "one"

def test_download_rejects_bad_url():
    response = client.post("/api/download", json={"url": "https://127.0.0.1/private", "format_id": "bad"})
    assert response.status_code == 422


def test_subtitles_and_audio_metadata(monkeypatch):
    monkeypatch.setattr(main, "extract", lambda url: {
        "id": "abc", "title": "Test", "duration": 40,
        "formats": [{"format_id": "140", "acodec": "mp4a.40.2", "vcodec": "none", "protocol": "https"}],
        "subtitles": {"zh-Hans": [{"ext": "vtt", "url": "https://example.com/test.vtt"}]},
        "automatic_captions": {"en": [{"ext": "vtt", "url": "https://example.com/auto.vtt"}]},
    })
    result = client.post("/api/extract", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert result.status_code == 200
    assert result.json()["audio_available"] is True
    assert result.json()["subtitles"] == [
        {"language": "zh-Hans", "automatic": False},
        {"language": "en", "automatic": True},
    ]


def test_audio_and_subtitle_validation():
    url = "https://www.youtube.com/watch?v=abc"
    assert client.post("/api/audio", json={"url": url, "audio_format": "exe"}).status_code == 422
    assert client.post("/api/subtitle", json={
        "url": url, "language": "../en", "subtitle_format": "srt"
    }).status_code == 422
    assert client.post("/api/subtitle", json={
        "url": url, "language": "en", "subtitle_format": "pdf"
    }).status_code == 422


def test_unavailable_subtitle_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract", lambda url: {"subtitles": {}, "automatic_captions": {}})
    import pytest
    with pytest.raises(ValueError, match="字幕轨道不可用"):
        main._run_subtitle("https://x.com/i/status/123", "en", "srt", False, tmp_path)


def test_unavailable_audio_rejected(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "extract", lambda url: {"formats": []})
    import pytest
    with pytest.raises(ValueError, match="音轨"):
        main._run_audio("https://x.com/i/status/123", "mp3", tmp_path)


def test_parse_bookmarks_response():
    sample = {
        "data": {"bookmark_timeline_v2": {"timeline": {"instructions": [
            {"type": "TimelineAddEntries", "entries": [
                {"entryId": "tweet-111111111111111111", "content": {"entryType": "TimelineTimelineItem",
                 "itemContent": {"tweet_results": {"result": {
                     "legacy": {"full_text": "hello video", "extended_entities": {"media": [
                         {"type": "video", "media_url": "https://pbs.twimg.com/cover.jpg"}]}},
                     "core": {"user_results": {"result": {"legacy": {
                         "screen_name": "alice", "name": "Alice"}}}},
                     "rest_id": "111111111111111111"}}}}},
                {"entryId": "tweet-222", "content": {"entryType": "TimelineTimelineItem",
                 "itemContent": {"tweet_results": {"result": {
                     "legacy": {"full_text": "text only"},
                     "core": {"user_results": {"result": {"legacy": {
                         "screen_name": "bob", "name": "Bob"}}}},
                     "rest_id": "222"}}}}},
                {"entryId": "cursor-bottom-333", "content": {"entryType": "TimelineTimelineCursor",
                 "value": "next-cursor-value"}},
            ]},
        ]}}}}
    items, cursor = bookmarks._parse_bookmarks_response(sample)
    assert cursor == "next-cursor-value"
    assert [i["id"] for i in items] == ["111111111111111111"]
    assert items[0]["has_video"] and items[0]["username"] == "alice"
    assert items[0]["thumbnail"] == "https://pbs.twimg.com/cover.jpg"


def test_resolve_format_fallback_and_exact():
    formats = [
        {"id": "248-sr+140-drc", "height": 1080},
        {"id": "136+140", "height": 720},
        {"id": "135+140", "height": 480},
    ]
    assert main.resolve_requested_format("136+140", formats) == "136+140"
    # 请求的 720p 已不存在：降级到不超过请求分辨率的最近画质。
    assert main.resolve_requested_format("999+140", formats) == "248-sr+140-drc"
    assert main.resolve_requested_format("gone+gone", formats) is None


def test_generic_https_sites_are_accepted():
    assert main.canonical_url("https://vimeo.com/123456") == ("https://vimeo.com/123456", "other")
    assert main.canonical_url("https://www.tiktok.com/@demo/video/123")[1] == "other"
    assert main.canonical_url("https://www.instagram.com/reel/ABC/")[1] == "other"


def test_generic_media_choices_support_mp4_dash_and_hls():
    sample = {"formats": [
        {"format_id": "direct", "ext": "mp4", "protocol": "https",
         "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 720},
        {"format_id": "hls", "ext": "mp4", "protocol": "m3u8_native",
         "vcodec": "avc1", "acodec": "mp4a.40.2", "height": 1080},
        {"format_id": "video", "ext": "mp4", "protocol": "https",
         "vcodec": "avc1", "acodec": "none", "height": 1440},
        {"format_id": "audio", "ext": "m4a", "protocol": "https",
         "vcodec": "none", "acodec": "mp4a.40.2"},
    ]}
    selected = main.choices(sample, "other")
    assert [x["id"] for x in selected] == ["video+audio", "hls", "direct"]
    assert [x["source"] for x in selected] == ["dash", "hls", "mp4"]


def test_generic_extract_response_uses_extractor_name(monkeypatch):
    monkeypatch.setattr(main, "extract", lambda url: {
        "id": "abc", "extractor_key": "Vimeo", "title": "Clip",
        "duration": 10, "thumbnail": None,
        "formats": [{"format_id": "stream", "ext": "mp4",
                     "vcodec": "avc1", "acodec": "aac",
                     "protocol": "https", "height": 480}],
    })
    result = client.post("/api/extract", json={"url": "https://vimeo.com/123456"})
    assert result.status_code == 200
    assert result.json()["platform"] == "other"
    assert result.json()["platform_name"] == "Vimeo"
    assert result.json()["formats"][0]["source"] == "mp4"


def test_public_network_url_rejects_unsafe_hosts(monkeypatch):
    from app.security import validate_media_url
    import pytest
    invalid = (
        "http://vimeo.com/123", "https://127.0.0.1/", "https://[::1]/",
        "https://localhost/video", "https://api.internal/video",
        "https://169.254.169.254/latest/meta-data/",
        "https://user:pass@vimeo.com/1", "file:///etc/passwd",
        "https://metadata.google.internal/computeMetadata/v1/",
        "https://0x7f000001/",
    )
    for url in invalid:
        with pytest.raises(ValueError):
            validate_media_url(url)
    assert validate_media_url("https://vimeo.com/123") == "https://vimeo.com/123"


def test_public_network_url_rejects_private_dns(monkeypatch):
    import socket
    import pytest
    from app.security import validate_media_url

    def fake_resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.50", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolve)
    with pytest.raises(ValueError, match="公网"):
        validate_media_url("https://videos.example.net/watch/1", resolve=True)


def test_public_network_url_accepts_public_dns(monkeypatch):
    import socket
    from app.security import validate_media_url

    def fake_resolve(host, port, type=None):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))]
    monkeypatch.setattr(socket, "getaddrinfo", fake_resolve)
    assert validate_media_url("https://videos.example.net/watch/1", resolve=True)


def test_generic_task_accepts_other_platform(monkeypatch, tmp_path):
    from app.tasks import routes
    from app.tasks.store import TaskStore
    store = TaskStore(tmp_path / "tasks.sqlite", tmp_path)
    monkeypatch.setattr(routes, "get_store", lambda: store)
    response = client.post("/api/tasks", json={
        "url": "https://vimeo.com/123456", "kind": "video", "format_id": "stream"
    })
    assert response.status_code == 202
    assert store.get(response.json()["id"])["platform"] == "other"
