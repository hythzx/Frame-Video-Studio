"""解析历史存储测试：按 URL 去重更新、按时间倒序列表、条目上限裁剪。"""
from app.tasks.store import TaskStore

SAMPLE = {
    "platform": "youtube", "url": "https://x.com/i/status/999",
    "video_id": "999", "title": "Big Buck Bunny",
    "description": "a bunny", "thumbnail": "https://img/thumb.jpg",
    "duration": 596.5, "uploader": "Blender",
    "formats_json": '[{"id": "136+140", "height": 720}]',
}


def make_store(tmp_path):
    return TaskStore(tmp_path / "db.sqlite", tmp_path)


def test_record_and_list_latest_first(tmp_path):
    store = make_store(tmp_path)
    store.record_extraction(**SAMPLE)
    second = dict(SAMPLE, url="u2", video_id="222", title="Second")
    store.record_extraction(**second)
    history = store.list_history()
    assert [h["title"] for h in history] == ["Second", "Big Buck Bunny"]


def test_same_url_updates_existing_entry(tmp_path):
    store = make_store(tmp_path)
    store.record_extraction(**SAMPLE)
    updated = dict(SAMPLE, title="Big Buck Bunny (re-extract)", duration=600)
    store.record_extraction(**updated)
    history = store.list_history()
    assert len(history) == 1
    assert history[0]["title"] == "Big Buck Bunny (re-extract)"


def test_history_capped_at_limit(tmp_path):
    store = make_store(tmp_path)
    for i in range(210):
        store.record_extraction(**dict(SAMPLE, url=f"u{i}", video_id=str(i), title=f"t{i}"))
    assert len(store.list_history(limit=300)) == 200


def test_list_contains_all_display_fields(tmp_path):
    store = make_store(tmp_path)
    store.record_extraction(**SAMPLE)
    entry = store.list_history()[0]
    for key in ("platform", "url", "video_id", "title", "thumbnail",
                "duration", "uploader", "formats_json", "extracted_at"):
        assert key in entry and entry[key] is not None
