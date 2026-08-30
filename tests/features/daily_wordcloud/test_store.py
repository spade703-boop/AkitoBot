from __future__ import annotations

import pytest

from nonebot_plugin_akito.features.daily_wordcloud import store


@pytest.fixture
def isolated_database(tmp_path, monkeypatch):
    database_path = tmp_path / "daily_wordcloud.db"
    monkeypatch.setattr(store, "DATABASE_PATH", database_path)
    store.init_database()
    return database_path


async def test_raw_messages_are_deduplicated_and_filtered_by_time(isolated_database):
    assert await store.record_raw_message("1001", "u1", "阿一", "hello", "m1", 100) is True
    assert await store.record_raw_message("1001", "u1", "阿一", "hello", "m1", 100) is False
    await store.record_raw_message("1001", "u2", "阿二", "world", "m2", 200)

    rows = await store.fetch_raw_messages("1001", 100, 200)

    assert rows == [("u1", "阿一", "hello", 100)]


async def test_report_keeps_sent_state_when_recomputed(isolated_database):
    report = {"frequencies": [["hello", 2]], "top_words": []}
    await store.save_report("1001", "2026-08-29", report)
    assert await store.report_needs_delivery("1001", "2026-08-29") is True
    await store.mark_report_sent("1001", "2026-08-29")

    updated = {"frequencies": [["hello", 3]], "top_words": []}
    await store.save_report("1001", "2026-08-29", updated)

    assert await store.report_needs_delivery("1001", "2026-08-29") is False
    assert await store.load_report("1001", "2026-08-29") == updated


async def test_empty_report_is_saved_as_non_deliverable(isolated_database):
    await store.save_report("1001", "2026-08-29", {"frequencies": [], "top_words": []})

    assert await store.report_needs_delivery("1001", "2026-08-29") is False


async def test_cleanup_only_deletes_rows_before_cutoff(isolated_database):
    await store.record_raw_message("1001", "u1", "阿一", "old", "m1", 99)
    await store.record_raw_message("1001", "u1", "阿一", "kept", "m2", 100)

    assert await store.cleanup_raw_messages(100) == 1
    assert await store.fetch_raw_messages("1001", 0, 1000) == [("u1", "阿一", "kept", 100)]


async def test_blocked_word_crud_is_normalized_by_caller_and_persistent(isolated_database):
    assert await store.add_blocked_words(["akito", "coffee", "akito"], "9001") == 2
    assert await store.list_blocked_words() == ["akito", "coffee"]
    assert await store.remove_blocked_words(["akito"]) == 1
    assert await store.list_blocked_words() == ["coffee"]
