"""记忆系统：长期记忆 JSON 的原子读写，以及基于 SQLite 的群聊上下文存取。"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
import datetime
import json
import os
import sqlite3
from typing import Optional, cast

import aiosqlite
from nonebot.adapters import Event
from nonebot.log import logger

from . import DB_PATH
from .data import find_data_path, get_data_dir
from .types import MemorySession, MessageRow

MEMORY_DB: dict[str, MemorySession] = {}
_MESSAGE_WRITE_LOCK = asyncio.Lock()


def init_db() -> None:
    """创建 impression 历史记录表（由 core 统一初始化，供 impression.py 和 get_group_context 使用）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as conn, conn:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_id TEXT,
                user_id TEXT,
                nickname TEXT,
                content TEXT,
                message_id TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(messages)").fetchall()}
        if "message_id" not in columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN message_id TEXT")
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_gid_uid ON messages(group_id, user_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_gid_timestamp ON messages(group_id, timestamp)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_gid_mid ON messages(group_id, message_id)
        ''')
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_messages_gid_id ON messages(group_id, id)
        ''')


init_db()


class MessageReader:
    """在单个异步 SQLite 连接上读取群消息。"""

    def __init__(self, connection: aiosqlite.Connection):
        self._connection = connection

    async def fetch_impression_history_candidates(
        self,
        group_id: str,
        target_id: str,
        *,
        limit: int,
    ) -> list[MessageRow]:
        async with self._connection.execute(
            "SELECT id, user_id, nickname, content, message_id, timestamp "
            "FROM messages WHERE group_id=? AND user_id=? AND length(content)>2 "
            "ORDER BY id DESC LIMIT ?",
            (group_id, target_id, limit),
        ) as cursor:
            return cast(list[MessageRow], await cursor.fetchall())

    async def fetch_recent_impression_candidates(
        self,
        group_id: str,
        target_id: str,
        *,
        cutoff: str,
        limit: int,
    ) -> list[MessageRow]:
        async with self._connection.execute(
            "SELECT id, user_id, nickname, content, message_id, timestamp "
            "FROM messages WHERE group_id=? AND user_id=? AND length(trim(content))>0 AND timestamp>=? "
            "ORDER BY id DESC LIMIT ?",
            (group_id, target_id, cutoff, limit),
        ) as cursor:
            return cast(list[MessageRow], await cursor.fetchall())

    async def fetch_message_context_sides(
        self,
        group_id: str,
        anchor_id: int,
        *,
        before_limit: int,
        after_limit: int,
    ) -> tuple[list[MessageRow], list[MessageRow]]:
        async with self._connection.execute(
            "SELECT id, user_id, nickname, content, message_id, timestamp "
            "FROM messages WHERE group_id=? AND id<=? ORDER BY id DESC LIMIT ?",
            (group_id, anchor_id, before_limit + 1),
        ) as cursor:
            before_rows = cast(list[MessageRow], await cursor.fetchall())
        async with self._connection.execute(
            "SELECT id, user_id, nickname, content, message_id, timestamp "
            "FROM messages WHERE group_id=? AND id>? ORDER BY id ASC LIMIT ?",
            (group_id, anchor_id, after_limit),
        ) as cursor:
            after_rows = cast(list[MessageRow], await cursor.fetchall())
        return before_rows, after_rows

    async def fetch_recent_impression_reply_contents(
        self,
        group_id: str,
        bot_id: str,
        *,
        limit: int,
    ) -> list[str]:
        async with self._connection.execute(
            "SELECT content FROM messages WHERE group_id=? AND user_id=? AND content LIKE '对%印象是%' "
            "ORDER BY id DESC LIMIT ?",
            (group_id, bot_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
        return [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]


@asynccontextmanager
async def open_message_reader() -> AsyncIterator[MessageReader]:
    """打开一个短生命周期的消息只读会话。"""
    async with aiosqlite.connect(DB_PATH) as connection:
        yield MessageReader(connection)


def load_memory() -> None:
    """从磁盘加载长期记忆到 MEMORY_DB（原地 clear+update，保持其他模块持有的引用不失效）。"""
    path = find_data_path("akito_memories.json")
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            MEMORY_DB.clear()
            MEMORY_DB.update(cast(dict[str, MemorySession], loaded))
            logger.info(f"💾 长期记忆已加载！包含 {len(MEMORY_DB)} 个会话数据")
            return
        except Exception as e:
            logger.error(f"⚠️ 记忆文件损坏: {e}")
    logger.info("🆕 未找到记忆文件，初始化空记忆库。")
    MEMORY_DB.clear()


def save_memory() -> None:
    """将 MEMORY_DB 原子写入磁盘（.tmp + os.replace），失败仅记日志不抛出。"""
    try:
        target_path = get_data_dir() / "akito_memories.json"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(MEMORY_DB, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target_path)
    except Exception as e:
        logger.error(f"❌ 记忆保存失败: {e}")


load_memory()


def get_memory_key(event: Event) -> str:
    """根据事件生成会话记忆键：群聊为 group_{id}，私聊为 private_{id}。"""
    group_id = getattr(event, "group_id", None)
    user_id = event.get_user_id()
    return f"group_{group_id}" if group_id else f"private_{user_id}"


def get_user_memory(unique_key: str) -> MemorySession:
    """取某会话的记忆字典，不存在时初始化 {"history": [], "temp_implants": []} 并返回。"""
    if unique_key not in MEMORY_DB:
        MEMORY_DB[unique_key] = {"history": [], "temp_implants": []}
    return MEMORY_DB[unique_key]


def parse_sqlite_timestamp(value: str) -> Optional[datetime.datetime]:
    """解析 SQLite 时间戳；无时区的 CURRENT_TIMESTAMP 按 UTC 处理。"""
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _relative_time_label(value: str, now: Optional[datetime.datetime] = None) -> str:
    """把 SQLite 时间戳转换为简短相对时间标签。"""
    parsed = parse_sqlite_timestamp(value)
    if parsed is None:
        return "时间未知"
    current = now or datetime.datetime.now(datetime.timezone.utc)
    age_seconds = max(0, int((current - parsed.astimezone(datetime.timezone.utc)).total_seconds()))
    if age_seconds < 60:
        return "刚刚"
    if age_seconds < 3600:
        return f"{age_seconds // 60}分钟前"
    return f"{age_seconds // 3600}小时前"


async def get_group_context(
    group_id: str,
    limit: int = 20,
    *,
    max_age_seconds: Optional[int] = None,
    max_gap_seconds: Optional[int] = None,
    exclude_message_id: Optional[str] = None,
    include_timestamps: bool = False,
) -> str:
    """读取群聊上下文，可按消息年龄、连续性和当前消息 ID 收紧范围。"""
    try:
        async with aiosqlite.connect(DB_PATH) as connection:
            clauses = ["group_id=?"]
            params: list[object] = [str(group_id)]
            if max_age_seconds is not None:
                cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=max_age_seconds)
                clauses.append("timestamp>=?")
                params.append(cutoff.strftime("%Y-%m-%d %H:%M:%S"))
            if exclude_message_id is not None:
                clauses.append("(message_id IS NULL OR message_id<>?)")
                params.append(str(exclude_message_id))
            params.append(limit)
            async with connection.execute(
                f"SELECT nickname, content, timestamp FROM messages WHERE {' AND '.join(clauses)} "
                "ORDER BY id DESC LIMIT ?",
                params,
            ) as cursor:
                rows = cast(list[tuple[str, str, str]], await cursor.fetchall())
        if not rows:
            return ""

        if max_gap_seconds is not None and len(rows) > 1:
            contiguous_rows = [rows[0]]
            newer_time = parse_sqlite_timestamp(rows[0][2])
            for row in rows[1:]:
                older_time = parse_sqlite_timestamp(row[2])
                if (
                    newer_time is not None
                    and older_time is not None
                    and (newer_time - older_time).total_seconds() > max_gap_seconds
                ):
                    break
                contiguous_rows.append(row)
                newer_time = older_time
            rows = contiguous_rows

        context_str = ""
        seen_bot_contents: set = set()
        bot_consecutive = 0
        for nickname, content, timestamp in rows[::-1]:
            if nickname == "东云彰人":
                if content in seen_bot_contents or bot_consecutive >= 2:
                    continue
                seen_bot_contents.add(content)
                bot_consecutive += 1
            else:
                bot_consecutive = 0
            time_label = f"[{_relative_time_label(timestamp)}]" if include_timestamps else ""
            context_str += f"{time_label}[{nickname}]: {content}\n"
        return context_str
    except Exception as e:
        logger.warning(f"⚠️ 读取群上下文失败: {e}")
        return ""


async def record_message(
    group_id: str,
    user_id: str,
    nickname: str,
    content: str,
    message_id: Optional[str] = None,
) -> None:
    """向共享群消息表写入一条消息。"""
    async with _MESSAGE_WRITE_LOCK, aiosqlite.connect(DB_PATH) as connection:
        await connection.execute(
            "INSERT INTO messages (group_id, user_id, nickname, content, message_id) VALUES (?, ?, ?, ?, ?)",
            (str(group_id), str(user_id), nickname, content, message_id),
        )
        await connection.commit()


async def delete_group_messages(group_id: str) -> int:
    """删除指定群的全部背景消息并返回删除行数。"""
    async with _MESSAGE_WRITE_LOCK, aiosqlite.connect(DB_PATH) as connection, connection.execute(
        "DELETE FROM messages WHERE group_id=?", (str(group_id),)
    ) as cursor:
        deleted = cursor.rowcount
        await connection.commit()
    return int(deleted)


async def record_bot_message(group_id: str, content: str, bot_qq: str = "") -> None:
    """把 bot 自己的回复写入共享 SQLite 群日志（nickname 统一为「东云彰人」）。

    供 get_group_context 跨引擎读取——主动对话与随机插嘴据此互相「看见」对方说过的话。
    """
    if not content or not content.strip():
        return
    try:
        await record_message(group_id, bot_qq, "东云彰人", content)
    except Exception as e:
        logger.error(f"❌ 记录 bot 回复到群日志失败: {e}")
