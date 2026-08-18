"""
测试记忆模块的原子写入逻辑。
"""
import asyncio
import json
import os
from pathlib import Path
import sqlite3

import pytest

import nonebot_plugin_akito.core.memory as memory

# ── 从 memory.py 抽取的原子写入逻辑（不依赖运行中的 bot） ─────────────────

def atomic_save(data: dict, target_path: Path):
    """模拟 save_memory() 的原子写入：先写 .tmp 再 os.replace。"""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, target_path)


# ── 测试 ───────────────────────────────────────────────────────────────────

def test_atomic_write_creates_file(tmp_path: Path):
    """首次写入创建目标文件。"""
    target = tmp_path / "test_memory.json"
    data = {"key": "value"}
    atomic_save(data, target)
    assert target.exists()


def test_atomic_write_data_integrity(tmp_path: Path):
    """写入后读取的数据完全一致（round-trip）。"""
    target = tmp_path / "test_memory.json"
    data = {
        "group_123": {
            "history": [{"role": "user", "content": "你好"}],
            "temp_implants": [],
        }
    }
    atomic_save(data, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data


def test_atomic_write_overwrites_previous(tmp_path: Path):
    """第二次写入完全覆盖第一次的内容。"""
    target = tmp_path / "test_memory.json"
    atomic_save({"version": 1}, target)
    atomic_save({"version": 2, "extra": "new"}, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {"version": 2, "extra": "new"}


def test_atomic_write_creates_parent_dir(tmp_path: Path):
    """目标目录不存在时自动创建。"""
    target = tmp_path / "nested" / "deep" / "memory.json"
    data = {"hello": "world"}
    atomic_save(data, target)
    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == data


def test_atomic_write_tmp_cleaned_up(tmp_path: Path):
    """写入成功后 .tmp 文件已被 os.replace 移动（不应残留）。"""
    target = tmp_path / "memory.json"
    atomic_save({"x": 1}, target)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_atomic_write_handles_unicode(tmp_path: Path):
    """中文和 emoji 数据完整保存和读取。"""
    target = tmp_path / "memory.json"
    data = {"对话": "🎭 东云彰人：嗯，交给我吧。"}
    atomic_save(data, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded["对话"] == data["对话"]


def test_atomic_write_handles_empty_dict(tmp_path: Path):
    """空 dict 也能正常写入。"""
    target = tmp_path / "memory.json"
    atomic_save({}, target)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == {}


def test_init_db_migrates_message_id_column(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE messages (id INTEGER PRIMARY KEY, group_id TEXT, user_id TEXT, "
            "nickname TEXT, content TEXT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP)"
        )
    monkeypatch.setattr(memory, "DB_PATH", db_path)

    memory.init_db()

    with sqlite3.connect(db_path) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(messages)")}
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(messages)")}
    assert "message_id" in columns
    assert "idx_messages_gid_id" in indexes


@pytest.mark.asyncio
async def test_group_context_excludes_current_and_drops_stale_topic(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "context.db"
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    memory.init_db()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO messages (group_id, user_id, nickname, content, message_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, datetime('now', ?))",
            [
                ("1001", "1", "甲", "很久以前的话题", "old", "-170 seconds"),
                ("1001", "2", "乙", "刚才的铺垫", "recent", "-20 seconds"),
                ("1001", "bot", "东云彰人", "没有消息 ID 的回复", None, "-10 seconds"),
                ("1001", "3", "丙", "当前触发消息", "current", "-1 second"),
            ],
        )

    result = await memory.get_group_context(
        "1001",
        limit=12,
        max_age_seconds=180,
        max_gap_seconds=90,
        exclude_message_id="current",
        include_timestamps=True,
    )

    assert "刚才的铺垫" in result
    assert "没有消息 ID 的回复" in result
    assert "当前触发消息" not in result
    assert "很久以前的话题" not in result
    assert "分钟前]" in result or "刚刚]" in result


@pytest.mark.asyncio
async def test_message_store_writes_reads_and_deletes_by_group(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "messages.db"
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    memory.init_db()

    await memory.record_message("1001", "1", "甲", "保留这条用户消息", "m1")
    await memory.record_message("1002", "2", "乙", "另一群消息", "m2")
    await memory.record_bot_message("1001", "对小明印象是认真")
    await memory.record_bot_message("1001", "")

    async with memory.open_message_reader() as reader:
        rows = await reader.fetch_impression_history_candidates("1001", "1", limit=10)
        replies = await reader.fetch_recent_impression_reply_contents("1001", "", limit=10)

    assert [(row[1], row[3], row[4]) for row in rows] == [("1", "保留这条用户消息", "m1")]
    assert replies == ["对小明印象是认真"]
    assert await memory.delete_group_messages("1001") == 2

    with sqlite3.connect(db_path) as conn:
        remaining = conn.execute("SELECT group_id, content FROM messages ORDER BY id").fetchall()
    assert remaining == [("1002", "另一群消息")]


@pytest.mark.asyncio
async def test_message_reader_handles_sparse_ids(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "sparse.db"
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    memory.init_db()
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "INSERT INTO messages (id, group_id, user_id, nickname, content, message_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (10, "1001", "1", "甲", "第一句", None, "2026-08-18 01:00:00"),
                (500000, "1001", "2", "乙", "第二句", "m2", "2026-08-18 01:01:00"),
                (1031958, "1001", "1", "甲", "第三句", "m3", "2026-08-18 01:02:00"),
                (1031959, "1002", "3", "丙", "其他群", "m4", "2026-08-18 01:03:00"),
            ],
        )

    async with memory.open_message_reader() as reader:
        before, after = await reader.fetch_message_context_sides(
            "1001",
            500000,
            before_limit=1,
            after_limit=1,
        )

    assert [row[0] for row in before] == [500000, 10]
    assert [row[0] for row in after] == [1031958]


@pytest.mark.asyncio
async def test_concurrent_message_writes_are_serialized(tmp_path: Path, monkeypatch):
    db_path = tmp_path / "concurrent.db"
    monkeypatch.setattr(memory, "DB_PATH", db_path)
    memory.init_db()

    await asyncio.gather(*(
        memory.record_message("1001", str(index), f"用户{index}", f"并发消息{index}", f"m{index}")
        for index in range(20)
    ))

    with sqlite3.connect(db_path) as conn:
        count, distinct_message_ids = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT message_id) FROM messages"
        ).fetchone()
    assert (count, distinct_message_ids) == (20, 20)
