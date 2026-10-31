"""Multi-platform video extractor (X/Twitter, YouTube, Bilibili). Optional login cookies per platform."""
import asyncio
import http.cookiejar
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

app = FastAPI(title="X Video Studio API", docs_url=None, redoc_url=None)


def _prefer_official_cdn(data: dict) -> None:
    """把 B 站 playurl 响应中 P2P(mcdn) 的 base_url 替换为 backupUrl 里的官方 CDN。

    mcdn 节点在部分运营商网络不可达；backupUrl 为同一资源的官方 CDN 地址。
    无可用 backup 时保持原样，失败模式安全。
    """
    dash = ((data.get("data") or {}).get("dash")) or data.get("dash") or {}
    for streams in (dash.get("video"), dash.get("audio")):
        for stream in streams or []:
            base = stream.get("baseUrl") or stream.get("base_url") or ""
            if "mcdn" not in base:
                continue
            candidates = [u for u in (stream.get("backupUrl") or stream.get("backup_url") or [])
                          if "mcdn" not in u]
            candidates.sort(key=lambda u: "bilivideo.com" not in u)
            if candidates:
                stream["baseUrl"] = candidates[0]


def _is_playurl_api(url: str) -> bool:
    """按路径精确判断是否 B 站 playurl 接口；下载直链的 query 含 gen=playurlv3，不可用子串匹配。"""
    return urlsplit(url).path.endswith("/playurl")


def _rewrite_playinfo_html(html: str) -> str:
    """改写网页内嵌 __playinfo__ 中的 mcdn CDN 地址；无变化时原样返回。"""
    match = re.search(r"(window\.__playinfo__\s*=\s*)(\{.*?\})\s*</script>", html, re.S)
    if not match:
        return html
    try:
        data = json.loads(match.group(2))
    except ValueError:
        return html
    _prefer_official_cdn(data)
    rewritten = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    if rewritten == match.group(2):
        return html
    return html[:match.start(2)] + rewritten + html[match.end(2):]


def _install_bili_cdn_patch() -> None:
    """拦截 B 站播放数据响应，把 P2P(mcdn) 的 CDN 替换为官方备用 CDN。

    登录态下播放数据内嵌在视频页 HTML（window.__playinfo__），匿名时走
    /playurl 接口，故在 YoutubeDL.urlopen（所有请求的必经之路）按响应改写。
    无 mcdn 或结构不符时原样返回，失败模式安全。
    """

    class _RereadResponse:
        """替换 payload 后的只读响应包装，透传原响应的其余属性。"""

        def __init__(self, resp, payload: bytes):
            self._resp = resp
            self._payload = payload
            self._pos = 0

        def read(self, n=-1):
            remaining = self._payload[self._pos:]
            if n == -1:
                chunk = remaining
            else:
                chunk = remaining[:n]
            self._pos += len(chunk)
            return chunk

        def __getattr__(self, name):
            return getattr(self._resp, name)

        def close(self):
            self._resp.close()

    orig_urlopen = YoutubeDL.urlopen

    def patched_urlopen(self, req, *args, **kwargs):
        resp = orig_urlopen(self, req, *args, **kwargs)
        try:
            url = getattr(req, "url", "") or ""
            # 登录态下播放数据内嵌在视频页 HTML（window.__playinfo__）。
            if "bilibili.com/video/" in url:
                text = resp.read().decode("utf-8", "replace")
                new_text = _rewrite_playinfo_html(text)
                if new_text != text:
                    return _RereadResponse(resp, new_text.encode())
                return resp
            if not _is_playurl_api(url):
                return resp
            data = json.loads(resp.read())
            _prefer_official_cdn(data)
            dash = ((data.get("data") or {}).get("dash")) or data.get("dash")
            if dash:
                return _RereadResponse(resp, json.dumps(data).encode())
            return resp
        except Exception:
            return resp

    YoutubeDL.urlopen = patched_urlopen


_install_bili_cdn_patch()

