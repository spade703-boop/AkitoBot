"""Prompt 组装：人设、示例台词、歌曲记忆、关系文本等上下文片段的拼装与缓存。"""

import random

from nonebot.log import logger

from .api import expand_query_for_retrieval, smart_search
from .data import (
    PJSK_ENTRIES,
    RELATIONSHIP_DATA,
    SCRIPT_DB,
    SONG_DATA,
    get_pjsk_intro,
    get_pjsk_knowledge_base,
    load_prompt_template,
)
from .retrieval import retrieve


def get_random_examples(num: int = 5) -> str:
    """随机抽取 num 条参考剧本台词，拼成用于模仿语气的提示文本；无数据返回空串。"""
    pool = [s for s in SCRIPT_DB if s.get("type") != "noise"]
    if not pool:
        return ""
    samples = random.sample(pool, min(len(pool), num))
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


# 语义检索过渡比例：top-(num-1) 相关 + 1 随机（story 纳入后相关池更丰富，降随机比例）
_RELEVANT_RATIO = 1  # 保留的随机条数（其余来自语义检索）

# 查询扩散增强开关（出问题一键回退原行为）
_QUERY_EXPANSION_ENABLED = True


async def get_relevant_examples(query: str, num: int = 5) -> str:
    """语义检索剧本示例；检索不可用（None）或精排判定无相关命中（[]）时回退到随机抽取。

    检索前用 LLM 扩散 query（游戏黑话翻含义 + 潜台词/情绪），
    原文 + 联想词 blend 后 embed，让 BGE-M3 突破字面屏障。
    story 条目（日文原作情境）用「原作·类似情境」格式标注前情与彰人台词，
    表头点明"体会语气/态度，用中文表达"。
    """
    retrieval_query = query
    if _QUERY_EXPANSION_ENABLED and query and len(query.strip()) >= 3:
        expanded = await expand_query_for_retrieval(query)
        if expanded:
            retrieval_query = f"{query} {expanded}"
            logger.debug(f"🔍 查询扩散: {query[:40]} → +{expanded[:60]}")

    ids = await retrieve("scripts", retrieval_query, num) if retrieval_query.strip() else None
    if not ids:
        # None=检索不可用；[]=精排判定全部不相关——均回退随机抽取（与旧兜底一致）
        logger.debug(f"🔍 剧本检索无果，回退随机抽取 query={query[:40]}")
        return get_random_examples(num)

    # 取 top-(num-1) 相关 + 1 随机
    relevant_count = max(0, num - _RELEVANT_RATIO)
    relevant_ids = ids[:relevant_count]
    random_count = min(num - len(relevant_ids), len(SCRIPT_DB))

    relevant = [SCRIPT_DB[i] for i in relevant_ids if 0 <= i < len(SCRIPT_DB)]
    rand_sources: list[int] = []  # 记录哪些是随机来的
    if random_count > 0:
        remaining = [
            i for i in range(len(SCRIPT_DB))
            if i not in relevant_ids and SCRIPT_DB[i].get("type") != "noise"
        ]
        if remaining:
            rand_sources = random.sample(remaining, min(random_count, len(remaining)))
            relevant += [SCRIPT_DB[i] for i in rand_sources]

    # 调试日志：每条来源 + 类型 + 前 30 字
    logger.debug(
        f"🔍 剧本命中 [{len(relevant_ids)}检索+{len(rand_sources)}随机] query={query[:40]}"
    )
    for i, s in enumerate(relevant):
        src = "检索" if i < len(relevant_ids) else "随机"
        logger.debug(f"  [{src}] type={s.get('type','?')} {s.get('context','')[:30]}")

    if not relevant:
        return get_random_examples(num)

    header = (
        "\n\n# 参考剧本 (语义匹配 + 随机注入)\n"
        "## 以下为原作中类似情境下彰人的反应（日文原文），请体会其语气/态度，**用中文表达**\n"
    )
    lines = [header]
    for s in relevant:
        tp = s.get("type", "")
        if tp == "story":
            lines.append(f"【原作·类似情境】前情：{s.get('context')}\n  彰人：{s.get('dialogue')}")
        else:
            lines.append(f"- 情境：{s.get('context')}\n  台词：{s.get('dialogue')}")
    return "\n".join(lines)


async def get_relevant_pjsk(query: str, num: int = 6) -> str:
    """语义检索 PJSK 黑话，三态注入；PJSK_INTRO 永远在前。

    检索前与剧本检索一致做 query 扩散 blend（黑话同形词如"开车"需要扩散词
    才能被 reranker 正确关联到词典体条目——评测实测 0.003 → 0.166）。
    检索不可用（None）→ 全量 base 兜底；精排判定无相关命中（[]）→ 仅注入前言（降噪）；
    命中 → 前言 + 相关条目。
    """
    retrieval_query = query or ""
    if _QUERY_EXPANSION_ENABLED and query and len(query.strip()) >= 3:
        expanded = await expand_query_for_retrieval(query)
        if expanded:
            retrieval_query = f"{query} {expanded}"
            logger.debug(f"🔍 PJSK查询扩散: {query[:40]} → +{expanded[:60]}")

    ids = await retrieve("pjsk", retrieval_query, num) if retrieval_query.strip() else None
    if ids is None or not PJSK_ENTRIES:
        logger.debug(f"🔍 PJSK检索不可用，回退全量 base query={query[:40]}")
        return get_pjsk_knowledge_base()

    relevant = [PJSK_ENTRIES[i] for i in ids if 0 <= i < len(PJSK_ENTRIES)]
    if not relevant:
        # 检索可用但精排判定无任何相关条目 → 刻意降噪：仅注入语境锁前言，不再全量灌注
        logger.debug(f"🔍 PJSK无相关命中，仅注入前言 query={query[:40]}")
        return (get_pjsk_intro() or "").strip()

    logger.debug(f"🔍 PJSK命中 [{len(relevant)}条] query={query[:40]}")
    for item in relevant:
        logger.debug(f"  [PJSK] {item.get('category','')[:20]} {item.get('text','')[:40]}")

    intro = get_pjsk_intro() or ""
    text = intro + "\n\n"
    for item in relevant:
        text += f"{item['category']}：{item['text']}\n"
    return text.strip()
