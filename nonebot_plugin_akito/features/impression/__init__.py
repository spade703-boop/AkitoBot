"""群印象：默默记录群聊到 SQLite、生成「群印象」评价、低概率随机插嘴。"""

import asyncio
from dataclasses import asdict, dataclass
import datetime
import difflib
import json
import random
import re
import time
from typing import Optional

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from ...core import (
    ALLOWED_CP_GROUPS,
    MessageReader,
    MessageRow,
    PROMPTS_DB,
    RELATIONSHIP_DATA,
    TZ_CN,
    build_event_memory_context,
    build_shared_prompt_context,
    call_deepseek_api,
    get_base_persona,
    get_daily_activity,
    get_group_context,
    get_safe_until,
    get_toya_anchor,
    get_user_memory,
    is_sleeping,
    load_prompt_template,
    open_message_reader,
    parse_json_object,
    parse_sqlite_timestamp,
    record_bot_message,
    evaluate_auto_reply_shadow,
    record_auto_reply_shadow,
    record_context_shadow,
    record_event_memory,
    record_message,
    render_auto_chat_prompt,
    render_impression_analysis_prompt,
    render_impression_reply_prompt,
    rescue_field,
    rescue_tail_after_field,
    finish_turn_trace,
    new_request_id,
    record_context_sources,
    record_intent,
    record_parse_result,
    record_rollout,
    mode_is_active,
    resolve_rollout,
    select_context_for_mode,
    set_trace_stage,
    start_turn_trace,
)

# ================= 配置区域 =================

MODEL_NAME = "deepseek-v4-flash"

AUTO_CHAT_GROUPS = ALLOWED_CP_GROUPS

CHAT_PROBABILITY = 0.03

IMPRESSION_HISTORY_LIMIT = 50
IMPRESSION_HISTORY_SCAN_LIMIT = 150
IMPRESSION_RECENT_DAYS = 14
IMPRESSION_RECENT_TARGET_LIMIT = 20
IMPRESSION_RECENT_SCAN_LIMIT = 60
IMPRESSION_CONTEXT_SIDE_LIMIT = 2
IMPRESSION_CONTEXT_MAX_GAP_SECONDS = 300
IMPRESSION_CONTEXT_MAX_BLOCKS = 6
IMPRESSION_CONTEXT_BLOCK_MESSAGE_LIMIT = 12
IMPRESSION_MESSAGE_CHAR_LIMIT = 240
IMPRESSION_LIMITED_MAX_LENGTH = 120
IMPRESSION_SPECIFIC_MAX_LENGTH = 180
IMPRESSION_RECENT_REPLY_LIMIT = 8
IMPRESSION_SIMILARITY_THRESHOLD = 0.72
IMPRESSION_ENDING_SIMILARITY_THRESHOLD = 0.82
IMPRESSION_CANDIDATE_COUNT = 3
IMPRESSION_COMMANDS = ("群印象", "评价我", "说说印象", "我的印象")
IMPRESSION_SELF_ALIASES = ("东云彰人", "彰人", "Akito", "akito", "小彰", "akt")

IMPRESSION_STALE_PATTERNS = (
    re.compile(r"普通(?:人|网友|玩家)"),
    re.compile(r"没什么(?:特别|值得)[^。！？]{0,12}(?:的|说)"),
    re.compile(r"(?:挺|比较)(?:随和|好相处)"),
    re.compile(r"也就那样"),
)

IMPRESSION_UNCERTAIN_PATTERN = re.compile(r"(?:暂时|目前|现在|还没|看不准|说不好|只看得出|能看出的|先不下结论)")

BLOCK_PREFIXES = ["/", "#", ".", "!", "！", "*", "-", "@"]
BLOCK_KEYWORDS = [
    "签到", "打卡", "个人信息", "日速", "时速", "help",
    "pjsk", "抽签", "娶群友", "透群友", "看看",
    "cn", "sn", "绑定", "解绑", "倍率",
    "存", "收下", "这是", "投喂", "增加",
    "开始进货", "停止进货", "开始收图",
    "看你的", "发张", "来张",
    "图库", "清单", "库存",
    "冬弥呢", "搭档呢", "冬弥在哪",
    "植入", "清除", "遗忘", "重置"
]

# ===========================================

@dataclass(frozen=True)
class ImpressionAnalysis:
    mode: str
    evidence: tuple[str, ...]
    observations: tuple[str, ...]
    uncertainties: tuple[str, ...]
    avoid_patterns: tuple[str, ...]


@dataclass(frozen=True)
class ImpressionStyleScore:
    total: float
    full: float
    ending: float
    clause: float


@dataclass(frozen=True)
class ImpressionCandidateEvaluation:
    index: int
    reply: str
    score: ImpressionStyleScore
    issue: str


@dataclass(frozen=True)
class ImpressionRelationshipReference:
    label: str
    matched_aliases: tuple[str, ...]
    content: str


def _normalize_impression_entity_text(value: str) -> str:
    return re.sub(r"\s+", "", value or "").casefold()


def _collect_impression_relationship_references(material_text: str) -> tuple[ImpressionRelationshipReference, ...]:
    normalized_material = _normalize_impression_entity_text(material_text)
    if not normalized_material:
        return ()

    candidates: list[tuple[int, int, int, str, object]] = []
    for entry_index, entry in enumerate(RELATIONSHIP_DATA):
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content", "")).strip()
        raw_keywords = entry.get("keywords", [])
        if not content or not isinstance(raw_keywords, list):
            continue
        for raw_keyword in raw_keywords:
            keyword = str(raw_keyword).strip()
            normalized_keyword = _normalize_impression_entity_text(keyword)
            if not normalized_keyword:
                continue
            start = normalized_material.find(normalized_keyword)
            while start >= 0:
                candidates.append((start, -len(normalized_keyword), entry_index, keyword, entry))
                start = normalized_material.find(normalized_keyword, start + 1)

    accepted_spans: list[tuple[int, int]] = []
    accepted: dict[int, tuple[dict, list[str]]] = {}
    for start, negative_length, entry_index, keyword, entry in sorted(candidates, key=lambda item: item[:4]):
        end = start - negative_length
        if any(start < accepted_end and end > accepted_start for accepted_start, accepted_end in accepted_spans):
            continue
        accepted_spans.append((start, end))
        if entry_index not in accepted:
            accepted[entry_index] = (entry, [])
        if keyword not in accepted[entry_index][1]:
            accepted[entry_index][1].append(keyword)

    references: list[ImpressionRelationshipReference] = []
    self_aliases = tuple(
        alias for alias in IMPRESSION_SELF_ALIASES
        if _normalize_impression_entity_text(alias) in normalized_material
    )
    if self_aliases:
        references.append(
            ImpressionRelationshipReference(
                label="东云彰人（你自己）",
                matched_aliases=self_aliases,
                content=(
                    "材料中的这些称呼指向你本人，不是目标之外的第三方角色。"
                    "目标群友提到彰人时，应理解为她/他正在谈到你、你们之间的互动或对你的看法。"
                ),
            )
        )

    for entry, matched_aliases in (accepted[index] for index in sorted(accepted)):
        label_match = re.search(r"【关系：([^】]+)", str(entry.get("content", "")))
        label = label_match.group(1).strip() if label_match else matched_aliases[0]
        references.append(
            ImpressionRelationshipReference(
                label=label,
                matched_aliases=tuple(matched_aliases),
                content=str(entry["content"]).strip(),
            )
        )
    return tuple(references)


