"""下载任务 Worker：独立进程，轮询认领任务并执行 yt-dlp 下载与合并。

启动时恢复中断任务；执行失败分类记录，不无限重试。并发由
WORKER_CONCURRENCY（默认 2）控制。
"""
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from .. import main as app_main
from .store import DOWNLOADING, RESOLVING, TaskStore


def _execute(store: TaskStore, task: dict) -> None:
    task_id = task["id"]
    work_dir = store.work_dir(task_id)
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        store.update_status(task_id, RESOLVING)
        if task["kind"] == "bookmark_batch":
            # 收藏批量：复用 bookmarks 的顺序下载与 ZIP 打包逻辑。
            from .. import bookmarks as app_bookmarks
            ids = (json.loads(task.get("params_json") or "{}").get("ids")) or []
            output = app_bookmarks._download_bookmarks(ids, work_dir)
        else:
            # 解析阶段：提取元数据（标题供文件命名）；格式匹配与降级
            # 由 download_video 在其自身的单次提取内完成，避免跨提取 ID 漂移。
            data = app_main.extract(task["url"])
            store.set_title(task_id, str(data.get("title") or ""))

            store.update_status(task_id, DOWNLOADING)
            output = app_main.download_video(
                task["url"], task["format_id"], work_dir, task["platform"])

        target = store.file_dir(task_id) / f"output{output.suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(target))
        store.complete(task_id, file_size=target.stat().st_size)
    except Exception as exc:
        store.fail(task_id, str(exc))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def cleanup_expired_files(store: TaskStore, ttl_hours: float) -> int:
    """删除超过保留时长的完成文件，任务标记 expired（元数据保留）。"""
    removed = 0
    for task in store.list_expired_ready(ttl_hours=ttl_hours):
        shutil.rmtree(store.file_path(task["id"]).parent, ignore_errors=True)
        store.mark_expired(task["id"])
        removed += 1
    return removed


def process_next_task(store: TaskStore) -> bool:
    """认领并同步执行一个任务；无任务时返回 False。"""
    task = store.claim_next()
    if not task:
        return False
    _execute(store, task)
    return True


def main() -> None:
    import os

    data_dir = os.getenv("FRAME_DATA_DIR", "/data")
    store = TaskStore(f"{data_dir}/tasks.sqlite", f"{data_dir}")
    recovered = store.recover_running()
    print(f"[worker] recovered {len(recovered)} interrupted task(s)", flush=True)

    concurrency = int(os.getenv("WORKER_CONCURRENCY", "2"))
    print(f"[worker] started, concurrency={concurrency}", flush=True)
    ttl_hours = float(os.getenv("FILE_TTL_HOURS", "6"))
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = set()
        last_cleanup = time.monotonic()
        while True:
            futures = {f for f in futures if not f.done()}
            while len(futures) < concurrency:
                task = store.claim_next()
                if not task:
                    break
                futures.add(pool.submit(_execute, store, task))
            # 每小时清理一次超过保留时长的完成文件。
            if time.monotonic() - last_cleanup >= 3600:
                removed = cleanup_expired_files(store, ttl_hours)
                if removed:
                    print(f"[worker] cleaned {removed} expired file(s)", flush=True)
                last_cleanup = time.monotonic()
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
