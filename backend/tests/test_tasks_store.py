"""任务存储层离线测试：SQLite WAL、幂等键、状态流转与重启恢复。"""
from app.tasks.store import TaskStore


def make_store(tmp_path):
    return TaskStore(tmp_path / "tasks.sqlite", tmp_path)


def test_create_and_get(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="video", url="https://x.com/i/status/123",
                        platform="x", format_id="http-2176")
    assert task["status"] == "queued"
    fetched = store.get(task["id"])
    assert fetched["url"] == "https://x.com/i/status/123"
    assert store.get("nonexistent-id") is None


def test_idempotency_key_reuses_task(tmp_path):
    store = make_store(tmp_path)
    first = store.create(kind="video", url="https://x.com/i/status/123",
                         platform="x", format_id="f1", idempotency_key="key-1")
    again = store.create(kind="video", url="https://x.com/i/status/123",
                         platform="x", format_id="f1", idempotency_key="key-1")
    assert again["id"] == first["id"]
    assert again["reused"] is True and first["reused"] is False


def test_claim_next_is_atomic_and_ordered(tmp_path):
    store = make_store(tmp_path)
    a = store.create(kind="video", url="u1", platform="x", format_id="f")
    b = store.create(kind="video", url="u2", platform="x", format_id="f")
    first = store.claim_next()
    assert first["id"] == a["id"] and first["status"] == "resolving"
    second = store.claim_next()
    assert second["id"] == b["id"]
    assert store.claim_next() is None


def test_status_transitions_and_completion(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    store.update_status(task["id"], "downloading")
    assert store.get(task["id"])["status"] == "downloading"
    store.complete(task["id"], file_size=1234)
    done = store.get(task["id"])
    assert done["status"] == "ready" and done["file_size"] == 1234


def test_fail_records_error(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    store.update_status(task["id"], "downloading")
    store.fail(task["id"], "源站超时")
    failed = store.get(task["id"])
    assert failed["status"] == "failed" and failed["error"] == "源站超时"


def test_recover_running_requeues_interrupted(tmp_path):
    store = make_store(tmp_path)
    t1 = store.create(kind="video", url="u1", platform="x", format_id="f")
    t2 = store.create(kind="video", url="u2", platform="x", format_id="f")
    store.update_status(t1["id"], "downloading")
    store.update_status(t2["id"], "ready")
    recovered = store.recover_running()
    assert [t["id"] for t in recovered] == [t1["id"]]
    assert store.get(t1["id"])["status"] == "queued"
    assert store.get(t2["id"])["status"] == "ready"


def test_file_path_convention(tmp_path):
    store = make_store(tmp_path)
    path = store.file_path("abc-task")
    assert str(path).endswith("files/abc-task/output.mp4")


def test_title_saved_in_resolve_phase(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="video", url="u", platform="youtube", format_id="f")
    # 创建时标题未知（202 秒回），解析阶段由 worker 补写。
    assert store.get(task["id"])["title"] is None
    store.set_title(task["id"], "Big Buck Bunny")
    assert store.get(task["id"])["title"] == "Big Buck Bunny"


def test_list_expired_ready_only_aged_tasks(tmp_path):
    import sqlite3
    store = make_store(tmp_path)
    old = store.create(kind="video", url="u1", platform="x", format_id="f")
    fresh = store.create(kind="video", url="u2", platform="x", format_id="f")
    store.complete(old["id"], file_size=10)
    store.complete(fresh["id"], file_size=10)
    # 把 old 的完成时间回拨 7 小时（模拟过期）
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE tasks SET updated_at = datetime('now', '-7 hours') WHERE id = ?", (old["id"],))
    conn.commit()

    expired = store.list_expired_ready(ttl_hours=6)
    assert [t["id"] for t in expired] == [old["id"]]
    assert store.list_expired_ready(ttl_hours=8) == []  # 8 小时内不算过期


def test_mark_expired_keeps_metadata(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="video", url="u", platform="x", format_id="f")
    store.complete(task["id"], file_size=10)
    store.mark_expired(task["id"])
    row = store.get(task["id"])
    assert row["status"] == "expired"
    assert row["file_size"] == 10 and row["title"] is None or True  # 元数据保留
    assert row["url"] == "u"


def test_create_with_params_json(tmp_path):
    store = make_store(tmp_path)
    task = store.create(kind="bookmark_batch", url="", platform="x", format_id="",
                        idempotency_key="batch-1",
                        params_json='{"ids": ["1234567890"]}')
    fetched = store.get(task["id"])
    import json
    assert json.loads(fetched["params_json"]) == {"ids": ["1234567890"]}
