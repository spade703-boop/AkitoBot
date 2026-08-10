"""Prompt 组装：人设、示例台词、歌曲记忆、关系文本等上下文片段的拼装与缓存。"""

import random

from nonebot.log import logger

from .data import (
    PJSK_ENTRIES,
    RELATIONSHIP_DATA,
    SCRIPT_DB,
    SONG_DATA,
    get_pjsk_intro,
    get_pjsk_knowledge_base,  # noqa: F401
    load_prompt_template,
)
from .retrieval import (
    RetrievalContext,
    build_retrieval_context,
    retrieve,  # noqa: F401
    retrieve_result,
)

# Backward-compatible re-export for tests/legacy patch points.
expand_query_for_retrieval = None


_SCRIPT_ATTRIBUTION_RULE = (
    "## 这些片段只用于学习彰人的语气和反应结构，不是当前场景的事实。\n"
    "## 必须保留原片段的人物归属与因果：先看冒号前的说话人，不得交换主语/宾语，"
    "不得把某人的成绩、经历、关系或行为移植给另一个人。与核心人设或关系档案冲突时，以后者为准。\n"
)


def _format_script_example(entry: dict, *, story_label: bool) -> str:
    """Format one script sample with an explicit Akito-speaker attribution lock."""
    fact_label = entry.get("cn_key") or entry.get("context") or "未标注情境"
    prefix = "【原作·语气参考】" if story_label else "【语气参考】"
    return (
        f"{prefix}事实标签：{fact_label}\n"
        f"  前情：{entry.get('context')}\n"
        f"  本条发言者：彰人\n"
        f"  彰人台词：{entry.get('dialogue')}"
    )


def get_random_examples(num: int = 5) -> str:
    """随机抽取 num 条参考剧本台词，拼成用于模仿语气的提示文本；无数据返回空串。"""
    pool = [s for s in SCRIPT_DB if s.get("type") != "noise"]
    if not pool:
        return ""
    samples = random.sample(pool, min(len(pool), num))
    lines = ["\n\n# 参考剧本（随机语气兜底）\n" + _SCRIPT_ATTRIBUTION_RULE]
    lines.extend(_format_script_example(sample, story_label=sample.get("type") == "story") for sample in samples)
    return "\n".join(lines)


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


def _iter_song_entries() -> list[dict]:
    """返回歌曲条目列表；数据缺失或结构异常时优雅降级为空列表。"""
    if not SONG_DATA:
        return []
    song_iterator = SONG_DATA.values() if isinstance(SONG_DATA, dict) else SONG_DATA
    return [entry for entry in song_iterator if isinstance(entry, dict)]


def _get_song_summary(entry: dict) -> str:
    """读取歌曲描述；优先 description，兼容旧字段回退。"""
    return (
        entry.get("description", "").strip()
        or entry.get("memory_trigger", "").strip()
        or entry.get("story_core", "").strip()
    )


def get_song_memories() -> str:
    """返回静态歌曲清单；具体点名某首歌时再注入对应详细记忆。"""
    song_names = []
    for entry in _iter_song_entries():
        song_name = entry.get("song_name", "").strip()
        if song_name:
            song_names.append(song_name)
    if not song_names:
        return ""
    return f"\n🎵【你会唱的歌】（被问到具体某首时会有详细记忆）：{'/'.join(song_names)}\n"


def get_song_mention(text: str) -> str:
    """命中歌曲关键词时，注入最多两首歌的完整记忆。"""
    if not text:
        return ""

    text_lower = text.lower()
    matched_lines = []
    for entry in _iter_song_entries():
        keywords = entry.get("keywords", [])
        if not isinstance(keywords, list):
            continue
        for kw in keywords:
            if not isinstance(kw, str) or not kw.strip():
                continue
            if kw.lower() not in text_lower:
                continue
            song_name = entry.get("song_name", "").strip()
            if not song_name:
                break
            summary = _get_song_summary(entry)
            matched_lines.append(f"- {song_name}：{summary}" if summary else f"- {song_name}")
            break
        if len(matched_lines) >= 2:
            break

    if not matched_lines:
        return ""
    return "\n🎵【歌曲话题】检测到在聊这些歌，回应时用上你的真实记忆：\n" + "\n".join(matched_lines) + "\n"


async def get_hybrid_relationship(text: str) -> str:
    """命中关系档案关键词时拼装本地认知提示；联网统一由 chat.py 调度。"""
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

    final_prompt = f'\n【检测到用户正在询问关于"{matched_name}"的话题】\n'
    final_prompt += f"📖【长期记忆库 (基础认知)】📖\n{local_info}\n"
    return final_prompt


# 查询扩散增强开关（出问题一键回退原行为）
_QUERY_EXPANSION_ENABLED = True


