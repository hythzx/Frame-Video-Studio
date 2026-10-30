"""下载任务 Worker：独立进程，轮询认领任务并执行 yt-dlp 下载与合并。

启动时恢复中断任务；执行失败分类记录，不无限重试。并发由
WORKER_CONCURRENCY（默认 2）控制。
"""
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
        # 解析阶段：提前校验格式可用性，快速失败，不进入下载。
        store.update_status(task_id, RESOLVING)
        data = app_main.extract(task["url"])
        formats = app_main.choices(data, task["platform"])
        if task["format_id"] not in {x["id"] for x in formats}:
            raise ValueError("该画质已不可用，请重新解析。")

        store.update_status(task_id, DOWNLOADING)
        output = app_main.download_video(task["url"], task["format_id"], work_dir, task["platform"])

        target = store.file_path(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(output), str(target))
        store.complete(task_id, file_size=target.stat().st_size)
    except Exception as exc:
        store.fail(task_id, str(exc))
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


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
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = set()
        while True:
            futures = {f for f in futures if not f.done()}
            while len(futures) < concurrency:
                task = store.claim_next()
                if not task:
                    break
                futures.add(pool.submit(_execute, store, task))
            time.sleep(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
