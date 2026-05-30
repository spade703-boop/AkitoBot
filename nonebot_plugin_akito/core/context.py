"""Prompt 组装：人设、示例台词、歌曲记忆、关系文本等上下文片段的拼装与缓存。"""

import random

from nonebot.log import logger

from .api import smart_search
from .data import RELATIONSHIP_DATA, SCRIPT_DB, SONG_DATA, load_prompt_template


def get_random_examples(num: int = 5) -> str:
    """随机抽取 num 条参考剧本台词，拼成用于模仿语气的提示文本；无数据返回空串。"""
    if not SCRIPT_DB:
        return ""
    samples = random.sample(SCRIPT_DB, min(len(SCRIPT_DB), num))
    text = "\n\n# 参考剧本 (请严格模仿以下台词的语气、长短和用词)\n"
    for s in samples:
        text += f"- 情境：{s.get('context')}\n  台词：{s.get('dialogue')}\n"
    return text


_PERSONA_CACHE: str = ""


def get_base_persona() -> str:
    """返回人设文本（带进程内缓存）；缺人设文件时返回兜底文本。"""
    global _PERSONA_CACHE
    if _PERSONA_CACHE:
        return _PERSONA_CACHE
    text = load_prompt_template("akito_persona.txt")
    if text:
        _PERSONA_CACHE = text
        return _PERSONA_CACHE
    return "你现在是东云彰人。（警告：未找到人设文件）"


def reload_persona() -> str:
    """强制重新从磁盘加载人设文件（用于热更新）。"""
    global _PERSONA_CACHE
    _PERSONA_CACHE = ""
    return get_base_persona()


def get_song_memories() -> str:
    """将歌曲数据格式化为背景知识条目，供系统 Prompt 静态注入。

    优先读取 description 字段（为新格式专门设计的简洁描述）；
    若不存在则依次回退到 memory_trigger、story_core（兼容旧格式）。
    """
    if not SONG_DATA:
        return ""
    song_iterator = SONG_DATA.values() if isinstance(SONG_DATA, dict) else SONG_DATA
    lines = []
    for entry in song_iterator:
        song_name = entry.get("song_name", "")
        if not song_name:
            continue
        summary = (
            entry.get("description", "").strip()
            or entry.get("memory_trigger", "").strip()
            or entry.get("story_core", "").strip()
        )
        if summary and len(summary) > 120:
            summary = summary[:120] + "……"
        lines.append(f"- {song_name}：{summary}" if summary else f"- {song_name}")
    if not lines:
        return ""
    return "🎵【你的歌曲记忆】（有人问起时自然回应，无需主动发挥）：\n" + "\n".join(lines)


async def get_hybrid_relationship(text: str) -> str:
    """命中关系档案关键词时，拼装「本地认知 +（提问时）网络搜索」的关系提示；未命中返回空串。"""
    text_lower = text.lower()

    # --- Step 1: 本地关键词白名单扫描 ---
    # 直接遍历 RELATIONSHIP_DATA，只认 JSON 里明确登记的角色名/别名
    matched_entry = None
    matched_name = ""
    if RELATIONSHIP_DATA:
        for entry in RELATIONSHIP_DATA:
            keywords = entry.get("keywords", [])
            for kw in keywords:
                if kw.lower() in text_lower:
                    matched_entry = entry
                    matched_name = kw
                    break
            if matched_entry:
                break

    if not matched_entry:
        return ""

    local_info = matched_entry.get("content", "")
    if not local_info:
        return ""

    # --- Step 3: 仅在明确提问时才触发网络搜索 ---
    question_markers = ["吗", "呢", "吧", "？", "?", "怎么", "怎样", "如何", "是什么", "哪里", "多少", "为什么", "谁"]
    should_search = any(m in text for m in question_markers)

    web_info = ""
    if should_search:
        logger.info(f"🔍 关系档案触发网搜: [{matched_name}]")
        web_info = await smart_search(f"Project Sekai 东云彰人 {matched_name} 关系 剧情互动 评价")

    final_prompt = f'\n【检测到用户正在询问关于"{matched_name}"的话题】\n'
    final_prompt += f"📖【长期记忆库 (基础认知)】📖\n{local_info}\n"
    if web_info:
        final_prompt += f"🔍【补充情报 (网络搜索结果)】🔍\n{web_info}\n"
    return final_prompt