STATUS = re.compile(r"^/(?:[^/]+/status|i/(?:web/)?status)/(\d+)(?:/.*)?$", re.I)
X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com",
           "mobile.twitter.com", "mobile.x.com"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
                 "music.youtube.com", "youtu.be", "www.youtu.be"}
BILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
MAX_DURATION = int(os.getenv("MAX_DURATION_SECONDS", "3600"))
MAX_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "1024")) * 1024 * 1024


class VideoRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)


class DownloadRequest(VideoRequest):
    format_id: str = Field(min_length=1, max_length=120)


class AudioRequest(VideoRequest):
    audio_format: str = Field(pattern=r"^(mp3|m4a)$")


class SubtitleRequest(VideoRequest):
    language: str = Field(min_length=1, max_length=40)
    subtitle_format: str = Field(pattern=r"^(vtt|srt)$")
    automatic: bool = False


def canonical_url(value: str) -> tuple[str, str]:
    """校验并规范化输入链接，返回 (规范化 URL, 平台标识)。"""
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host:
            raise ValueError()
        if parts.username or parts.password or parts.port:
            raise ValueError()
        if host in X_HOSTS:
            match = STATUS.fullmatch(parts.path.rstrip("/"))
            if not match:
                raise ValueError()
            return f"https://x.com/i/status/{match.group(1)}", "x"
        if host in YOUTUBE_HOSTS:
            if not parts.path.strip("/"):
                raise ValueError()
            return value.strip(), "youtube"
        if host in BILI_HOSTS:
            if not parts.path.strip("/"):
                raise ValueError()
            return value.strip(), "bilibili"
        raise ValueError()
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "请输入有效的 X/Twitter、YouTube 或 B 站公开视频 HTTPS 链接。") from exc


X_COOKIE_DOMAINS = (".x.com", ".twitter.com")
BILI_COOKIE_DOMAINS = (".bilibili.com",)


def _cookie_jar_from_env(env_name: str, domains: tuple) -> http.cookiejar.CookieJar | None:
    """从环境变量（格式：name=value; name=value）构造登录 cookie。

    登录态可解锁受限内容与更优 CDN；未配置时保持匿名访问。
    """
    raw = os.getenv(env_name, "").strip()
    if not raw:
        return None
    jar = http.cookiejar.CookieJar()
    for domain in domains:
        for pair in raw.split(";"):
            name, sep, value = pair.strip().partition("=")
            if not sep or not name or not value:
                continue
            jar.set_cookie(http.cookiejar.Cookie(
                version=0, name=name, value=value, port=None, port_specified=False,
                domain=domain, domain_specified=True, domain_initial_dot=True,
                path="/", path_specified=True, secure=True, expires=None,
                discard=True, comment=None, comment_url=None, rest={},
            ))
    return jar if jar else None


def _twitter_cookie_jar() -> http.cookiejar.CookieJar | None:
    return _cookie_jar_from_env("TWITTER_COOKIES", X_COOKIE_DOMAINS)


def _apply_cookies(ydl) -> None:
    """把各平台登录 cookie 注入 YoutubeDL 的全局 cookiejar。

    新版 yt-dlp 已不支持 cookiejar 选项，只能在构造后写入其全局 jar。
    """
    for jar in (_twitter_cookie_jar(),
                _cookie_jar_from_env("BILI_COOKIES", BILI_COOKIE_DOMAINS)):
        if jar is None:
            continue
        for cookie in jar:
            ydl.cookiejar.set_cookie(cookie)


def extract(url: str) -> dict:
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "skip_download": True, "socket_timeout": 15,
        "extractor_retries": 2, "retries": 2,
        "ignoreerrors": False,
    }
    try:
        with YoutubeDL(opts) as ydl:
            _apply_cookies(ydl)
            data = ydl.extract_info(url, download=False)
        if not data:
            raise ValueError("未找到可下载的视频。")
        if data.get("_type") == "playlist":
            entries = [e for e in data.get("entries", []) if e]
            if not entries:
                raise ValueError("未找到可下载的视频。")
            data = entries[0]
        duration = data.get("duration")
        if duration and duration > MAX_DURATION:
            raise ValueError("视频时长超过服务限制。")
        return data
    except (DownloadError, ValueError) as exc:
        raise HTTPException(422, f"解析失败：{str(exc)[:260]}") from exc


