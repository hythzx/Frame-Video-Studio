"""任务 API：创建（202 幂等）、状态查询与原生文件下载（Range 续传）。

阶段 A 不做实时进度与取消；旧同步下载接口保持兼容运行。
"""
import json
import re
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .store import READY, TaskStore

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_store: TaskStore | None = None


def get_store() -> TaskStore:
    # 惰性初始化：API 进程内共享一个实例；测试可替换。
    global _store
    if _store is None:
        import os
        data_dir = __import__("pathlib").Path(os.getenv("FRAME_DATA_DIR", "/data"))
        _store = TaskStore(data_dir / "tasks.sqlite", data_dir)
    return _store


_TASK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


class TaskCreateRequest(BaseModel):
    url: str = Field(min_length=12, max_length=2048)
    kind: str = Field(default="video", pattern=r"^(video|audio|subtitle|bookmark_batch)$")
    format_id: str = Field(default="", max_length=120)


def _validate_id(task_id: str) -> str:
    try:
        UUID(task_id)
    except ValueError:
        raise HTTPException(404, "任务不存在") from None
    return task_id


@router.post("", status_code=202)
async def create_task(
    request: TaskCreateRequest,
    idempotency_key: str | None = Header(default=None, max_length=128),
):
    from ..main import canonical_url

    if request.kind != "video":
        raise HTTPException(422, "该下载类型尚未接入任务系统（阶段 C 迁移）。")
    if not request.format_id.strip():
        raise HTTPException(422, "请提供要下载的画质格式。")
    url, platform = canonical_url(request.url)
    key = (idempotency_key or "").strip() or None
    task = get_store().create(kind="video", url=url, platform=platform,
                              format_id=request.format_id.strip(), idempotency_key=key)
    return {
        "id": task["id"],
        "status": task["status"],
        "status_url": f"/api/tasks/{task['id']}",
        "reused": task.get("reused", False),
    }


@router.get("/{task_id}")
async def task_status(task_id: str):
    task = get_store().get(_validate_id(task_id))
    if not task:
        raise HTTPException(404, "任务不存在")
    body = {k: task[k] for k in ("id", "status", "kind", "url", "platform",
                                 "format_id", "title", "error", "file_size",
                                 "created_at", "updated_at")}
    body["download_url"] = f"/api/tasks/{task['id']}/file" if task["status"] == READY else None
    return body


_FILENAME_UNSAFE = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def _download_filename(task: dict) -> str:
    """以视频标题命名下载文件；无标题或净化后为空时回退到任务 ID。"""
    title = _FILENAME_UNSAFE.sub(" ", task.get("title") or "").strip()
    title = re.sub(r"\s+", " ", title)[:80].strip(" .")
    if not title:
        return f"video-{task['id']}.mp4"
    return f"{title}.mp4"


@router.get("/{task_id}/file")
async def task_file(task_id: str):
    store = get_store()
    task = store.get(_validate_id(task_id))
    if not task:
        raise HTTPException(404, "任务不存在")
    if task["status"] != READY:
        raise HTTPException(409, "任务尚未完成，文件不可下载。")
    path = store.file_path(task["id"])
    if not path.is_file():
        raise HTTPException(410, "文件已过期或不存在。")
    return FileResponse(path, media_type="video/mp4",
                        filename=_download_filename(task))
