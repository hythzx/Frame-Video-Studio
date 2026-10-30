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