def _twitter_choices(data: dict) -> list:
    result = []
    seen = set()
    for f in data.get("formats") or []:
        # Progressive MP4: contains audio + video and can be downloaded without merging.
        # X 不再为渐进式直链上报编解码信息：None 表示未知而非无流，仅拒绝明确的 "none"。
        if (f.get("ext") != "mp4" or
            f.get("vcodec") == "none" or
            f.get("acodec") == "none" or
            f.get("protocol") not in ("https", "http")):
            continue
        fid = str(f.get("format_id", ""))
        if not fid or fid in seen:
            continue
        seen.add(fid)
        size = f.get("filesize") or f.get("filesize_approx")
        result.append({
            "id": fid,
            "width": f.get("width"),
            "height": f.get("height"),
            "bitrate": f.get("tbr"),
            "size": size,
            "label": f"{f.get('height') or '?'}p",
        })
    result.sort(key=lambda f: (f["height"] or 0, f["bitrate"] or 0), reverse=True)
    return result


def _dash_choices(data: dict) -> list:
    """DASH 分离流平台（YouTube / B 站）：avc1 视频流配最高码率音频，ffmpeg 合并输出 MP4。"""
    formats = data.get("formats") or []
    videos = {}
    for f in formats:
        if (f.get("vcodec") in (None, "none") or
            f.get("acodec") != "none" or
            f.get("protocol") != "https" or
            not f.get("height")):
            continue
        # 同分辨率优先 avc1（保证合并后为 MP4 容器），其次取码率更高者。
        key = (str(f.get("vcodec", "")).startswith("avc1"), f.get("tbr") or 0)
        if f["height"] not in videos or key > videos[f["height"]][0]:
            videos[f["height"]] = (key, f)
    audios = [f for f in formats
              if f.get("acodec") not in (None, "none")
              and f.get("vcodec") in (None, "none")
              and f.get("protocol") == "https"]
    if not audios or not videos:
        return []
    # m4a 音频与 avc1 视频合并后 MP4 兼容性最好。
    audio = max(audios, key=lambda f: (f.get("ext") == "m4a", f.get("tbr") or 0))
    result = []
    for height, (_, v) in videos.items():
        result.append({
            "id": f"{v['format_id']}+{audio['format_id']}",
            "width": v.get("width"),
            "height": height,
            "bitrate": (v.get("tbr") or 0) + (audio.get("tbr") or 0),
            "size": v.get("filesize") or v.get("filesize_approx"),
            "label": f"{height}p",
        })
    result.sort(key=lambda f: f["height"], reverse=True)
    return result


def subtitle_choices(data: dict) -> list:
    tracks = []
    for automatic, field in ((False, "subtitles"), (True, "automatic_captions")):
        for language, formats in (data.get(field) or {}).items():
            if isinstance(formats, list) and formats:
                tracks.append({"language": language, "automatic": automatic})
    return sorted(tracks, key=lambda t: (t["automatic"], t["language"]))


def audio_available(data: dict) -> bool:
    return any(
        f.get("acodec") not in (None, "none")
        or (f.get("acodec") is None and f.get("vcodec") != "none"
            and f.get("ext") == "mp4" and f.get("protocol") in ("https", "http"))
        for f in (data.get("formats") or [])
    )


def choices(data: dict, platform: str) -> list:
    if platform in ("youtube", "bilibili"):
        return _dash_choices(data)
    return _twitter_choices(data)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/extract")