def _build_impression_relationship_context(material_text: str) -> str:
    references = _collect_impression_relationship_references(material_text)
    lines = [
        "【群印象人物指代与关系资料】",
        "材料中的角色名称必须先按以下归因处理；不要把这些人物当成与彰人毫无关系的普通第三方。",
        "东云彰人相关别名指向彰人自己；其他命中的角色是彰人认识的人，态度必须服从对应关系资料。",
    ]
    if not references:
        lines.append("本次材料未命中可用的自我别名或关系资料；不要凭空补充角色关系。")
        return "\n".join(lines)
    for reference in references:
        aliases = "、".join(reference.matched_aliases)
        lines.extend(
            [
                f"\n【识别人物：{reference.label}；材料命中：{aliases}】",
                reference.content,
            ]
        )
    return "\n".join(lines)


def _resolve_impression_target(event: GroupMessageEvent, bot_self_id: str) -> tuple[str, str, bool, bool]:
    """Resolve whose impression is being requested.

    Returns:
        target_id, target_name, is_querying_other, is_querying_bot
    """
    sender_id = str(event.user_id)
    sender_name = event.sender.card or event.sender.nickname
    target_id = sender_id
    target_name = sender_name
    is_querying_other = False

    for seg in event.original_message:
        if seg.type == "at":
            target_id = str(seg.data["qq"])
            if target_id != "all":
                is_querying_other = True
            break

    is_querying_bot = is_querying_other and target_id == str(bot_self_id)
    return target_id, target_name, is_querying_other, is_querying_bot


def _format_message_time(value: str) -> str:
    parsed = parse_sqlite_timestamp(value)
    if parsed is None:
        return "时间未知"
    return parsed.astimezone(TZ_CN).strftime("%m-%d %H:%M")


def _truncate_impression_content(content: str) -> str:
    content = " ".join(content.split())
    if len(content) <= IMPRESSION_MESSAGE_CHAR_LIMIT:
        return content
    return content[:IMPRESSION_MESSAGE_CHAR_LIMIT].rstrip() + "…"


def _is_impression_noise(content: str) -> bool:
    text = " ".join(content.split()).strip()
    if not text:
        return True
    if any(text == command or text.startswith(f"{command} ") for command in IMPRESSION_COMMANDS):
        return True
    return any(text.startswith(prefix) for prefix in BLOCK_PREFIXES)


def _is_current_impression_message(row: MessageRow, current_message_id: str) -> bool:
    return bool(current_message_id and row[4] is not None and str(row[4]) == current_message_id)


def _select_target_rows(
    rows: list[MessageRow],
    *,
    current_message_id: str,
    limit: int,
    min_content_length: int,
    max_repeats: Optional[int],
) -> list[MessageRow]:
    selected: list[MessageRow] = []
    content_counts: dict[str, int] = {}
    for row in rows:
        content = row[3].strip()
        if _is_current_impression_message(row, current_message_id):
            continue
        if len(content) < min_content_length or _is_impression_noise(content):
            continue
        normalized = "".join(content.split())
        if max_repeats is not None and content_counts.get(normalized, 0) >= max_repeats:
            continue
        content_counts[normalized] = content_counts.get(normalized, 0) + 1
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _build_impression_history_text(rows: list[MessageRow], target_name: str) -> str:
    """Render target-only history into chronological prompt order with timestamps."""
    return "\n".join(
        f"[{_format_message_time(row[5])}]【{target_name}】: {_truncate_impression_content(row[3])}"
        for row in rows[::-1]
    )


async def _load_context_window(
    reader: MessageReader,
    *,
    group_id: str,
    target_row: MessageRow,
    current_message_id: str,
) -> list[MessageRow]:
    before_rows, after_rows = await reader.fetch_message_context_sides(
        group_id,
        target_row[0],
        before_limit=IMPRESSION_CONTEXT_SIDE_LIMIT,
        after_limit=IMPRESSION_CONTEXT_SIDE_LIMIT,
    )

    target_time = parse_sqlite_timestamp(target_row[5])
    window: list[MessageRow] = []
    for row in list(reversed(before_rows)) + after_rows:
        if _is_current_impression_message(row, current_message_id) or _is_impression_noise(row[3]):
            continue
        row_time = parse_sqlite_timestamp(row[5])
        if (
            target_time is not None
            and row_time is not None
            and abs((row_time - target_time).total_seconds()) > IMPRESSION_CONTEXT_MAX_GAP_SECONDS
        ):
            continue
        window.append(row)
    return window


def _merge_context_windows(windows: list[list[MessageRow]]) -> list[list[MessageRow]]:
    ordered_windows = sorted(
        (sorted(window, key=lambda row: row[0]) for window in windows if window), key=lambda rows: rows[0][0]
    )
    merged: list[list[MessageRow]] = []
    for window in ordered_windows:
        if not merged:
            merged.append(window)
            continue

        previous = merged[-1]
        previous_time = parse_sqlite_timestamp(previous[-1][5])
        current_time = parse_sqlite_timestamp(window[0][5])
        overlaps = window[0][0] <= previous[-1][0]
        gap_is_close = (
            previous_time is not None
            and current_time is not None
            and 0 <= (current_time - previous_time).total_seconds() <= IMPRESSION_CONTEXT_MAX_GAP_SECONDS
        )
        if overlaps or gap_is_close:
            combined = {row[0]: row for row in previous}
            combined.update({row[0]: row for row in window})
            merged[-1] = sorted(combined.values(), key=lambda row: row[0])
        else:
            merged.append(window)
    return merged[-IMPRESSION_CONTEXT_MAX_BLOCKS:]


async def _load_impression_material(
    reader: MessageReader,
    *,
    group_id: str,
    target_id: str,
    current_message_id: str,
    now: Optional[datetime.datetime] = None,
) -> tuple[list[MessageRow], list[list[MessageRow]]]:
    history_candidates = await reader.fetch_impression_history_candidates(
        group_id,
        target_id,
        limit=IMPRESSION_HISTORY_SCAN_LIMIT,
    )
    history_rows = _select_target_rows(
        history_candidates,
        current_message_id=current_message_id,
        limit=IMPRESSION_HISTORY_LIMIT,
        min_content_length=3,
        max_repeats=2,
    )

    current = now or datetime.datetime.now(datetime.timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.timezone.utc)
    cutoff = current.astimezone(datetime.timezone.utc) - datetime.timedelta(days=IMPRESSION_RECENT_DAYS)
    recent_candidates = await reader.fetch_recent_impression_candidates(
        group_id,
        target_id,
        cutoff=cutoff.strftime("%Y-%m-%d %H:%M:%S"),
        limit=IMPRESSION_RECENT_SCAN_LIMIT,
    )
    recent_target_rows = _select_target_rows(
        recent_candidates,
        current_message_id=current_message_id,
        limit=IMPRESSION_RECENT_TARGET_LIMIT,
        min_content_length=1,
        max_repeats=None,
    )
    windows = []
    for row in recent_target_rows:
        windows.append(
            await _load_context_window(
                reader,
                group_id=group_id,
                target_row=row,
                current_message_id=current_message_id,
            )
        )
    return history_rows, _merge_context_windows(windows)


