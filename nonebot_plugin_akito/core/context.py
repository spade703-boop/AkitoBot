"""Prompt 组装：人设、示例台词、歌曲记忆、关系文本等上下文片段的拼装与缓存。"""

import random

from nonebot.log import logger

from .api import smart_search
from .data import (
    PJSK_ENTRIES,
    RELATIONSHIP_DATA,
    SCRIPT_DB,
    SONG_DATA,
    get_pjsk_intro,
    get_pjsk_knowledge_base,
    load_prompt_template,
)
from .retrieval import RetrievalContext, build_retrieval_context, retrieve, retrieve_result

# Backward-compatible re-export for tests/legacy patch points.
expand_query_for_retrieval = None


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


async def get_relevant_examples(query: str, num: int = 5, retrieval_ctx: RetrievalContext | None = None) -> str:
    """语义检索剧本示例；检索不可用（None）或精排判定无相关命中（[]）时回退到随机抽取。

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
    if result is None or result.status != "hit":
        # 不可用或无相关命中均回退随机抽取；只有 no_hit 不再注入随机混合样本头
        logger.debug(f"🔍 剧本检索无果，回退随机抽取 query={query[:40]}")
        return get_random_examples(num)
    ids = result.ids

    # 高置信命中时不再固定掺随机；只有纯 cosine 回退时保留少量随机兜底。
    random_ratio = _RELEVANT_RATIO if result.fell_back_to_cosine else 0
    relevant_count = max(0, num - random_ratio)
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
        "\n\n# 参考剧本 (语义匹配"
        + (" + 随机注入" if random_ratio else "")
        + ")\n"
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
