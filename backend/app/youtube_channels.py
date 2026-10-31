"""Paginated YouTube channel library — metadata only, no media download.

The public channel browser uses yt-dlp's flat playlist extractor. Each request
has a bounded window, so even a channel with thousands of uploads is browsable.
"""
import asyncio
import re
from itertools import islice
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from .security import validate_media_url

router = APIRouter(prefix="/api/youtube/channels", tags=["youtube-channels"])

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com"}
_CHANNEL_PATH = re.compile(
    r"/(?P<identity>@[^/\s?#]{1,100}|channel/UC[A-Za-z0-9_-]{22}|"
    r"c/[A-Za-z0-9_.-]{1,100}|user/[A-Za-z0-9_.-]{1,100})"
    r"(?:/(?:videos|shorts|streams))?/?\Z"
)
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_SECTIONS = {"videos", "shorts", "streams"}
_PAGE_SIZE = 24
_TITLE_LANGUAGES = {"zh-CN", "zh-TW", "en", "original"}


def canonical_channel_url(value: str, section: str = "videos") -> str:
    if section not in _SECTIONS:
        raise ValueError("不支持的频道栏目")
    url = validate_media_url(value, allow_http=False)
    parts = urlsplit(url)
    if parts.hostname not in _YOUTUBE_HOSTS or parts.port is not None:
        raise ValueError("请粘贴 YouTube 频道主页，不是单条视频链接")
    match = _CHANNEL_PATH.fullmatch(parts.path)
    if not match:
        raise ValueError("支持 @用户名、/channel/UC...、/c/... 或 /user/... 频道地址")
    return f"https://www.youtube.com/{match.group('identity')}/{section}"


def _video_id(info: dict) -> str | None:
    candidate = str(info.get("id") or "")
    if _VIDEO_ID.fullmatch(candidate):
        return candidate
    url = str(info.get("url") or "")
    if _VIDEO_ID.fullmatch(url):
        return url
    parts = urlsplit(url)
    if parts.hostname in ("youtube.com", "www.youtube.com", "youtu.be"):
        if parts.hostname == "youtu.be":
            candidate = parts.path.strip("/")
        else:
            from urllib.parse import parse_qs
            candidate = (parse_qs(parts.query).get("v") or [""])[0]
        if _VIDEO_ID.fullmatch(candidate):
            return candidate
    return None


def _channel_page(channel_url: str, start: int, language: str = "zh-CN", count: int = _PAGE_SIZE) -> dict:
    # All entries are flat: do not analyze formats or download videos during listing.
    if language not in _TITLE_LANGUAGES:
        raise ValueError("不支持的标题语言")
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist", "noplaylist": False,
        "playliststart": start, "playlistend": start + count,
        "ignoreerrors": True, "socket_timeout": 15,
        "retries": 1, "extractor_retries": 1,
    }
    # Only the listing metadata requests use this preference. Download URLs
    # and video IDs never change. "original" preserves yt-dlp defaults.
    if language != "original":
        opts["extractor_args"] = {"youtube": {"lang": [language]}}
    try:
        from .main import _apply_cookies
        with YoutubeDL(opts) as ydl:
            _apply_cookies(ydl)
            result = ydl.extract_info(channel_url, download=False)
        if not result or not result.get("entries"):
            return {"channel_title": "", "items": [], "next_start": None}
        entries = list(islice(iter(result["entries"]), count + 1))
        has_next = len(entries) > count
        items = []
        for entry in entries[:count]:
            if not isinstance(entry, dict):
                continue
            vid = _video_id(entry)
            if not vid:
                continue
            thumbnail = entry.get("thumbnail")
            if not thumbnail and entry.get("thumbnails"):
                thumbnail = (entry["thumbnails"][-1] or {}).get("url")
            items.append({
                "id": vid,
                "url": f"https://www.youtube.com/watch?v={vid}",
                "title": str(entry.get("title") or "未命名作品")[:240],
                "duration": entry.get("duration"),
                "upload_date": entry.get("upload_date"),
                "thumbnail": thumbnail if isinstance(thumbnail, str) and thumbnail.startswith("https://") else None,
                "view_count": entry.get("view_count"),
            })
        return {
            "channel_title": str(result.get("channel") or result.get("title") or "")[:160],
            "items": items,
            "next_start": start + count if has_next else None,
        }
    except (DownloadError, ValueError) as exc:
        raise HTTPException(422, "频道读取失败：YouTube 可能限制访问，或 yt-dlp 需要更新。") from exc


@router.get("")
async def list_channel(
    url: str = Query(min_length=15, max_length=2048),
    section: str = Query(default="videos", pattern="^(videos|shorts|streams)$"),
    lang: str = Query(default="zh-CN", pattern="^(zh-CN|zh-TW|en|original)$"),
    start: int = Query(default=1, ge=1, le=100000),
):
    try:
        channel_url = canonical_channel_url(url, section)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result = await asyncio.to_thread(_channel_page, channel_url, start, lang)
    return {"url": channel_url, "section": section, "lang": lang, "start": start, **result}
