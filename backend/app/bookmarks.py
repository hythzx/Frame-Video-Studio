"""Read-only, cookie-backed X bookmark browser and bounded batch downloader.

This integration calls the unofficial web Bookmarks GraphQL API directly with
the server-configured cookies. Keep it disabled unless both TWITTER_COOKIES
and FRAME_ACCESS_TOKEN are explicitly configured.
"""
import asyncio
import json
import os
import re
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
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

# 公共 web 客户端 Bearer Token；非账号凭据。
_BEARER_TOKEN = (
    "AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D"
    "1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
_BOOKMARKS_ENDPOINT = "https://x.com/i/api/graphql/jbqsfdzI0EMFHAtpEC6vyA/Bookmarks"
_BOOKMARKS_FEATURES = {
    "rweb_video_screen_enabled": False,
    "rweb_cashtags_enabled": True,
    "profile_label_improvements_pcf_label_in_post_enabled": True,
    "responsive_web_profile_redirect_enabled": True,
    "rweb_tipjar_consumption_enabled": False,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_timeline_navigation_enabled": True,
    "premium_content_api_read_enabled": False,
    "communities_web_enable_tweet_community_results_fetch": True,
    "c9s_tweet_anatomy_moderator_badge_enabled": True,
    "responsive_web_grok_analyze_button_fetch_trends_enabled": False,
    "responsive_web_grok_analyze_post_followups_enabled": True,
    "rweb_cashtags_composer_attachment_enabled": True,
    "responsive_web_jetfuel_frame": True,
    "rweb_sports_post_context_enabled": True,
    "x_elections_post_context_enabled": True,
    "responsive_web_grok_share_attachment_enabled": True,
    "responsive_web_grok_annotations_enabled": True,
    "articles_preview_enabled": True,
    "responsive_web_edit_tweet_api_enabled": True,
    "rweb_conversational_replies_downvote_enabled": False,
    "graphql_is_translatable_rweb_tweet_is_translatable_enabled": True,
    "view_counts_everywhere_api_enabled": True,
    "longform_notetweets_consumption_enabled": True,
    "responsive_web_twitter_article_tweet_consumption_enabled": True,
    "content_disclosure_indicator_enabled": True,
    "content_disclosure_ai_generated_indicator_enabled": True,
    "responsive_web_grok_show_grok_translated_post": True,
    "responsive_web_grok_analysis_button_from_backend": True,
    "post_ctas_fetch_enabled": False,
    "freedom_of_speech_not_reach_fetch_enabled": True,
    "standardized_nudges_misinfo": True,
    "tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled": True,
    "longform_notetweets_rich_text_read_enabled": True,
    "longform_notetweets_inline_media_enabled": False,
    "responsive_web_nested_quote_preview_enabled": True,
    "responsive_web_grok_image_annotation_enabled": True,
    "responsive_web_grok_imagine_annotation_enabled": True,
    "responsive_web_made_on_x_label_enabled": False,
    "responsive_web_grok_community_note_auto_translation_is_enabled": True,
    "responsive_web_enhance_cards_enabled": False,
}


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


def _bookmarks_headers() -> dict:
    cookies = _cookie_pairs()
    # GET 请求不可携带 content-type 等 POST 语义头，X 的反爬会直接 403。
    return {
        "authorization": _BEARER_TOKEN,
        "x-csrf-token": cookies.get("ct0", ""),
        "cookie": f"auth_token={cookies.get('auth_token', '')}; ct0={cookies.get('ct0', '')}",
        "x-twitter-auth-type": "OAuth2Session",
        "Referer": "https://x.com/i/bookmarks",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    }


def _bookmarks_url(cursor: str | None, count: int) -> str:
    variables: dict = {"count": count, "includePromotedContent": True}
    if cursor:
        variables["cursor"] = cursor
    query = urllib.parse.urlencode({
        "variables": json.dumps(variables, separators=(",", ":")),
        "features": json.dumps(_BOOKMARKS_FEATURES, separators=(",", ":")),
    })
    return f"{_BOOKMARKS_ENDPOINT}?{query}"


def _serialize_tweet_result(result: dict) -> dict | None:
    tweet_id = str(result.get("rest_id") or "")
    if not _TWEET_ID.fullmatch(tweet_id):
        return None
    tweet = result.get("legacy") or {}
    media = ((tweet.get("extended_entities") or {}).get("media")
             or (tweet.get("entities") or {}).get("media") or [])
    videos = [m for m in media if m.get("type") in ("video", "animated_gif")]
    cover = next((m.get("media_url") for m in videos if m.get("media_url")), None)
    if not cover:
        cover = next((m.get("media_url") for m in media if m.get("media_url")), None)
    user = (((result.get("core") or {}).get("user_results") or {}).get("result")
            or {}).get("legacy") or {}
    return {
        "id": tweet_id,
        "text": str(tweet.get("full_text") or "")[:800],
        "author": str(user.get("name") or ""),
        "username": str(user.get("screen_name") or ""),
        "created_at": str(tweet.get("created_at") or ""),
        "thumbnail": cover,
        "has_video": bool(videos),
        "url": f"https://x.com/i/status/{tweet_id}",
    }


def _parse_bookmarks_response(data: dict) -> tuple[list[dict], str | None]:
    """从 Bookmarks GraphQL 响应解析收藏条目与底部翻页游标。"""
    timeline = (((data.get("data") or {}).get("bookmark_timeline_v2") or {})
                .get("timeline") or {})
    entries = []
    for instruction in timeline.get("instructions") or []:
        if instruction.get("type") == "TimelineAddEntries":
            entries.extend(instruction.get("entries") or [])

    items: list[dict] = []
    cursor: str | None = None
    for entry in entries:
        content = entry.get("content") or {}
        if content.get("entryType") == "TimelineTimelineCursor":
            if entry.get("entryId", "").startswith("cursor-bottom"):
                cursor = content.get("value") or None
            continue
        result = ((content.get("itemContent") or {}).get("tweet_results") or {}).get("result") or {}
        item = _serialize_tweet_result(result)
        if item:
            items.append(item)
    return items, cursor


def _request_bookmarks_page(cursor: str | None, count: int) -> dict:
    req = urllib.request.Request(_bookmarks_url(cursor, count), headers=_bookmarks_headers())
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            raise HTTPException(429, "X 已限制请求，请停止刷新并等待恢复。") from None
        if exc.code in (401, 403):
            raise HTTPException(401, "X Cookie 已失效或账号未获准读取书签，请在服务器更新 Cookie。") from None
        raise HTTPException(502, "读取 X 收藏失败：非官方接口可能已变化，或当前网络不可访问 X。") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
        raise HTTPException(502, "读取 X 收藏失败：非官方接口可能已变化，或当前网络不可访问 X。") from None


async def _fetch_page(cursor: str | None = None, count: int = 20) -> dict:
    global _LAST_PAGE_REQUEST
    async with _PAGE_LOCK:
        now = time.monotonic()
        if now - _LAST_PAGE_REQUEST < _PAGE_COOLDOWN:
            raise HTTPException(429, "请求过于频繁，请稍后再刷新收藏列表。")
        _LAST_PAGE_REQUEST = now

    data = await asyncio.to_thread(_request_bookmarks_page, cursor, count)
    items, next_cursor = _parse_bookmarks_response(data)
    # Some web API versions return a cursor even on an empty last page.
    return {"items": items, "next_cursor": (next_cursor or None) if items else None}


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
