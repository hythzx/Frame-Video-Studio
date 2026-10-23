"""Personal X / Twitter video extractor. Anonymous by default; optional login via TWITTER_COOKIES."""
import asyncio
import http.cookiejar
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

STATUS = re.compile(r"^/(?:[^/]+/status|i/(?:web/)?status)/(\d+)(?:/.*)?$", re.I)
ALLOWED_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com",
                 "mobile.twitter.com", "mobile.x.com"}
MAX_DURATION = int(os.getenv("MAX_DURATION_SECONDS", "3600"))
MAX_BYTES = int(os.getenv("MAX_DOWNLOAD_MB", "1024")) * 1024 * 1024


class VideoRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)


class DownloadRequest(VideoRequest):
    format_id: str = Field(min_length=1, max_length=120)


def canonical_url(value: str) -> str:
    try:
        parts = urlsplit(value.strip())
        if parts.scheme != "https" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
            raise ValueError()
        if parts.username or parts.password or parts.port:
            raise ValueError()
        match = STATUS.fullmatch(parts.path.rstrip("/"))
        if not match:
            raise ValueError()
        return f"https://x.com/i/status/{match.group(1)}"
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, "请输入有效的公开 X/Twitter 帖子 HTTPS 链接。") from exc


def _twitter_cookie_jar() -> http.cookiejar.CookieJar | None:
    """从 TWITTER_COOKIES（格式：name=value; name=value）构造 X 登录凭据。

    登录态可使受保护/敏感内容的帖子可解析；未配置时保持匿名访问。
    """
    raw = os.getenv("TWITTER_COOKIES", "").strip()
    if not raw:
        return None
    jar = http.cookiejar.CookieJar()
    # X 的接口同时分布在 x.com 与 twitter.com，两个域都要携带。
    for domain in (".x.com", ".twitter.com"):
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


def _apply_twitter_cookies(ydl) -> None:
    """把 TWITTER_COOKIES 注入 YoutubeDL 的全局 cookiejar。

    新版 yt-dlp 已不支持 cookiejar 选项，只能在构造后写入其全局 jar。
    """
    jar = _twitter_cookie_jar()
    if jar is None:
        return
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
            _apply_twitter_cookies(ydl)
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


def choices(data: dict) -> list:
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


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/extract")
async def inspect_video(request: VideoRequest):
    url = canonical_url(request.url)
    data = await asyncio.to_thread(extract, url)
    formats = choices(data)
    if not formats:
        raise HTTPException(422, "未发现带音轨的直链 MP4 视频；此帖子可能仅提供 HLS 或受访问限制。")
    return {
        "id": str(data.get("id", "")),
        "title": data.get("title") or "Untitled video",
        "description": (data.get("description") or "")[:400],
        "thumbnail": data.get("thumbnail"),
        "duration": data.get("duration"),
        "uploader": data.get("uploader") or data.get("channel") or "",
        "url": url,
        "formats": formats,
    }


def download_video(url: str, format_id: str, folder: Path) -> Path:
    # Re-extract server-side: never accept a user-supplied CDN address.
    data = extract(url)
    permitted = {x["id"] for x in choices(data)}
    if format_id not in permitted:
        raise ValueError("该画质已不可用，请重新解析。")
    opts = {
        "quiet": True, "no_warnings": True, "noplaylist": True,
        "format": format_id, "outtmpl": str(folder / "video.%(ext)s"),
        "max_filesize": MAX_BYTES, "socket_timeout": 15,
        "retries": 2, "continuedl": False, "nopart": True,
    }
    with YoutubeDL(opts) as ydl:
        _apply_twitter_cookies(ydl)
        ydl.download([url])
    output = next(folder.glob("video.mp4"), None)
    if not output or not output.is_file():
        raise ValueError("没有生成可下载的视频文件。")
    if output.stat().st_size > MAX_BYTES:
        raise ValueError("文件超出大小限制。")
    return output


@app.post("/api/download")
async def download(request: DownloadRequest):
    url = canonical_url(request.url)
    folder = Path(tempfile.mkdtemp(prefix="xvideo-"))
    try:
        output = await asyncio.to_thread(download_video, url, request.format_id, folder)
        return FileResponse(
            output, media_type="video/mp4",
            filename=f"x-video-{url.rsplit('/', 1)[-1]}.mp4",
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
        )
    except (DownloadError, ValueError) as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, f"下载失败：{str(exc)[:260]}") from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
