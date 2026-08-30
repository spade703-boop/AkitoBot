"""SQLite persistence for daily group word-cloud reports."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, cast

import aiosqlite

from ...core import get_data_dir

DATABASE_PATH = get_data_dir() / "daily_wordcloud.db"
_WRITE_LOCK = asyncio.Lock()
_EXCLUDED_USER_IDS: set[str] = set()


def get_history_database_path() -> Path:
    configured_path = os.environ.get("WORDCLOUD_HISTORY_DB", "").strip()
    if configured_path:
        return Path(configured_path).expanduser()
    return get_data_dir() / "impression_history.db"


def init_database() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DATABASE_PATH)) as connection, connection:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS raw_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                nickname TEXT NOT NULL,
                content TEXT NOT NULL,
                message_id TEXT NOT NULL,
                event_time INTEGER NOT NULL,
                UNIQUE(group_id, message_id)
            )
            """
        )
        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_wordcloud_raw_group_time
            ON raw_messages(group_id, event_time)
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS daily_reports (
                group_id TEXT NOT NULL,
                report_date TEXT NOT NULL,
                payload TEXT NOT NULL,
                delivery_state TEXT NOT NULL DEFAULT 'pending',
                sent_at INTEGER,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(group_id, report_date)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS blocked_words (
                word TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS excluded_users (
                user_id TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                created_at INTEGER NOT NULL
            )
            """
        )
        _EXCLUDED_USER_IDS.clear()
        _EXCLUDED_USER_IDS.update(str(row[0]) for row in cursor.execute("SELECT user_id FROM excluded_users"))


init_database()


async def _configure(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA busy_timeout=5000")


async def record_raw_message(
    group_id: str,
    user_id: str,
    nickname: str,
    content: str,
    message_id: str,
    event_time: int,
) -> bool:
    """Insert one incoming message, returning whether it was new."""
    if str(user_id) in _EXCLUDED_USER_IDS:
        return False
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        cursor = await connection.execute(
            """
            INSERT OR IGNORE INTO raw_messages
                (group_id, user_id, nickname, content, message_id, event_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (str(group_id), str(user_id), nickname, content, str(message_id), int(event_time)),
        )
        await connection.commit()
        return cursor.rowcount > 0


async def fetch_raw_messages(
    group_id: str,
    start_time: int,
    end_time: int,
) -> list[tuple[str, str, str, int]]:
    """Read messages in a half-open Unix timestamp range."""
    async with aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        async with connection.execute(
            """
            SELECT user_id, nickname, content, event_time
            FROM raw_messages
            WHERE group_id=? AND event_time>=? AND event_time<?
            ORDER BY event_time ASC, id ASC
            """,
            (str(group_id), int(start_time), int(end_time)),
        ) as cursor:
            return cast(list[tuple[str, str, str, int]], await cursor.fetchall())


def _sqlite_timestamp_to_epoch(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())


async def fetch_history_messages(
    group_id: str,
    start_time: int,
    end_time: int,
    *,
    database_path: Path | None = None,
) -> list[tuple[str, str, str, int]]:
    """Read legacy impression messages for a Beijing-time date window."""
    records = await fetch_history_records(
        group_id,
        start_time,
        end_time,
        database_path=database_path,
    )
    return [(user_id, nickname, content, event_time) for user_id, nickname, content, _message_id, event_time in records]


async def fetch_history_records(
    group_id: str,
    start_time: int,
    end_time: int,
    *,
    database_path: Path | None = None,
) -> list[tuple[str, str, str, str, int]]:
    """Read legacy impression messages with stable source message IDs."""
    source_path = database_path or get_history_database_path()
    if not source_path.is_file():
        raise FileNotFoundError(f"历史消息库不存在: {source_path}")

    start_text = datetime.fromtimestamp(start_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    end_text = datetime.fromtimestamp(end_time, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(source_path) as connection:
        await _configure(connection)
        async with connection.execute("PRAGMA table_info(messages)") as cursor:
            columns = {str(row[1]) for row in await cursor.fetchall()}
        message_id_expression = "message_id" if "message_id" in columns else "NULL"
        async with connection.execute(
            f"""
            SELECT id, user_id, nickname, content, timestamp, {message_id_expression}
            FROM messages
            WHERE group_id=? AND timestamp>=? AND timestamp<?
            ORDER BY id ASC
            """,
            (str(group_id), start_text, end_text),
        ) as cursor:
            fetched_rows = await cursor.fetchall()

    rows: list[tuple[str, str, str, str, int]] = []
    for row_id, user_id, nickname, content, timestamp, message_id in fetched_rows:
        event_time = _sqlite_timestamp_to_epoch(timestamp)
        if event_time is None or not (start_time <= event_time < end_time):
            continue
        stable_message_id = str(message_id or f"history:{row_id}")
        rows.append((str(user_id), str(nickname or ""), str(content or ""), stable_message_id, event_time))
    return rows


async def import_raw_messages(
    group_id: str,
    rows: Iterable[tuple[str, str, str, str, int]],
    *,
    excluded_user_ids: Iterable[str] = (),
) -> int:
    """Import historical rows into the local raw table, deduplicated by message ID."""
    excluded = _EXCLUDED_USER_IDS | {str(user_id) for user_id in excluded_user_ids}
    values = [
        (str(group_id), str(user_id), str(nickname), str(content), str(message_id), int(event_time))
        for user_id, nickname, content, message_id, event_time in rows
        if str(user_id) not in excluded and str(message_id).strip()
    ]
    if not values:
        return 0
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        before = connection.total_changes
        await connection.executemany(
            """
            INSERT OR IGNORE INTO raw_messages
                (group_id, user_id, nickname, content, message_id, event_time)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        await connection.commit()
        return connection.total_changes - before


async def save_report(group_id: str, report_date: str, payload: dict[str, Any]) -> None:
    """Upsert a report and reset its delivery state for the new result."""
    state = "skipped" if not payload.get("frequencies") else "pending"
    serialized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        await connection.execute(
            """
            INSERT INTO daily_reports
                (group_id, report_date, payload, delivery_state, sent_at, updated_at)
            VALUES (?, ?, ?, ?, NULL, ?)
            ON CONFLICT(group_id, report_date) DO UPDATE SET
                payload=excluded.payload,
                delivery_state=CASE
                    WHEN daily_reports.delivery_state='sent' THEN 'sent'
                    ELSE excluded.delivery_state
                END,
                sent_at=CASE
                    WHEN daily_reports.delivery_state='sent' THEN daily_reports.sent_at
                    ELSE NULL
                END,
                updated_at=excluded.updated_at
            """,
            (str(group_id), report_date, serialized, state, int(time.time())),
        )
        await connection.commit()


async def load_report(group_id: str, report_date: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        async with connection.execute(
            "SELECT payload FROM daily_reports WHERE group_id=? AND report_date=?",
            (str(group_id), report_date),
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        return None
    try:
        loaded = json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return None
    return cast(dict[str, Any], loaded) if isinstance(loaded, dict) else None


async def report_needs_delivery(group_id: str, report_date: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        async with connection.execute(
            """
            SELECT 1 FROM daily_reports
            WHERE group_id=? AND report_date=? AND delivery_state='pending'
            """,
            (str(group_id), report_date),
        ) as cursor:
            return await cursor.fetchone() is not None


async def mark_report_sent(group_id: str, report_date: str) -> None:
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        await connection.execute(
            """
            UPDATE daily_reports SET delivery_state='sent', sent_at=?
            WHERE group_id=? AND report_date=?
            """,
            (int(time.time()), str(group_id), report_date),
        )
        await connection.commit()


async def cleanup_raw_messages(cutoff_time: int) -> int:
    """Delete raw message bodies older than the retention cutoff."""
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        cursor = await connection.execute(
            "DELETE FROM raw_messages WHERE event_time<?",
            (int(cutoff_time),),
        )
        await connection.commit()
        return max(0, int(cursor.rowcount))


async def list_blocked_words() -> list[str]:
    async with aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        async with connection.execute("SELECT word FROM blocked_words ORDER BY word ASC") as cursor:
            rows = await cursor.fetchall()
    return [str(row[0]) for row in rows]


async def list_excluded_user_ids() -> list[str]:
    return sorted(_EXCLUDED_USER_IDS)


async def add_excluded_user_ids(user_ids: list[str], created_by: str) -> int:
    if not user_ids:
        return 0
    normalized_ids = sorted({str(user_id) for user_id in user_ids})
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        before = connection.total_changes
        await connection.executemany(
            "INSERT OR IGNORE INTO excluded_users(user_id, created_by, created_at) VALUES (?, ?, ?)",
            [(user_id, str(created_by), int(time.time())) for user_id in normalized_ids],
        )
        await connection.commit()
        changed = connection.total_changes - before
    _EXCLUDED_USER_IDS.update(normalized_ids)
    return changed


async def remove_excluded_user_ids(user_ids: list[str]) -> int:
    if not user_ids:
        return 0
    normalized_ids = sorted({str(user_id) for user_id in user_ids})
    placeholders = ",".join("?" for _ in normalized_ids)
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        cursor = await connection.execute(
            f"DELETE FROM excluded_users WHERE user_id IN ({placeholders})",
            normalized_ids,
        )
        await connection.commit()
        changed = max(0, int(cursor.rowcount))
    _EXCLUDED_USER_IDS.difference_update(normalized_ids)
    return changed


async def add_blocked_words(words: list[str], created_by: str) -> int:
    if not words:
        return 0
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        before = connection.total_changes
        await connection.executemany(
            "INSERT OR IGNORE INTO blocked_words(word, created_by, created_at) VALUES (?, ?, ?)",
            [(word, str(created_by), int(time.time())) for word in words],
        )
        await connection.commit()
        return connection.total_changes - before


async def remove_blocked_words(words: list[str]) -> int:
    if not words:
        return 0
    placeholders = ",".join("?" for _ in words)
    async with _WRITE_LOCK, aiosqlite.connect(DATABASE_PATH) as connection:
        await _configure(connection)
        cursor = await connection.execute(
            f"DELETE FROM blocked_words WHERE word IN ({placeholders})",
            words,
        )
        await connection.commit()
        return max(0, int(cursor.rowcount))
