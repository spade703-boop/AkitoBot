"""记忆系统：长期记忆 JSON 的原子读写，以及基于 SQLite 的群聊上下文存取。"""

import json
import os
from pathlib import Path
import sqlite3

from nonebot.adapters import Event
from nonebot.log import logger

from . import DB_PATH

MEMORY_DB: dict = {}


def init_db():
    """创建 impression 历史记录表（由 core 统一初始化，供 impression.py 和 get_group_context 使用）。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            user_id TEXT,
            nickname TEXT,
            content TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    cursor.execute('''
        CREATE INDEX IF NOT EXISTS idx_messages_gid_uid ON messages(group_id, user_id)
    ''')
    conn.commit()
    conn.close()


init_db()


def load_memory():
    global MEMORY_DB
    possible_paths = [
        Path("/app/akito_bot/data/akito_memories.json"),
        Path("data/akito_memories.json"),
        Path("./akito_memories.json"),
    ]
    for path in possible_paths:
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    MEMORY_DB = json.load(f)
                logger.info(f"💾 长期记忆已加载！包含 {len(MEMORY_DB)} 个会话数据")
                return
            except Exception as e:
                logger.error(f"⚠️ 记忆文件损坏: {e}")
    logger.info("🆕 未找到记忆文件，初始化空记忆库。")
    MEMORY_DB = {}


def save_memory():
    try:
        if Path("/app/akito_bot/data").exists():
            target_path = Path("/app/akito_bot/data/akito_memories.json")
        else:
            target_path = Path("data/akito_memories.json")
            target_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = target_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(MEMORY_DB, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, target_path)
    except Exception as e:
        logger.error(f"❌ 记忆保存失败: {e}")


load_memory()


def get_memory_key(event: Event) -> str:
    group_id = getattr(event, "group_id", None)
    user_id = event.get_user_id()
    return f"group_{group_id}" if group_id else f"private_{user_id}"


def get_user_memory(unique_key: str) -> dict:
    if unique_key not in MEMORY_DB:
        MEMORY_DB[unique_key] = {"history": [], "temp_implants": []}
    return MEMORY_DB[unique_key]


def get_group_context(group_id: str, limit: int = 20) -> str:
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT nickname, content FROM messages WHERE group_id=? ORDER BY id DESC LIMIT ?",
            (str(group_id), limit),
        )
        rows = cursor.fetchall()
        conn.close()
        if not rows:
            return ""
        context_str = ""
        seen_bot_contents: set = set()
        bot_consecutive = 0
        for nickname, content in rows[::-1]:
            if nickname == "东云彰人":
                if content in seen_bot_contents or bot_consecutive >= 2:
                    continue
                seen_bot_contents.add(content)
                bot_consecutive += 1
            else:
                bot_consecutive = 0
            context_str += f"[{nickname}]: {content}\n"
        return context_str
    except Exception as e:
        logger.warning(f"⚠️ 读取群上下文失败: {e}")
        return ""
