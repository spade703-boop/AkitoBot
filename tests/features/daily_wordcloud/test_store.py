from __future__ import annotations

from datetime import date
import sqlite3

import pytest

from nonebot_plugin_akito.features.daily_wordcloud import analysis, store


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


async def test_import_history_rows_are_deduplicated_and_excluded(isolated_database):
    rows = [
        ("u1", "甲", "hello", "history:1", 100),
        ("u1", "甲", "hello", "history:1", 100),
        ("bot", "机器人", "ignored", "history:2", 101),
    ]

    assert await store.import_raw_messages("1001", rows, excluded_user_ids={"bot"}) == 1
    assert await store.import_raw_messages("1001", rows, excluded_user_ids={"bot"}) == 0
    assert await store.fetch_raw_messages("1001", 0, 200) == [("u1", "甲", "hello", 100)]


async def test_excluded_users_are_persistent_and_skipped_for_new_messages(isolated_database):
    assert await store.add_excluded_user_ids(["834285229", "123456789", "834285229"], "9001") == 2
    assert await store.list_excluded_user_ids() == ["123456789", "834285229"]
    assert await store.record_raw_message("1001", "834285229", "bot", "hello", "excluded", 100) is False
    assert await store.record_raw_message("1001", "123456789", "user", "hello", "excluded-2", 100) is False
    assert await store.record_raw_message("1001", "u1", "user", "hello", "kept", 100) is True

    assert await store.remove_excluded_user_ids(["834285229"]) == 1
    assert await store.list_excluded_user_ids() == ["123456789"]


async def test_history_messages_can_be_read_from_a_live_database_slice(tmp_path):
    history_path = tmp_path / "impression_history.db"
    with sqlite3.connect(history_path) as connection:
        connection.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, group_id TEXT, user_id TEXT, nickname TEXT, content TEXT, timestamp DATETIME)"
        )
        connection.executemany(
            "INSERT INTO messages (group_id, user_id, nickname, content, timestamp) VALUES (?, ?, ?, ?, ?)",
            [
                ("1001", "u1", "甲", "start", "2026-08-28 16:00:00"),
                ("1001", "u2", "乙", "end", "2026-08-29 15:59:59"),
                ("1001", "u3", "丙", "next", "2026-08-29 16:00:00"),
                ("1002", "u4", "丁", "other", "2026-08-29 10:00:00"),
            ],
        )
        connection.commit()

    start_time, end_time = analysis.date_bounds(date(2026, 8, 29))
    rows = await store.fetch_history_messages(
        "1001",
        start_time,
        end_time,
        database_path=history_path,
    )

    assert rows == [("u1", "甲", "start", start_time), ("u2", "乙", "end", end_time - 1)]


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
