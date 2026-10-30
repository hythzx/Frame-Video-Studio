"""Read-only, cookie-backed X bookmark browser and bounded batch downloader.

This integration uses an unofficial web client. Keep it disabled unless both
TWITTER_COOKIES and FRAME_ACCESS_TOKEN are explicitly configured.
"""
import asyncio
import os
import re
import secrets
import shutil
import tempfile
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

router = APIRouter(prefix="/api/bookmarks", tags=["bookmarks"])
_PAGE_LOCK = asyncio.Lock()
_LAST_PAGE_REQUEST = 0.0
_PAGE_COOLDOWN = 5.0
_MAX_ARCHIVE_BYTES = 600 * 1024 * 1024
_TWEET_ID = re.compile(r"[0-9]{5,25}\Z")


def _cookie_pairs() -> dict[str, str]:
    raw = os.getenv("TWITTER_COOKIES", "")
    values = {}
    for pair in raw.split(";"):
        name, sep, value = pair.strip().partition("=")
        if sep and name and value and "\n" not in value and "\r" not in value:
            values[name] = value
    return values


def _require_access(x_frame_token: str | None = Header(default=None, alias="X-Frame-Token")) -> None:
    expected = os.getenv("FRAME_ACCESS_TOKEN", "")
    if len(expected) < 20:
        raise HTTPException(503, "未配置 FRAME_ACCESS_TOKEN，私人收藏功能已关闭。")
    if not x_frame_token or not secrets.compare_digest(x_frame_token, expected):
        raise HTTPException(401, "收藏访问密钥不正确。")
    cookies = _cookie_pairs()
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise HTTPException(503, "请在服务器配置 TWITTER_COOKIES（至少 auth_token 与 ct0）。")


def _new_client():
    from twikit import Client
    return Client(language="en-US")


def _serialize_tweet(tweet) -> dict | None:
    tweet_id = str(getattr(tweet, "id", "") or "")
    if not _TWEET_ID.fullmatch(tweet_id):
        return None
    media = list(getattr(tweet, "media", None) or [])
    videos = [m for m in media if getattr(m, "type", None) in ("video", "animated_gif")]
    cover = next((getattr(m, "media_url", None) for m in videos if getattr(m, "media_url", None)), None)
    if not cover:
        cover = next((getattr(m, "media_url", None) for m in media if getattr(m, "media_url", None)), None)
    author = getattr(tweet, "user", None)
    username = str(getattr(author, "screen_name", "") or "")
    return {
        "id": tweet_id,
        "text": str(getattr(tweet, "full_text", "") or getattr(tweet, "text", "") or "")[:800],
        "author": str(getattr(author, "name", "") or ""),
        "username": username,
        "created_at": str(getattr(tweet, "created_at", "") or ""),
        "thumbnail": cover,
        "has_video": bool(videos),
        "url": f"https://x.com/i/status/{tweet_id}",
    }


async def _fetch_page(cursor: str | None = None, count: int = 20) -> dict:
    global _LAST_PAGE_REQUEST
    async with _PAGE_LOCK:
        now = time.monotonic()
        if now - _LAST_PAGE_REQUEST < _PAGE_COOLDOWN:
            raise HTTPException(429, "请求过于频繁，请稍后再刷新收藏列表。")
        _LAST_PAGE_REQUEST = now

    client = _new_client()
    client.set_cookies(_cookie_pairs(), clear_cookies=True)
    try:
        result = await client.get_bookmarks(count=count, cursor=cursor)
        items = []
        for tweet in result:
            item = _serialize_tweet(tweet)
            if item:
                items.append(item)
        # Some web API versions return a cursor even on an empty last page.
        next_cursor = str(getattr(result, "next_cursor", "") or "") if items else ""
        return {"items": items, "next_cursor": next_cursor or None}
    except HTTPException:
        raise
    except Exception as exc:
        status = getattr(exc, "status_code", None) or getattr(
            getattr(exc, "response", None), "status_code", None)
        if status == 429 or "rate limit" in str(exc).lower():
            raise HTTPException(429, "X 已限制请求，请停止刷新并等待恢复。") from None
        if status in (401, 403) or "unauthorized" in str(exc).lower():
            raise HTTPException(401, "X Cookie 已失效或账号未获准读取书签，请在服务器更新 Cookie。") from None
        raise HTTPException(502, "读取 X 收藏失败：非官方接口可能已变化，或当前网络不可访问 X。") from None
    finally:
        await client.http.aclose()


@router.get("")
async def list_bookmarks(
    cursor: str | None = Query(default=None, max_length=1024),
    count: int = Query(default=20, ge=1, le=30),
    _: None = Depends(_require_access),
):
    data = await _fetch_page(cursor, count)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


class BookmarkDownloadRequest(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=5)


def _download_bookmarks(ids: list[str], folder: Path) -> Path:
    # Downloads are sequential to avoid hammering X or overloading the server.
    from .main import choices, download_video, extract

    files = []
    failed = []
    total = 0
    for tweet_id in ids:
        url = f"https://x.com/i/status/{tweet_id}"
        destination = folder / tweet_id
        destination.mkdir(mode=0o700)
        try:
            info = extract(url)
            variants = choices(info, "x")
            if not variants:
                raise ValueError("没有可用 MP4 视频格式")
            output = download_video(url, variants[0]["id"], destination, "x")
            size = output.stat().st_size
            if total + size > _MAX_ARCHIVE_BYTES:
                raise ValueError("文件总大小超过 600 MiB 限制")
            total += size
            files.append((tweet_id, output))
        except Exception:
            # Do not include upstream errors: some error messages may contain URLs.
            failed.append(tweet_id)
        if total >= _MAX_ARCHIVE_BYTES:
            break

    if not files:
        raise ValueError("所选推文均未能下载视频，可能没有视频或存在访问限制。")

    if len(ids) == 1:
        return files[0][1]

    archive = folder / "frame-bookmarks.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
        for tweet_id, output in files:
            zf.write(output, arcname=f"x-{tweet_id}.mp4")
        if failed:
            zf.writestr("failed-ids.txt", "\n".join(failed) + "\n")
    return archive


@router.post("/download")
async def download_bookmarks(
    request: BookmarkDownloadRequest,
    _: None = Depends(_require_access),
):
    ids = list(dict.fromkeys(request.ids))
    if not all(_TWEET_ID.fullmatch(x) for x in ids):
        raise HTTPException(422, "推文 ID 格式无效。")

    folder = Path(tempfile.mkdtemp(prefix="frame-bookmarks-"))
    try:
        output = await asyncio.to_thread(_download_bookmarks, ids, folder)
        single = output.suffix == ".mp4"
        return FileResponse(
            output,
            filename=f"x-{ids[0]}.mp4" if single else "frame-bookmarks.zip",
            media_type="video/mp4" if single else "application/zip",
            headers={"Cache-Control": "no-store"},
            background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
        )
    except ValueError as exc:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(502, "下载失败，请检查媒体访问权限与服务器日志。") from None