async def get_relevant_examples(query: str, num: int = 5, retrieval_ctx: RetrievalContext | None = None) -> str:
    """语义检索剧本示例；无相关命中时不注入，检索不可用时使用带归因锁的随机语气兜底。

    检索前用 LLM 扩散 query（游戏黑话翻含义 + 潜台词/情绪），
    原文 + 联想词 blend 后 embed，让 BGE-M3 突破字面屏障。
    story 条目（日文原作情境）用「原作·类似情境」格式标注前情与彰人台词，
    表头点明"体会语气/态度，用中文表达"。
    """
    ctx = retrieval_ctx
    if ctx is None and query and query.strip():
        ctx = await build_retrieval_context(query, enable_expansion=_QUERY_EXPANSION_ENABLED)
        if ctx.expanded_query:
            logger.debug(f"🔍 查询扩散: {query[:40]} → +{ctx.expanded_query[:60]}")

    result = await retrieve_result("scripts", ctx.query, num, ctx=ctx) if ctx and ctx.query.strip() else None
    if result is None or result.status == "unavailable":
        logger.debug(f"🔍 剧本检索不可用，回退带归因锁的随机语气样本 query={query[:40]}")
        return get_random_examples(num)
    if result.status != "hit":
        logger.debug(f"🔍 剧本检索无相关命中，跳过剧本注入 query={query[:40]}")
        return ""
    ids = result.ids

    relevant_ids = ids[:num]
    relevant = [SCRIPT_DB[i] for i in relevant_ids if 0 <= i < len(SCRIPT_DB)]

    logger.debug(f"🔍 剧本命中 [{len(relevant)}检索] query={query[:40]}")
    for entry in relevant:
        logger.debug(f"  [检索] type={entry.get('type','?')} {entry.get('context','')[:30]}")

    if not relevant:
        return ""

    header = (
        "\n\n# 参考剧本（语义匹配）\n"
        "## 以下为原作中类似情境下彰人的反应（日文原文），请只体会其语气/态度，**用中文表达**。\n"
        + _SCRIPT_ATTRIBUTION_RULE
    )
    lines = [header]
    for entry in relevant:
        lines.append(_format_script_example(entry, story_label=entry.get("type") == "story"))
    return "\n".join(lines)


async def get_relevant_pjsk(query: str, num: int = 6, retrieval_ctx: RetrievalContext | None = None) -> str:
    """语义检索 PJSK 黑话，三态注入；PJSK_INTRO 永远在前。

    检索前与剧本检索一致做 query 扩散 blend（黑话同形词如"开车"需要扩散词
    才能被 reranker 正确关联到词典体条目——评测实测 0.003 → 0.166）。
    检索不可用（None）→ 全量 base 兜底；精排判定无相关命中（[]）→ 仅注入前言（降噪）；
    命中 → 前言 + 相关条目。
    """
    ctx = retrieval_ctx
    if ctx is None and query and query.strip():
        ctx = await build_retrieval_context(query, enable_expansion=_QUERY_EXPANSION_ENABLED)
        if ctx.expanded_query:
            logger.debug(f"🔍 PJSK查询扩散: {query[:40]} → +{ctx.expanded_query[:60]}")

    if not PJSK_ENTRIES:
        return (get_pjsk_intro() or "").strip()

    lexical_hits: list[int] = []
    query_lower = (query or "").lower()
    if query_lower:
        for i, entry in enumerate(PJSK_ENTRIES):
            aliases = entry.get("aliases", [])
            if isinstance(aliases, list) and any(isinstance(alias, str) and alias.lower() in query_lower for alias in aliases):
                lexical_hits.append(i)
        if lexical_hits:
            lexical_hits = lexical_hits[:num]

    result = await retrieve_result("pjsk", ctx.query, num, ctx=ctx) if ctx and ctx.query.strip() else None
    if result is None or result.status == "unavailable":
        logger.debug(f"🔍 PJSK检索不可用，退到 intro-only query={query[:40]}")
        return (get_pjsk_intro() or "").strip()

    merged_ids: list[int] = []
    for idx in lexical_hits + result.ids:
        if idx not in merged_ids:
            merged_ids.append(idx)
        if len(merged_ids) >= num:
            break

    relevant = [PJSK_ENTRIES[i] for i in merged_ids if 0 <= i < len(PJSK_ENTRIES)]
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
        aliases = item.get("aliases", [])
        alias_text = f"（别名：{' / '.join(aliases)}）" if isinstance(aliases, list) and aliases else ""
        title = item.get("title") or item.get("category") or "PJSK"
        prompt_text = item.get("prompt_text") or item.get("text") or ""
        text += f"{title}{alias_text}：{prompt_text}\n"
    return text.strip()
