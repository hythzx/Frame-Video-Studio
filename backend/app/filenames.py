"""File naming helpers for X/Twitter media.

Names use the tweet body, not yt-dlp's generic video title. Filenames are
limited to the first 20 usable non-whitespace characters; only missing/empty
tweet text falls back to the tweet ID.
"""
import re
from urllib.parse import urlsplit

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]')
_TWEET_ID = re.compile(r"^[0-9]{5,25}$")
_STATUS_ID = re.compile(r"/(?:[^/]+/status|i/(?:web/)?status)/(\d+)")
_WINDOWS_RESERVED = re.compile(r"^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)


def tweet_id_from_url(url: str) -> str:
    match = _STATUS_ID.search(urlsplit(url).path)
    return match.group(1) if match else ""


def tweet_body(info: dict | None) -> str:
    """yt-dlp's description normally contains the actual X post text."""
    if not isinstance(info, dict):
        return ""
    for value in (info.get("full_text"), info.get("description"),
                  (info.get("legacy") or {}).get("full_text"),
                  info.get("text")):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def tweet_video_stem(text: str | None, tweet_id: str) -> str:
    """Make a cross-platform-safe file stem from the first 20 visible chars."""
    body = text if isinstance(text, str) else ""
    body = "".join(ch for ch in body if not ch.isspace())
    body = _UNSAFE.sub("", body)
    body = body.strip(" .")[:20].rstrip(" .")
    if body:
        if _WINDOWS_RESERVED.fullmatch(body):
            return "_" + body
        return body
    return str(tweet_id) if _TWEET_ID.fullmatch(str(tweet_id)) else "video"


def unique_video_name(stem: str, existing: set[str]) -> str:
    """Keep distinct videos with identical tweet prefixes in the same ZIP."""
    filename = f"{stem}.mp4"
    n = 2
    while filename.casefold() in existing:
        filename = f"{stem}({n}).mp4"
        n += 1
    existing.add(filename.casefold())
    return filename