async def inspect_video(request: VideoRequest):
    url, platform = canonical_url(request.url)
    data = await asyncio.to_thread(extract, url)
    formats = choices(data, platform)
    subtitles = subtitle_choices(data)
    audio = audio_available(data)
    if not formats and not audio and not subtitles:
        raise HTTPException(422, "未发现可下载的视频、音频或字幕。")
    await asyncio.to_thread(_record_history, url, platform, data, formats)
    # B 站封面图 URL 常为 http://：HTTPS 页面属于混合内容，且会被浏览器拦截。
    thumbnail = data.get("thumbnail")
    if platform == "bilibili" and thumbnail and thumbnail.startswith("http://"):
        thumbnail = "https://" + thumbnail[7:]
    return {
        "id": str(data.get("id", "")),
        "title": data.get("title") or "Untitled video",
        "description": (data.get("description") or "")[:400],
        "thumbnail": thumbnail,
        "duration": data.get("duration"),
        "uploader": data.get("uploader") or data.get("channel") or "",
        "url": url,
        "platform": platform,
        "formats": formats,
        "audio_available": audio,
        "subtitles": subtitles,
    }


def resolve_requested_format(format_id: str, formats: list) -> str | None:
    """把请求的画质解析为当前提取结果中可用的格式。

    YouTube 等平台的格式 ID（含 -drc 等后缀）在多次提取之间并不稳定：
    精确匹配失败时，按请求的视频流分辨率就近降级（不超过请求值），
    而不是直接报"画质不可用"。
    """
    ids = {x["id"] for x in formats}
    if format_id in ids:
        return format_id
    parts = format_id.split("+")
    requested_video = parts[0]
    requested_audio = parts[1] if len(parts) > 1 else None
    requested_height = next(
        (f["height"] for f in formats if f["id"].split("+")[0] == requested_video and f["height"]),
        None)
    with_height = [f for f in formats if f["height"]]
    # 降级前提：请求中至少音频流仍存在；音视频流均已消失则无有意义的替代。
    audio_exists = requested_audio and any(
        f["id"].split("+")[-1] == requested_audio for f in with_height)
    if requested_height:
        # 视频流仍在：就近降级到不超过请求分辨率的最近画质。
        candidates = [f for f in with_height if f["height"] <= requested_height]
        best = max(candidates or with_height, key=lambda f: f["height"], default=None)
    elif audio_exists:
        # 视频流已不存在、无法得知请求高度：提供当前最高可用画质。
        best = max(with_height, key=lambda f: f["height"], default=None)
    else:
        best = None
    return best["id"] if best else None


def download_video(url: str, format_id: str, folder: Path, platform: str) -> Path:
    # Re-extract server-side: never accept a user-supplied CDN address.
    data = extract(url)
    formats = choices(data, platform)
    resolved = resolve_requested_format(format_id, formats)
    if not resolved:
        raise ValueError("该画质已不可用，且没有可降级的替代画质，请重新解析。")
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": resolved, "outtmpl": str(folder / "video.%(ext)s"),
        "merge_output_format": "mp4",
        "max_filesize": MAX_BYTES, "socket_timeout": 15,
        "retries": 2, "continuedl": False, "nopart": True,
    }
    with YoutubeDL(opts) as ydl:
        _apply_cookies(ydl)
        ydl.download([url])
    # 分离流经 ffmpeg 合并后输出 video.mp4；按实际产物匹配以防扩展名差异。
    output = next(folder.glob("video.mp4"), None)
    if not output or not output.is_file():
        raise ValueError("没有生成可下载的视频文件。")
    if output.stat().st_size > MAX_BYTES:
        raise ValueError("文件超出大小限制。")
    return output


@app.get("/api/history")
async def extraction_history(limit: int = 200):
    from .tasks.routes import get_store
    limit = max(1, min(limit, 200))
    return get_store().list_history(limit=limit)


@app.post("/api/download")
async def download(request: DownloadRequest):
    url, platform = canonical_url(request.url)
    folder = Path(tempfile.mkdtemp(prefix="xvideo-"))
    try:
        output = await asyncio.to_thread(
            download_video, url, request.format_id, folder, platform)
        return FileResponse(
            output, media_type="video/mp4",
            filename=f"video-{url.rsplit('/', 1)[-1]}.mp4",
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
        )
    except (DownloadError, ValueError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f"下载失败：{str(exc)[:260]}") from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


