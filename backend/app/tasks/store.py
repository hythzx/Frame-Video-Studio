"""SQLite WAL 任务存储：API 与 Worker 通过同一数据库共享任务状态。

单机部署不引入外部队列；所有写操作使用短连接，跨进程安全。
"""
import sqlite3
import uuid
from pathlib import Path

QUEUED = "queued"
RESOLVING = "resolving"
DOWNLOADING = "downloading"
READY = "ready"
FAILED = "failed"
CANCELED = "canceled"
INTERRUPTED = "interrupted"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT NOT NULL,
    platform TEXT NOT NULL,
    format_id TEXT NOT NULL,
    title TEXT,
    error TEXT,
    file_size INTEGER,
    idempotency_key TEXT UNIQUE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""

_HISTORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS extracts (
    url TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    thumbnail TEXT,
    duration REAL,
    uploader TEXT,
    formats_json TEXT NOT NULL,
    extracted_at TEXT NOT NULL
)
"""

_TASK_COLUMNS = ("id", "status", "kind", "url", "platform", "format_id",
                 "title", "error", "file_size", "created_at", "updated_at")

_HISTORY_COLUMNS = ("url", "platform", "video_id", "title", "description",
                    "thumbnail", "duration", "uploader", "formats_json", "extracted_at")


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class TaskStore:
    def __init__(self, db_path: Path, data_dir: Path):
        self.db_path = Path(db_path)
        self.data_dir = Path(data_dir)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)
            conn.execute(_HISTORY_SCHEMA)
            # 轻量迁移：为既有数据库补齐后加的列。
            try:
                conn.execute("ALTER TABLE tasks ADD COLUMN title TEXT")
            except sqlite3.OperationalError:
                pass

    def record_extraction(self, platform: str, url: str, video_id: str, title: str,
                          description: str | None, thumbnail: str | None,
                          duration: float | None, uploader: str | None,
                          formats_json: str) -> None:
        """记录一次成功解析；同一规范化 URL 覆盖更新并刷新时间。"""
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO extracts (url, platform, video_id, title, description,"
                " thumbnail, duration, uploader, formats_json, extracted_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(url) DO UPDATE SET platform=excluded.platform,"
                " video_id=excluded.video_id, title=excluded.title,"
                " description=excluded.description, thumbnail=excluded.thumbnail,"
                " duration=excluded.duration, uploader=excluded.uploader,"
                " formats_json=excluded.formats_json, extracted_at=excluded.extracted_at",
                (url, platform, video_id, title, description, thumbnail,
                 duration, uploader, formats_json, _now()))

    def list_history(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM extracts ORDER BY extracted_at DESC, rowid DESC").fetchall()
        return [dict(row) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def file_path(self, task_id: str) -> Path:
        return self.data_dir / "files" / task_id / "output.mp4"

    def work_dir(self, task_id: str) -> Path:
        return self.data_dir / "work" / task_id

    def create(self, kind: str, url: str, platform: str, format_id: str,
               idempotency_key: str | None = None) -> dict:
        with self._connect() as conn:
            if idempotency_key:
                row = conn.execute(
                    "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
                if row:
                    existing = dict(row)
                    existing["reused"] = True
                    return existing
            task_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO tasks (id, status, kind, url, platform, format_id,"
                " idempotency_key, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task_id, QUEUED, kind, url, platform, format_id,
                 idempotency_key, _now(), _now()))
        task = self.get(task_id)
        task["reused"] = False
        return task

    def get(self, task_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update_status(self, task_id: str, status: str, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                         (status, error, _now(), task_id))

    def set_title(self, task_id: str, title: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE tasks SET title = ?, updated_at = ? WHERE id = ?",
                         (title[:200], _now(), task_id))

    def claim_next(self) -> dict | None:
        """原子认领最早的 queued 任务并置为 resolving；无任务时返回 None。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM tasks WHERE status = ? ORDER BY created_at LIMIT 1",
                (QUEUED,)).fetchone()
            if not row:
                conn.rollback()
                return None
            conn.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?",
                         (RESOLVING, _now(), row["id"]))
            claimed = conn.execute("SELECT * FROM tasks WHERE id = ?", (row["id"],)).fetchone()
            conn.commit()
        return dict(claimed)

    def complete(self, task_id: str, file_size: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, file_size = ?, error = NULL, updated_at = ?"
                " WHERE id = ?", (READY, file_size, _now(), task_id))

    def fail(self, task_id: str, error: str) -> None:
        self.update_status(task_id, FAILED, error[:300])

    def recover_running(self) -> list[dict]:
        """Worker 启动恢复：运行中被中断的任务标记 interrupted 并重新排队。"""
        running = (RESOLVING, DOWNLOADING)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM tasks WHERE status IN (?, ?)", running).fetchall()
            recovered = []
            for row in rows:
                conn.execute(
                    "UPDATE tasks SET status = ?, error = ?, updated_at = ? WHERE id = ?",
                    (QUEUED, "服务重启导致任务中断，已自动重新排队", _now(), row["id"]))
                recovered.append(self.get(row["id"]))
        return recovered

    def list_tasks(self, limit: int = 50) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]