def _build_impression_context_text(
    blocks: list[list[MessageRow]],
    *,
    target_id: str,
    target_name: str,
) -> str:
    sections: list[str] = []
    for index, block in enumerate(blocks, start=1):
        display_rows = block[-IMPRESSION_CONTEXT_BLOCK_MESSAGE_LIMIT:]
        lines = [f"【近期对话片段 {index}】"]
        for row in display_rows:
            nickname = target_name if row[1] == target_id else row[2]
            lines.append(f"[{_format_message_time(row[5])}][{nickname}]: {_truncate_impression_content(row[3])}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections) if sections else "（最近没有形成可还原的连续对话片段）"


def _build_target_evidence_source(
    history_rows: list[MessageRow],
    blocks: list[list[MessageRow]],
    *,
    target_id: str,
) -> str:
    contents = [row[3] for row in history_rows]
    contents.extend(row[3] for block in blocks for row in block if row[1] == target_id)
    return "\n".join(_truncate_impression_content(content) for content in contents)


def _resolve_wl2_overlay(mem: dict, now_ts: Optional[float] = None) -> tuple[bool, str]:
    current_ts = time.time() if now_ts is None else now_ts
    for item in reversed(mem.get("temp_implants", [])):
        if item.get("id") != "WL2":
            continue
        expire_at = item.get("expire_at", item.get("expire_time"))
        if expire_at is not None and expire_at <= current_ts:
            continue
        return True, str(item.get("content", "")).strip()
    return False, ""


def _build_impression_analysis_system_prompt(*, target_name: str, relationship_context: str = "") -> str:
    return render_impression_analysis_prompt(
        target_name=target_name,
        recent_reply_limit=IMPRESSION_RECENT_REPLY_LIMIT,
        relationship_context=relationship_context,
    )


def _build_impression_reply_system_prompt(
    *,
    persona: str,
    state_overlay_prompt: str,
    target_name: str,
    is_querying_other: bool,
    relationship_context: str = "",
    event_memory: str = "",
) -> str:
    return render_impression_reply_prompt(
        persona=persona,
        state_overlay_prompt=state_overlay_prompt,
        target_name=target_name,
        is_querying_other=is_querying_other,
        specific_max_length=IMPRESSION_SPECIFIC_MAX_LENGTH,
        limited_max_length=IMPRESSION_LIMITED_MAX_LENGTH,
        candidate_count=IMPRESSION_CANDIDATE_COUNT,
        relationship_context=relationship_context,
        event_memory=event_memory,
    )


def _build_auto_chat_system_prompt(
    *,
    persona: str,
    time_str: str,
    toya_anchor: str,
    scene_desc: str,
    group_context: str,
    relation_info: str,
    song_info: str,
    script_examples: str,
    pjsk_block: str,
    cool_guy_filter: str,
    task_logic: str,
    inner_os_guide: str,
    event_memory: str = "",
) -> str:
    """Compatibility wrapper for the core auto-chat renderer."""
    return render_auto_chat_prompt(
        persona=persona,
        time_str=time_str,
        toya_anchor=toya_anchor,
        scene_desc=scene_desc,
        group_context=group_context,
        relation_info=relation_info,
        song_info=song_info,
        script_examples=script_examples,
        pjsk_block=pjsk_block,
        cool_guy_filter=cool_guy_filter,
        task_logic=task_logic,
        inner_os_guide=inner_os_guide,
        event_memory=event_memory,
    )


def _parse_string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return ()


def _parse_impression_analysis(raw_result: str) -> Optional[ImpressionAnalysis]:
    try:
        response_data = parse_json_object(raw_result)
        if response_data is None:
            raise json.JSONDecodeError("invalid json object", raw_result, 0)
        return ImpressionAnalysis(
            mode=str(response_data.get("mode", "")).strip().lower(),
            evidence=_parse_string_tuple(response_data.get("evidence", [])),
            observations=_parse_string_tuple(response_data.get("observations", [])),
            uncertainties=_parse_string_tuple(response_data.get("uncertainties", [])),
            avoid_patterns=_parse_string_tuple(response_data.get("avoid_patterns", [])),
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning(f"⚠️ [Impression] 材料分析未输出标准 JSON: {raw_result[:120]}")
        return None


def _parse_impression_candidates(raw_result: str) -> tuple[str, list[str]]:
    inner_os = ""
    candidates: tuple[str, ...] = ()
    try:
        response_data = parse_json_object(raw_result)
        if response_data is None:
            raise json.JSONDecodeError("invalid json object", raw_result, 0)
        inner_os = str(response_data.get("inner_os", "")).strip()
        candidates = _parse_string_tuple(response_data.get("replies", []))
        if not candidates:
            candidates = _parse_string_tuple(response_data.get("reply", ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning(f"⚠️ [Impression] 表达阶段未输出标准 JSON: {raw_result[:120]}")
        rescued = rescue_field(raw_result, "reply")
        if rescued is None:
            rescued = rescue_tail_after_field(raw_result, "inner_os")
        if rescued is not None and rescued.strip():
            candidates = (rescued.strip().strip('"'),)

    unique_candidates = list(dict.fromkeys(candidate for candidate in candidates if candidate))
    return inner_os, unique_candidates


def _find_impression_style_issue(reply: str) -> str:
    for pattern in IMPRESSION_STALE_PATTERNS:
        match = pattern.search(reply)
        if match:
            return f"reply 使用了泛化套话: {match.group(0)!r}"
    return ""


def _extract_impression_body(reply: str) -> str:
    return re.sub(r"^对.+?的印象是[：:…。.．\s]*", "", reply.strip())


def _find_impression_addressing_issue(reply: str, *, is_querying_other: bool) -> str:
    body = _extract_impression_body(reply)
    if not body:
        return ""

    sentence_prefix = r"(?:^|[。！？!?；;…])\s*"
    subject_continuation = r"(?=[，,。！？!?；;、\s]|最近|平时|总|也|还|倒|对|把|会|是|有|没|很|真|就|都|嘴|想|说|聊|玩|抽)"
    distant_reference = re.search(
        sentence_prefix + r"(?:他这个人|她这个人|这个人|这人|这家伙)" + subject_continuation,
        body,
    )
    if distant_reference:
        return f"reply 对目标使用了旁观称呼: {distant_reference.group(0)!r}"

    sentence_subject = sentence_prefix + r"(?:{})" + subject_continuation
    if is_querying_other:
        direct_reference = re.search(sentence_subject.format("你"), body)
        if direct_reference:
            return "reply 把被艾特的目标写成了当前对话者“你”"
        masculine_reference = re.search(sentence_subject.format("他"), body)
        if masculine_reference:
            return "reply 对被艾特的目标使用了男性第三人称“他”"
    else:
        third_person_reference = re.search(sentence_subject.format("他|她"), body)
        if third_person_reference:
            return f"reply 没有直接对本人使用“你”: {third_person_reference.group(0).strip()!r}"
    return ""


def _normalize_impression_body(reply: str) -> str:
    body = _extract_impression_body(reply)
    return _normalize_impression_text(body)


def _normalize_impression_text(value: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value).lower()


def _split_impression_clauses(reply: str) -> list[str]:
    body = _extract_impression_body(reply)
    return [
        normalized
        for clause in re.split(r"[，,。！？!?；;…\n]+", body)
        if len(normalized := _normalize_impression_text(clause)) >= 4
    ]


def _max_similarity(value: str, candidates: list[str]) -> float:
    if len(value) < 4:
        return 0.0
    return max(
        (difflib.SequenceMatcher(None, value, candidate).ratio() for candidate in candidates if len(candidate) >= 4),
        default=0.0,
    )


def _score_impression_style_reuse(reply: str, recent_replies: list[str]) -> ImpressionStyleScore:
    if not recent_replies:
        return ImpressionStyleScore(total=0.0, full=0.0, ending=0.0, clause=0.0)

    body = _normalize_impression_body(reply)
    recent_bodies = [_normalize_impression_body(item) for item in recent_replies]
    full = _max_similarity(body, [item for item in recent_bodies if len(item) >= 20])

    clauses = _split_impression_clauses(reply)
    recent_clauses = [clause for item in recent_replies for clause in _split_impression_clauses(item)]
    ending = _max_similarity(clauses[-1] if clauses else "", [
        _split_impression_clauses(item)[-1]
        for item in recent_replies
        if _split_impression_clauses(item)
    ])
    clause = max((_max_similarity(item, recent_clauses) for item in clauses), default=0.0)
    total = full * 0.35 + ending * 0.45 + clause * 0.20
    return ImpressionStyleScore(total=total, full=full, ending=ending, clause=clause)


async def _load_recent_impression_replies(
    reader: MessageReader,
    *,
    group_id: str,
    bot_id: str,
) -> list[str]:
    return await reader.fetch_recent_impression_reply_contents(
        group_id,
        bot_id,
        limit=IMPRESSION_RECENT_REPLY_LIMIT,
    )


def _validate_impression_analysis(
    *,
    analysis: ImpressionAnalysis,
    target_evidence_source: str,
) -> tuple[bool, str]:
    if analysis.mode not in {"specific", "limited"}:
        return False, "mode 必须是 specific 或 limited"
    if not 2 <= len(analysis.evidence) <= 4:
        return False, "evidence 必须包含 2-4 条本人原话"

    normalized_source = "".join(target_evidence_source.split())
    for anchor in analysis.evidence:
        normalized_anchor = "".join(anchor.split())
        if len(normalized_anchor) < 2 or normalized_anchor not in normalized_source:
            return False, f"evidence 不在目标发言中: {anchor!r}"
    if analysis.mode == "specific" and not 1 <= len(analysis.observations) <= 4:
        return False, "specific 模式必须包含 1-4 条 observations"
    if analysis.mode == "limited" and len(analysis.observations) > 1:
        return False, "limited 模式最多保留一条确定现象"
    if len(analysis.uncertainties) > 4:
        return False, "uncertainties 最多包含 4 条"
    if len(analysis.avoid_patterns) > 4:
        return False, "avoid_patterns 最多包含 4 条"
    return True, ""


def _validate_impression_candidate(
    *,
    reply: str,
    mode: str,
    target_name: str,
    is_querying_other: bool,
    recent_replies: Optional[list[str]] = None,
) -> tuple[bool, str, ImpressionStyleScore]:
    score = _score_impression_style_reuse(reply, recent_replies or [])
    if not reply.startswith(f"对{target_name}的印象是"):
        return False, "reply 未使用指定开头", score
    if mode not in {"specific", "limited"}:
        return False, "mode 必须是 specific 或 limited", score
    max_reply_length = IMPRESSION_SPECIFIC_MAX_LENGTH if mode == "specific" else IMPRESSION_LIMITED_MAX_LENGTH
    if len(reply) > max_reply_length:
        return False, f"reply 超过 {max_reply_length} 字", score
    if mode == "limited" and not IMPRESSION_UNCERTAIN_PATTERN.search(reply):
        return False, "limited 模式必须明确表达暂时看不准", score
    style_issue = _find_impression_style_issue(reply)
    if style_issue:
        return False, style_issue, score
    addressing_issue = _find_impression_addressing_issue(reply, is_querying_other=is_querying_other)
    if addressing_issue:
        return False, addressing_issue, score
    if score.full >= IMPRESSION_SIMILARITY_THRESHOLD:
        return False, f"reply 与近期评价过于相似（{score.full:.0%}）", score
    if score.ending >= IMPRESSION_ENDING_SIMILARITY_THRESHOLD:
        return False, f"reply 结尾表达与近期评价过于相似（{score.ending:.0%}）", score
    return True, "", score


def _evaluate_impression_candidates(
    candidates: list[str],
    *,
    analysis: ImpressionAnalysis,
    target_name: str,
    is_querying_other: bool,
    recent_replies: list[str],
) -> list[ImpressionCandidateEvaluation]:
    evaluations: list[ImpressionCandidateEvaluation] = []
    for index, candidate in enumerate(candidates):
        valid, issue, score = _validate_impression_candidate(
            reply=candidate,
            mode=analysis.mode,
            target_name=target_name,
            is_querying_other=is_querying_other,
            recent_replies=recent_replies,
        )
        evaluations.append(ImpressionCandidateEvaluation(index=index, reply=candidate, score=score, issue=issue if not valid else ""))
    return evaluations


def _select_impression_candidate(evaluations: list[ImpressionCandidateEvaluation]) -> Optional[ImpressionCandidateEvaluation]:
    valid = [evaluation for evaluation in evaluations if not evaluation.issue]
    if not valid:
        return None
    return min(valid, key=lambda evaluation: evaluation.score.total)


def _build_impression_analysis_user_prompt(
    *,
    target_name: str,
    history_text: str,
    context_text: str,
    recent_replies: list[str],
) -> str:
    recent_text = "\n".join(f"- {reply}" for reply in recent_replies) or "（暂无近期评价）"
    return f"""
【目标：{target_name}】

【目标本人整体发言样本】
{history_text}

【近期对话片段】
{context_text}

【近期已发送群印象：只做表达结构审计，不可作为事实材料】
{recent_text}

请先核对 evidence 是否来自目标本人，再整理 observations、uncertainties 和近期需要避开的抽象表达结构。
""".strip()


def _build_impression_reply_user_prompt(*, target_name: str, analysis: ImpressionAnalysis) -> str:
    analysis_payload = json.dumps(asdict(analysis), ensure_ascii=False)
    return f"""
【当前评价对象：{target_name}】

【已完成的材料分析】
{analysis_payload}

只依据以上分析形成群印象。avoid_patterns 只表示本次表达需要避开的近期组织方式，不是新的事实材料。
请输出 {IMPRESSION_CANDIDATE_COUNT} 条候选；候选之间要有真实的表达差异，而不是同一句话的近义词替换。
""".strip()


def _should_skip_random_chat(msg: str) -> bool:
    """Return True when a message should never trigger random chat."""
    return bool(_random_chat_skip_reason(msg))


def _random_chat_skip_reason(msg: str) -> str:
    if len(msg) < 2:
        return "short_message"
    if any(msg.startswith(prefix) for prefix in BLOCK_PREFIXES):
        return "blocked_prefix"
    if any(keyword in msg for keyword in BLOCK_KEYWORDS):
        return "blocked_keyword"
    return ""


def _is_grounded_random_reply(msg: str, anchor: str, reply: str) -> bool:
    """确认随机回复引用的依据确实来自当前消息，而不是历史背景。"""
    if not reply.strip():
        return True
    normalized_msg = "".join(msg.split())
    normalized_anchor = "".join(anchor.split())
    return len(normalized_anchor) >= 2 and normalized_anchor in normalized_msg


async def is_in_auto_group(event: GroupMessageEvent) -> bool:
    """规则：判断该群是否在自动互动（印象 / 插嘴）白名单内。"""
    return event.group_id in AUTO_CHAT_GROUPS


def _is_exact_impression_request_message(event: GroupMessageEvent) -> bool:
    """只接受精确指令文本；额外消息段仅允许用于选择目标的 @。"""
    if event.get_plaintext().strip() not in IMPRESSION_COMMANDS:
        return False
    return all(segment.type in {"text", "at"} for segment in event.original_message)


async def is_exact_impression_request(event: GroupMessageEvent) -> bool:
    return await is_in_auto_group(event) and _is_exact_impression_request_message(event)


async def save_my_response(group_id: str, bot_qq: str, content: str) -> None:
    """将 bot 自己的回复写入 SQLite 群日志（转调 core.record_bot_message，单一真相源）。"""
    await record_bot_message(group_id, content, bot_qq)


# ================= 功能 1：默默记录群聊 =================
recorder = on_message(priority=1, block=False)
@recorder.handle()
async def _(event: GroupMessageEvent):
    if event.group_id not in AUTO_CHAT_GROUPS: return
    msg = event.get_plaintext().strip()
    if not msg or msg.startswith("/"): return
    await record_message(
        str(event.group_id),
        str(event.user_id),
        event.sender.card or event.sender.nickname,
        msg,
        str(event.message_id),
    )


# ================= 功能 2：生成印象 =================
um_cmd = on_command(
    "群印象",
    aliases={"评价我", "说说印象", "我的印象"},
    rule=is_exact_impression_request,
    priority=5,
    block=True,
)


@um_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    trace_request_id = new_request_id()
    start_turn_trace(trace_request_id, group_id=event.group_id, surface="impression", stage="analysis")
    record_intent(trace_request_id, "impression")
    group_id = str(event.group_id)
    rollout = resolve_rollout(group_id)
    record_rollout(
        trace_request_id,
        experiment_arm=rollout.arm,
        m1_context_mode=rollout.m1_context_mode,
        m2_memory_mode=rollout.m2_memory_mode,
        m3_tool_mode=getattr(rollout, "m3_tool_mode", "off"),
    )

    target_id, target_name, is_querying_other, is_querying_bot = _resolve_impression_target(event, str(bot.self_id))

    if is_querying_bot:
        reply_segment = MessageSegment.reply(event.message_id)
        refusals = [
            "喂，别想着查我对自己的印象。",
            "……啧，查我干嘛，你现在没事干？",
            "无可奉告。"
        ]
        finish_turn_trace(trace_request_id, outcome="completed")
        await um_cmd.finish(reply_segment + random.choice(refusals))
        return

    if is_querying_other:
        try:
            member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(target_id))
            target_name = member_info.get("card") or member_info.get("nickname") or f"用户{target_id}"
        except Exception as e:
            logger.error(f"获取被艾特成员信息失败: {e}")
            target_name = f"用户{target_id}"

    reply_segment = MessageSegment.reply(event.message_id)
    current_message_id = str(event.message_id)
    async with open_message_reader() as reader:
        history_rows, context_blocks = await _load_impression_material(
            reader,
            group_id=group_id,
            target_id=target_id,
            current_message_id=current_message_id,
        )
        recent_impression_replies = await _load_recent_impression_replies(
            reader,
            group_id=group_id,
            bot_id=str(bot.self_id),
        )

    if len(history_rows) < 10:
        finish_turn_trace(trace_request_id, outcome="completed")
        if is_querying_other:
            await um_cmd.finish(reply_segment + f"对{target_name}还没什么印象……让她多说点话吧。")
        else:
            await um_cmd.finish(reply_segment + "对你还没什么印象……多说点话吧。")
        return

    history_text = _build_impression_history_text(history_rows, target_name)
    context_text = _build_impression_context_text(
        context_blocks,
        target_id=target_id,
        target_name=target_name,
    )
    target_evidence_source = _build_target_evidence_source(
        history_rows,
        context_blocks,
        target_id=target_id,
    )
    event_memory, event_memory_result = await build_event_memory_context(
        f"{event.get_plaintext()}\n{target_evidence_source}",
        mode=rollout.m2_memory_mode,
    )
    record_event_memory(
        trace_request_id,
        candidates=event_memory_result.candidates,
        evidence_units=event_memory_result.evidence_units,
        confidences=event_memory_result.confidences,
        status=event_memory_result.status,
        reason=event_memory_result.reason,
        top_score=event_memory_result.top_score,
        score_margin=event_memory_result.score_margin,
        candidate_count=event_memory_result.candidate_count,
        retrieval_strategy=event_memory_result.retrieval_strategy,
        candidate_diagnostics=event_memory_result.candidate_diagnostics,
        fallback_reason=event_memory_result.fallback_reason
        or (event_memory_result.reason if event_memory_result.status == "unavailable" else ""),
    )
    relationship_context = _build_impression_relationship_context(target_evidence_source)
    context_sources = ["impression_history", "impression_context", "group_context"]
    if relationship_context:
        context_sources.append("relationship")
    if event_memory:
        context_sources.append("event_memory")
    if recent_impression_replies:
        context_sources.append("recent_impression_replies")

    persona = get_base_persona()
    is_wl2_active = False
    wl2_overlay = ""
    try:
        mem = get_user_memory(f"group_{group_id}")
        is_wl2_active, wl2_overlay = _resolve_wl2_overlay(mem)
    except Exception as e:
        logger.error(f"WL2 状态获取失败: {e}")

    if is_wl2_active:
        logger.info("🔥 [Impression] 判定当前处于 WL2 模式，正在应用群内状态覆写...")
        if not wl2_overlay:
            wl2_overlay = load_prompt_template("wl2_persona.txt").strip()

    state_overlay_prompt = ""
    if wl2_overlay:
        state_overlay_prompt = f"""
    🚨【当前群最高优先级世界线覆写】
    以下状态覆盖基础人设中与其冲突的世界观、关系和情绪设定：
    {wl2_overlay}
    """
    context_sources.append("persona")
    if wl2_overlay:
        context_sources.append("temporary_state")
    record_context_sources(trace_request_id, context_sources)
    analysis_blocks = [
        {"kind": "impression_history", "source": "impression_history", "content": history_text, "priority": 850},
        {"kind": "impression_context", "source": "impression_context", "content": context_text, "priority": 800},
        {"kind": "relationship", "source": "relationship", "content": relationship_context, "priority": 900},
        {"kind": "recent_impression_replies", "source": "recent_impression_replies", "content": "\n".join(recent_impression_replies), "priority": 500},
        {"kind": "persona", "source": "persona", "content": persona, "priority": 950},
        {"kind": "temporary_state", "source": "temporary_state", "content": state_overlay_prompt, "priority": 980},
    ]
    analysis_selected, analysis_shadow = select_context_for_mode(
        analysis_blocks,
        stage="impression_analysis",
        active=mode_is_active(rollout.m1_context_mode),
    )
    analysis_sources = {str(block["source"]) for block in analysis_selected}
    if "impression_history" not in analysis_sources:
        history_text = ""
    if "impression_context" not in analysis_sources:
        context_text = ""
    if "relationship" not in analysis_sources:
        relationship_context = ""
    if "recent_impression_replies" not in analysis_sources:
        recent_impression_replies = []
    record_context_shadow(trace_request_id, analysis_shadow.as_dict())

    try:
        final_reply = ""
        analysis = None
        analysis_messages = [
            {
                "role": "system",
                "content": _build_impression_analysis_system_prompt(
                    target_name=target_name,
                    relationship_context=relationship_context,
                ),
            },
            {
                "role": "user",
                "content": _build_impression_analysis_user_prompt(
                    target_name=target_name,
                    history_text=history_text,
                    context_text=context_text,
                    recent_replies=recent_impression_replies,
                ),
            },
        ]
        for attempt in range(2):
            raw_result = await call_deepseek_api(
                analysis_messages,
                model_name=MODEL_NAME,
                force_json=True,
                temperature=0.25,
                presence_penalty=0.0,
                frequency_penalty=0.0,
                max_tokens=1024,
                timeout=60.0,
            )
            parsed_analysis = _parse_impression_analysis(raw_result)
            if parsed_analysis is not None:
                is_valid, invalid_reason = _validate_impression_analysis(
                    analysis=parsed_analysis,
                    target_evidence_source=target_evidence_source,
                )
                if is_valid:
                    analysis = parsed_analysis
                    record_parse_result(trace_request_id, success=True)
                    logger.info(
                        f"🧭 [Impression] 材料分析完成: mode={analysis.mode!r}, "
                        f"observations={len(analysis.observations)}, avoid_patterns={len(analysis.avoid_patterns)}"
                    )
                    break
                record_parse_result(trace_request_id, success=False)
            else:
                invalid_reason = "分析结果不是合法 JSON"
                record_parse_result(trace_request_id, success=False)

            logger.warning(f"⚠️ [Impression] 材料分析校验失败（第 {attempt + 1} 次）: {invalid_reason}")
            if attempt == 0:
                analysis_messages.extend(
                    [
                        {"role": "assistant", "content": raw_result},
                        {
                            "role": "user",
                            "content": (
                                f"上一次分析未通过校验：{invalid_reason}。请重新输出完整 JSON；"
                                f"evidence 必须是包含2-4条字符串的 JSON 数组，逐字来自【{target_name}】本人发言，不能输出单个字符串；"
                                "observations 只能总结目标材料，"
                                "avoid_patterns 只能描述近期评价的表达结构，不能复制旧评价原句。"
                            ),
                        },
                    ]
                )

        if analysis is not None:
            set_trace_stage(trace_request_id, "reply")
            reply_blocks = [
                {"kind": "persona", "source": "persona", "content": persona, "priority": 950},
                {"kind": "relationship", "source": "relationship", "content": relationship_context, "priority": 900},
                {"kind": "analysis_result", "source": "analysis_result", "content": json.dumps(asdict(analysis), ensure_ascii=False), "priority": 850},
                {"kind": "event_memory", "source": "event_memory", "content": event_memory, "priority": 760},
                {"kind": "temporary_state", "source": "temporary_state", "content": state_overlay_prompt, "priority": 980},
            ]
            reply_selected, reply_shadow = select_context_for_mode(
                reply_blocks,
                stage="impression_reply",
                active=mode_is_active(rollout.m1_context_mode),
            )
            reply_sources = {str(block["source"]) for block in reply_selected}
            persona_for_reply = persona if "persona" in reply_sources else ""
            relationship_for_reply = relationship_context if "relationship" in reply_sources else ""
            event_for_reply = event_memory if "event_memory" in reply_sources else ""
            state_for_reply = state_overlay_prompt if "temporary_state" in reply_sources else ""
            record_context_shadow(trace_request_id, reply_shadow.as_dict())
            expression_messages = [
                {
                    "role": "system",
                    "content": _build_impression_reply_system_prompt(
                        persona=persona_for_reply,
                        state_overlay_prompt=state_for_reply,
                        target_name=target_name,
                        is_querying_other=is_querying_other,
                        relationship_context=relationship_for_reply,
                        event_memory=event_for_reply,
                    ),
                },
                {
                    "role": "user",
                    "content": _build_impression_reply_user_prompt(target_name=target_name, analysis=analysis),
                },
            ]
            for attempt in range(2):
                raw_result = await call_deepseek_api(
                    expression_messages,
                    model_name=MODEL_NAME,
                    force_json=True,
                    temperature=0.95,
                    presence_penalty=0.3,
                    frequency_penalty=0.3,
                    max_tokens=1536,
                    timeout=60.0,
                )
                inner_os, candidates = _parse_impression_candidates(raw_result)
                record_parse_result(trace_request_id, success=bool(candidates))
                if inner_os:
                    logger.info(f"📝【小彰评价OS】: {inner_os}")
                evaluations: list[ImpressionCandidateEvaluation] = []
                selected = None
                if len(candidates) < IMPRESSION_CANDIDATE_COUNT and attempt == 0:
                    invalid_reason = f"候选数量不足（需要 {IMPRESSION_CANDIDATE_COUNT} 条，实际 {len(candidates)} 条）"
                else:
                    evaluations = _evaluate_impression_candidates(
                        candidates[:IMPRESSION_CANDIDATE_COUNT],
                        analysis=analysis,
                        target_name=target_name,
                        is_querying_other=is_querying_other,
                        recent_replies=recent_impression_replies,
                    )
                    selected = _select_impression_candidate(evaluations)
                    invalid_reasons = [
                        f"#{evaluation.index + 1}: {evaluation.issue}"
                        for evaluation in evaluations
                        if evaluation.issue
                    ]
                    invalid_reason = "；".join(invalid_reasons) or "没有可用候选"
                    if selected is not None:
                        final_reply = selected.reply
                        logger.info(
                            f"✅ [Impression] 选择候选 #{selected.index + 1}: "
                            f"style={selected.score.total:.0%} full={selected.score.full:.0%} "
                            f"ending={selected.score.ending:.0%} clause={selected.score.clause:.0%}"
                        )
                        break

                logger.warning(f"⚠️ [Impression] 表达候选校验失败（第 {attempt + 1} 次）: {invalid_reason}")
                if attempt == 0:
                    expression_messages.extend(
                        [
                            {"role": "assistant", "content": raw_result},
                            {
                                "role": "user",
                                "content": (
                                    f"上一次候选未通过校验：{invalid_reason}。请重新输出完整 JSON；"
                                    f"必须给出 {IMPRESSION_CANDIDATE_COUNT} 条候选，遵守目标称呼，"
                                    "并避开分析结果中的近期重复表达结构。候选可以自然结束，不要补统一的态度收尾。"
                                ),
                            },
                        ]
                    )

        if not final_reply:
            if is_querying_other:
                final_reply = f"对{target_name}的印象是……她最近留下的话还是太碎了，现在硬下结论也没意思。"
            else:
                final_reply = f"对{target_name}的印象是……你最近留下的话还是太碎了，现在硬下结论也没意思。"

        await save_my_response(group_id, str(bot.self_id), final_reply)

        thinking_time = random.uniform(3.0, 5.0)
        await asyncio.sleep(thinking_time)
        finish_turn_trace(trace_request_id, outcome="completed")
        await um_cmd.finish(reply_segment + final_reply)
    except FinishedException:
        finish_turn_trace(trace_request_id, outcome="completed")
        raise
    except Exception as e:
        finish_turn_trace(trace_request_id, outcome="failed")
        logger.exception(f"群印象生成失败: {e}")
        await um_cmd.finish(reply_segment + "脑子短路了...")


# ================= 功能 3：随机插嘴 (AutoChat) =================
AUTO_CHAT_COOLDOWN = {}

random_chat = on_message(rule=is_in_auto_group, priority=99, block=False)

@random_chat.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = str(event.group_id)
    trace_request_id = new_request_id()
    start_turn_trace(trace_request_id, group_id=event.group_id, surface="auto_chat", stage="response")
    record_intent(trace_request_id, "auto_chat")
    rollout = resolve_rollout(group_id)
    record_rollout(
        trace_request_id,
        experiment_arm=rollout.arm,
        m1_context_mode=rollout.m1_context_mode,
        m2_memory_mode=rollout.m2_memory_mode,
        m3_tool_mode=getattr(rollout, "m3_tool_mode", "off"),
    )
    msg = event.get_plaintext().strip()

    def finish_silently(reason: str, *, reply: str = "", anchor: str = "") -> None:
        try:
            report = evaluate_auto_reply_shadow(
                msg,
                addressed_to_bot=any(name in msg for name in ("小彰", "彰人", "东云彰人")),
                silence_reason=reason,
                reply=reply,
                anchor=anchor,
            )
            record_auto_reply_shadow(trace_request_id, report)
        except Exception:
            pass
        finish_turn_trace(trace_request_id, outcome="silent")

    now_ts = time.time()
    if is_sleeping():
        finish_silently("sleeping")
        return
    now = datetime.datetime.now(TZ_CN)

    skip_reason = _random_chat_skip_reason(msg)
    if skip_reason:
        finish_silently(skip_reason)
        return

    last_time = AUTO_CHAT_COOLDOWN.get(group_id, 0)
    if time.time() < get_safe_until():
        finish_silently("safety_period")
        return
    if now_ts - last_time < 10:
        finish_silently("cooldown")
        return

    if random.random() > CHAT_PROBABILITY:
        finish_silently("probability_gate")
        return

    AUTO_CHAT_COOLDOWN[group_id] = now_ts

    reply_segment = MessageSegment.reply(event.message_id)
    group_context = await get_group_context(
        str(event.group_id),
        limit=12,
        max_age_seconds=180,
        max_gap_seconds=90,
        exclude_message_id=str(event.message_id),
        include_timestamps=True,
    )
    current_user_name = event.sender.card or event.sender.nickname

    shared_prompt_context = await build_shared_prompt_context(msg)
    script_examples = shared_prompt_context.script_examples
    pjsk_block = shared_prompt_context.pjsk_block
    relation_info = (
        shared_prompt_context.relationship_match.content
        if shared_prompt_context.relationship_match is not None
        else ""
    )
    song_info = shared_prompt_context.song_mention
    event_memory, event_memory_result = await build_event_memory_context(
        msg,
        mode=rollout.m2_memory_mode,
        retrieval_ctx=getattr(shared_prompt_context, "retrieval_context", None),
    )

    cool_guy_filter = PROMPTS_DB.get("cool_guy_filter", "")

    h24 = now.hour
    if h24 < 6: period = "凌晨"
    elif h24 < 12: period = "上午"
    elif h24 == 12: period = "中午"
    elif h24 < 18: period = "下午"
    else: period = "晚上"
    h12 = 12 if h24 % 12 == 0 else h24 % 12
    time_str = f"{period}{h12}点{now.minute:02d}分"

    persona = shared_prompt_context.persona
    is_wl2_active = False
    try:
        mem = get_user_memory(f"group_{group_id}")
        if any(item.get("id") == "WL2" for item in mem.get("temp_implants", [])):
            is_wl2_active = True
    except Exception as e:
        logger.error(f"WL2 状态获取失败: {e}")

    if is_wl2_active:
        logger.info("🔥 [AutoChat] 判定当前处于 WL2 模式，正在注入绝望剧本...")
        wl2_text = load_prompt_template("wl2_persona.txt")
        if wl2_text:
            persona += "\n" + wl2_text
        persona += "\n🎬【导演附加指导】：请基于上述 WL2 设定进行潜水判断。你不关心群友的状态，回复可以体现一些冷漠和距离感。"

    # 涉冬弥话题：注入 routine 锚定的冬弥去向 + 连贯锁（与主对话一致；WL2 决裂线跳过）
    toya_anchor = ""
    if any(k in msg for k in ("冬弥", "toya", "Toya", "搭档", "青柳")) and not is_wl2_active:
        get_daily_activity(now.hour, now.weekday(), now.minute)  # 预热 routine 缓存
        toya_anchor = get_toya_anchor()

    BOT_NAMES = ["小彰", "彰人", "东云彰人"]
    is_directed_at_bot = any(name in msg for name in BOT_NAMES)

    if is_directed_at_bot:
        scene_desc = f'群友【{current_user_name}】直接在跟你说话，消息内容是："{msg}"'
        task_logic = f'''
    作为东云彰人，对方在直接跟你说话，你必须回应。保持盐系男高中生人设，用符合角色的方式直接回复【{current_user_name}】。
    1. 不需要判断"这话是不是对我说的"，直接进入回应。
    2. 根据消息内容决定态度：调侃就无语反击，问问题就冷淡回答，废话就简短怼回去。
    3. **必须**输出非空的reply。'''
        inner_os_guide = f'分析过程：对方【{current_user_name}】在直接跟我说话，内容是"{msg}"。思考一下用什么态度回比较符合人设。'
        user_content = f'请以东云彰人的身份直接回应【{current_user_name}】，对方在跟你说话。严格按JSON格式输出，禁止复读原话。'
    else:
        scene_desc = f'你正在群里潜水（旁观），群友【{current_user_name}】刚刚发了一条消息："{msg}"'
        task_logic = f'''
    作为群里潜水的成员（东云彰人），看心情决定是否插一句嘴。遵循以下法则：
    1. **唯一回复目标**：只能点评当前消息"{msg}"，禁止回复群聊背景里的任何旧消息。
    2. **背景用途受限**：群聊背景只能帮助理解当前消息里的代词、省略或正在延续的人物关系，不能借旧话题强行插嘴。
    3. **静默判定**：当前消息本身没意思、无法自然回应时，必须输出空字符串继续潜水。'''
        inner_os_guide = f'分析过程：1.这句话是对我说的吗？2.当前消息"{msg}"本身有槽点吗？3.决定回应当前消息或继续潜水。'
        user_content = f'你在群里潜水，看到【{current_user_name}】说了："{msg}"。这句话不是对你说的。决定是否插嘴点评，严格按JSON格式输出。'

    auto_context_blocks = [
        {"kind": "current_message", "source": "current_message", "content": msg, "priority": 1000},
        {"kind": "persona", "source": "persona", "content": persona, "priority": 950},
        {"kind": "group_context", "source": "group_context", "content": group_context, "priority": 700},
        {"kind": "relationship", "source": "relationship", "content": relation_info, "priority": 850},
        {"kind": "script_retrieval", "source": "script_retrieval", "content": script_examples, "priority": 650},
        {"kind": "event_memory", "source": "event_memory", "content": event_memory, "priority": 760},
        {"kind": "pjsk_retrieval", "source": "pjsk_retrieval", "content": pjsk_block, "priority": 550},
        {"kind": "song_info", "source": "song_info", "content": song_info, "priority": 600},
        {"kind": "cool_guy_filter", "source": "cool_guy_filter", "content": cool_guy_filter, "priority": 700},
        {"kind": "toya_anchor", "source": "toya_anchor", "content": toya_anchor, "priority": 800},
    ]
    auto_selected, auto_shadow = select_context_for_mode(
        auto_context_blocks,
        stage="auto_chat",
        active=mode_is_active(rollout.m1_context_mode),
    )
    auto_sources = {str(block["source"]) for block in auto_selected}

    def auto_selected_text(source: str, value: str) -> str:
        return value if source in auto_sources else ""

    system_prompt = _build_auto_chat_system_prompt(
        persona=auto_selected_text("persona", persona),
        time_str=time_str,
        toya_anchor=auto_selected_text("toya_anchor", toya_anchor),
        scene_desc=scene_desc,
        group_context=auto_selected_text("group_context", group_context),
        relation_info=auto_selected_text("relationship", relation_info),
        song_info=auto_selected_text("song_info", song_info),
        script_examples=auto_selected_text("script_retrieval", script_examples),
        pjsk_block=auto_selected_text("pjsk_retrieval", pjsk_block),
        cool_guy_filter=auto_selected_text("cool_guy_filter", cool_guy_filter),
        task_logic=task_logic,
        inner_os_guide=inner_os_guide,
        event_memory=auto_selected_text("event_memory", event_memory),
    )

    record_event_memory(
        trace_request_id,
        candidates=event_memory_result.candidates,
        evidence_units=event_memory_result.evidence_units,
        confidences=event_memory_result.confidences,
        status=event_memory_result.status,
        reason=event_memory_result.reason,
        top_score=event_memory_result.top_score,
        score_margin=event_memory_result.score_margin,
        candidate_count=event_memory_result.candidate_count,
        retrieval_strategy=event_memory_result.retrieval_strategy,
        candidate_diagnostics=event_memory_result.candidate_diagnostics,
        fallback_reason=event_memory_result.fallback_reason
        or (event_memory_result.reason if event_memory_result.status == "unavailable" else ""),
    )
    context_sources = ["current_message", "group_context", "persona"]
    if relation_info:
        context_sources.append("relationship")
    if script_examples:
        context_sources.append("script_retrieval")
    if event_memory:
        context_sources.append("event_memory")
    if pjsk_block:
        context_sources.append("pjsk_retrieval")
    if toya_anchor:
        context_sources.append("toya_anchor")
    record_context_sources(trace_request_id, context_sources)
    record_context_shadow(trace_request_id, auto_shadow.as_dict())

    try:
        raw_result = await call_deepseek_api(
            [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
            model_name=MODEL_NAME,
            force_json=True,
            temperature=1.1,
            presence_penalty=0.4,
            frequency_penalty=0.6,
            max_tokens=2048,
            timeout=10.0,
        )
        reply = ""

        try:
            response_data = parse_json_object(raw_result)
            if response_data is None:
                raise json.JSONDecodeError("invalid json object", raw_result, 0)
            record_parse_result(trace_request_id, success=True)
            inner_os = response_data.get("inner_os", "")
            if inner_os:
                logger.info(f"💦【小彰潜水OS】: {inner_os}")
            anchor = response_data.get("anchor", "").strip()
            reply = response_data.get("reply", "").strip()

            if not _is_grounded_random_reply(msg, anchor, reply):
                logger.warning(f"⚠️ [AutoChat] 当前消息锚点校验失败，静音丢弃: anchor={anchor!r} reply={reply[:40]!r}")
                finish_silently("anchor_failed", reply=reply, anchor=anchor)
                return

            clean_msg = msg.strip("。，！？.!?~ \n\r")
            clean_reply = reply.strip("。，！？.!?~ \n\r")

            if len(clean_reply) >= 4 and (clean_reply in clean_msg or clean_msg in clean_reply):
                logger.warning(f"⚠️ [AutoChat] 触发片段/缝合复读拦截！静音丢弃: {reply}")
                finish_silently("repeat", reply=reply, anchor=anchor)
                return

        except json.JSONDecodeError:
            record_parse_result(trace_request_id, success=False)
            logger.warning(f"⚠️ 插嘴系统未输出标准JSON: {raw_result[:120]}")
            # 救援：提取 reply 字段（最常见原因：inner_os 内部引号未转义）
            rescued_os = rescue_field(raw_result, "inner_os")
            if rescued_os:
                logger.info(f"💦【小彰潜水OS（救援）】: {rescued_os[:80]}")
            rescued = rescue_field(raw_result, "reply")
            if rescued is None:
                rescued = rescue_tail_after_field(raw_result, "inner_os")
            if rescued is not None:
                reply = rescued.strip()
                anchor = rescue_field(raw_result, "anchor") or ""
                if not _is_grounded_random_reply(msg, anchor, reply):
                    logger.warning("⚠️ [AutoChat] 救援结果缺少有效当前消息锚点，静音丢弃")
                    finish_silently("anchor_failed", reply=reply, anchor=anchor)
                    return
                logger.info(f"🔧 插嘴救援成功，reply={repr(reply[:40])}")
                # reply 为空 = 模型决定静默，走正常静默流程
            else:
                finish_silently("json_parse_failed")
                return

        if "念叨" in reply or "自言自语" in reply:
            finish_silently("self_talk", reply=reply, anchor=anchor)
            return
        if not reply or reply.strip() == "……":
            finish_silently("model_empty", reply=reply, anchor=anchor)
            return
        if len(reply) < 2:
            finish_silently("reply_too_short", reply=reply, anchor=anchor)
            return

        await save_my_response(str(event.group_id), str(bot.self_id), reply)

        base_delay = random.uniform(1.5, 3.5)
        typing_delay = len(reply) * 0.15
        total_delay = base_delay + typing_delay
        if total_delay > 8: total_delay = 8

        await asyncio.sleep(total_delay)
        try:
            report = evaluate_auto_reply_shadow(
                msg,
                addressed_to_bot=any(name in msg for name in ("小彰", "彰人", "东云彰人")),
                reply=reply,
                anchor=anchor,
                actual_interjected=True,
            )
            record_auto_reply_shadow(trace_request_id, report, actual_interjected=True)
        except Exception:
            pass
        finish_turn_trace(trace_request_id, outcome="completed")
        await random_chat.finish(reply_segment + reply)
    except FinishedException:
        finish_turn_trace(trace_request_id, outcome="completed")
        raise
    except Exception as e:
        # 随机插嘴是尽力而为的可选行为：失败只记日志，不打扰群聊
        try:
            report = evaluate_auto_reply_shadow(
                msg,
                addressed_to_bot=any(name in msg for name in ("小彰", "彰人", "东云彰人")),
                silence_reason="exception",
            )
            record_auto_reply_shadow(trace_request_id, report)
        except Exception:
            pass
        finish_turn_trace(trace_request_id, outcome="failed")
        logger.debug(f"💦 随机插嘴流程异常，本次静默放弃: {e}")