def _record_history(url: str, platform: str, data: dict, formats: list) -> None:
    """解析成功后记录到历史表；失败静默，不影响解析响应。"""
    try:
        from .tasks.routes import get_store
        get_store().record_extraction(
            platform=platform, url=url, video_id=str(data.get("id", "")),
            title=str(data.get("title") or "")[:200],
            description=(data.get("description") or "")[:400],
            thumbnail=data.get("thumbnail"),
            duration=data.get("duration"),
            uploader=str(data.get("uploader") or data.get("channel") or ""),
            formats_json=json.dumps(formats, ensure_ascii=False))
    except Exception:
        pass


def _media_options(folder: Path) -> dict:
    return {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "outtmpl": str(folder / "media.%(ext)s"),
        "max_filesize": MAX_BYTES, "socket_timeout": 15,
        "retries": 2, "nopart": True,
    }


def _run_audio(url: str, fmt: str, folder: Path) -> Path:
    if not audio_available(extract(url)):
        raise ValueError("视频没有可用音轨。")
    opts = _media_options(folder)
    opts.update({
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": fmt,
            "preferredquality": "192",
        }],
    })
    with YoutubeDL(opts) as ydl:
        _apply_cookies(ydl)
        ydl.download([url])
    output = folder / f"media.{fmt}"
    if not output.is_file():
        raise ValueError("音频转换没有生成文件。")
    if output.stat().st_size > MAX_BYTES:
        raise ValueError("音频文件超过大小限制。")
    return output


def _run_subtitle(url: str, language: str, fmt: str, automatic: bool, folder: Path) -> Path:
    tracks = subtitle_choices(extract(url))
    if not any(t["language"] == language and t["automatic"] == automatic for t in tracks):
        raise ValueError("字幕轨道不可用，请重新解析。")
    opts = _media_options(folder)
    opts.update({
        "skip_download": True,
        "writesubtitles": not automatic,
        "writeautomaticsub": automatic,
        "subtitleslangs": [language],
        "subtitlesformat": "vtt/srt/best",
        "convertsubtitles": fmt,
    })
    with YoutubeDL(opts) as ydl:
        _apply_cookies(ydl)
        ydl.download([url])
    output = next(iter(folder.glob(f"media.*.{fmt}")), None)
    if not output or not output.is_file():
        raise ValueError("字幕文件未生成，平台可能不提供此格式。")
    if output.stat().st_size > min(MAX_BYTES, 20 * 1024 * 1024):
        raise ValueError("字幕文件超过大小限制。")
    return output


async def _temporary_response(worker, request_url: str, filename: str, mime: str, *args):
    url, _ = canonical_url(request_url)
    folder = Path(tempfile.mkdtemp(prefix="frame-media-"))
    try:
        output = await asyncio.to_thread(worker, url, *args, folder)
        return FileResponse(output, filename=filename, media_type=mime,
                            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))
    except (DownloadError, ValueError, HTTPException) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(422, f"处理失败：{str(exc)[:260]}") from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise


@app.post("/api/audio")
async def download_audio(request: AudioRequest):
    return await _temporary_response(
        _run_audio, request.url, f"audio.{request.audio_format}",
        "audio/mpeg" if request.audio_format == "mp3" else "audio/mp4",
        request.audio_format)


@app.post("/api/subtitle")
async def download_subtitle(request: SubtitleRequest):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", request.language):
        raise HTTPException(422, "字幕语言代码无效。")
    return await _temporary_response(
        _run_subtitle, request.url, f"subtitle.{request.subtitle_format}",
        "text/vtt" if request.subtitle_format == "vtt" else "application/x-subrip",
        request.language, request.subtitle_format, request.automatic)


from .bookmarks import router as bookmarks_router
app.include_router(bookmarks_router)

from .tasks.routes import router as tasks_router
app.include_router(tasks_router)
