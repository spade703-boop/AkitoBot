"""群印象：默默记录群聊到 SQLite、生成「群印象」评价、低概率随机插嘴。"""

import asyncio
import datetime
import difflib
import json
import random
import re
import sqlite3
import time
from typing import Optional

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from ...core import (
    ALLOWED_CP_GROUPS,
    DB_PATH,
    PROMPTS_DB,
    TZ_CN,
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
    parse_json_object,
    record_bot_message,
    render_auto_chat_prompt,
    render_impression_prompt,
    rescue_field,
    rescue_tail_after_field,
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
IMPRESSION_COMMANDS = ("群印象", "评价我", "说说印象", "我的印象")

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

MessageRow = tuple[int, str, str, str, Optional[str], str]


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


def _parse_message_timestamp(value: str) -> Optional[datetime.datetime]:
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def _format_message_time(value: str) -> str:
    parsed = _parse_message_timestamp(value)
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


def _load_context_window(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    target_row: MessageRow,
    current_message_id: str,
) -> list[MessageRow]:
    before_rows = conn.execute(
        "SELECT id, user_id, nickname, content, message_id, timestamp "
        "FROM messages WHERE group_id=? AND id<=? ORDER BY id DESC LIMIT ?",
        (group_id, target_row[0], IMPRESSION_CONTEXT_SIDE_LIMIT + 1),
    ).fetchall()
    after_rows = conn.execute(
        "SELECT id, user_id, nickname, content, message_id, timestamp "
        "FROM messages WHERE group_id=? AND id>? ORDER BY id ASC LIMIT ?",
        (group_id, target_row[0], IMPRESSION_CONTEXT_SIDE_LIMIT),
    ).fetchall()

    target_time = _parse_message_timestamp(target_row[5])
    window: list[MessageRow] = []
    for row in list(reversed(before_rows)) + after_rows:
        if _is_current_impression_message(row, current_message_id) or _is_impression_noise(row[3]):
            continue
        row_time = _parse_message_timestamp(row[5])
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
        previous_time = _parse_message_timestamp(previous[-1][5])
        current_time = _parse_message_timestamp(window[0][5])
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


def _load_impression_material(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    target_id: str,
    current_message_id: str,
    now: Optional[datetime.datetime] = None,
) -> tuple[list[MessageRow], list[list[MessageRow]]]:
    history_candidates = conn.execute(
        "SELECT id, user_id, nickname, content, message_id, timestamp "
        "FROM messages WHERE group_id=? AND user_id=? AND length(content)>2 "
        "ORDER BY id DESC LIMIT ?",
        (group_id, target_id, IMPRESSION_HISTORY_SCAN_LIMIT),
    ).fetchall()
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
    recent_candidates = conn.execute(
        "SELECT id, user_id, nickname, content, message_id, timestamp "
        "FROM messages WHERE group_id=? AND user_id=? AND length(trim(content))>0 AND timestamp>=? "
        "ORDER BY id DESC LIMIT ?",
        (group_id, target_id, cutoff.strftime("%Y-%m-%d %H:%M:%S"), IMPRESSION_RECENT_SCAN_LIMIT),
    ).fetchall()
    recent_target_rows = _select_target_rows(
        recent_candidates,
        current_message_id=current_message_id,
        limit=IMPRESSION_RECENT_TARGET_LIMIT,
        min_content_length=1,
        max_repeats=None,
    )
    windows = [
        _load_context_window(
            conn,
            group_id=group_id,
            target_row=row,
            current_message_id=current_message_id,
        )
        for row in recent_target_rows
    ]
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


def _build_impression_system_prompt(
    *,
    persona: str,
    state_overlay_prompt: str,
    target_name: str,
    is_querying_other: bool,
) -> str:
    """Compatibility wrapper for the core impression renderer."""
    return render_impression_prompt(
        persona=persona,
        state_overlay_prompt=state_overlay_prompt,
        target_name=target_name,
        is_querying_other=is_querying_other,
        specific_max_length=IMPRESSION_SPECIFIC_MAX_LENGTH,
        limited_max_length=IMPRESSION_LIMITED_MAX_LENGTH,
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
    )


def _parse_impression_result(raw_result: str) -> tuple[str, str, list[str], str, str]:
    final_reply = ""
    inner_os = ""
    evidence: list[str] = []
    mode = ""
    angle = ""

    try:
        response_data = parse_json_object(raw_result)
        if response_data is None:
            raise json.JSONDecodeError("invalid json object", raw_result, 0)
        inner_os = str(response_data.get("inner_os", "")).strip()
        if inner_os:
            logger.info(f"📝【小彰评价OS】: {inner_os}")
        raw_evidence = response_data.get("evidence", [])
        if isinstance(raw_evidence, list):
            evidence = [str(item).strip() for item in raw_evidence if str(item).strip()]
        elif isinstance(raw_evidence, str) and raw_evidence.strip():
            evidence = [raw_evidence.strip()]
        mode = str(response_data.get("mode", "")).strip().lower()
        angle = str(response_data.get("angle", "")).strip()
        final_reply = str(response_data.get("reply", "")).strip()
        if not final_reply:
            final_reply = "（打量了你一下）……没什么好说的。"
    except json.JSONDecodeError:
        logger.warning(f"⚠️ 评价系统未输出标准JSON: {raw_result[:120]}")
        rescued = rescue_field(raw_result, "reply")
        if rescued is None:
            rescued = rescue_tail_after_field(raw_result, "inner_os")
        if rescued is not None:
            final_reply = rescued.strip().strip('"')
            logger.info(f"🔧 评价救援成功: {final_reply[:60]}")
        else:
            final_reply = "（上下打量了你一下）……啧，没什么特别的印象。"

    return final_reply, inner_os, evidence, mode, angle


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
    return re.sub(r"[^\w\u4e00-\u9fff]", "", body).lower()


def _find_similar_impression_reply(reply: str, recent_replies: list[str]) -> tuple[str, float]:
    normalized_reply = _normalize_impression_body(reply)
    if len(normalized_reply) < 20:
        return "", 0.0

    closest_reply = ""
    closest_ratio = 0.0
    for recent_reply in recent_replies:
        normalized_recent = _normalize_impression_body(recent_reply)
        if len(normalized_recent) < 20:
            continue
        ratio = difflib.SequenceMatcher(None, normalized_reply, normalized_recent).ratio()
        if ratio > closest_ratio:
            closest_reply = recent_reply
            closest_ratio = ratio
    return closest_reply, closest_ratio


def _load_recent_impression_replies(
    conn: sqlite3.Connection,
    *,
    group_id: str,
    bot_id: str,
) -> list[str]:
    rows = conn.execute(
        "SELECT content FROM messages WHERE group_id=? AND user_id=? AND content LIKE '对%印象是%' "
        "ORDER BY id DESC LIMIT ?",
        (group_id, bot_id, IMPRESSION_RECENT_REPLY_LIMIT),
    ).fetchall()
    return [str(row[0]).strip() for row in rows if row[0] and str(row[0]).strip()]


def _validate_impression_result(
    *,
    reply: str,
    evidence: list[str],
    mode: str,
    angle: str,
    target_name: str,
    is_querying_other: bool,
    target_evidence_source: str,
    recent_replies: Optional[list[str]] = None,
) -> tuple[bool, str]:
    if not reply.startswith(f"对{target_name}的印象是"):
        return False, "reply 未使用指定开头"
    if mode not in {"specific", "limited"}:
        return False, "mode 必须是 specific 或 limited"
    max_reply_length = IMPRESSION_SPECIFIC_MAX_LENGTH if mode == "specific" else IMPRESSION_LIMITED_MAX_LENGTH
    if len(reply) > max_reply_length:
        return False, f"reply 超过 {max_reply_length} 字"
    if mode == "specific" and len(angle) < 4:
        return False, "angle 缺少有区分度的观察角度"
    if mode == "limited" and not IMPRESSION_UNCERTAIN_PATTERN.search(reply):
        return False, "limited 模式必须明确表达暂时看不准，而不是补全人格画像"
    if not 2 <= len(evidence) <= 4:
        return False, "evidence 必须包含 2-4 条本人原话"

    normalized_source = "".join(target_evidence_source.split())
    for anchor in evidence:
        normalized_anchor = "".join(anchor.split())
        if len(normalized_anchor) < 2 or normalized_anchor not in normalized_source:
            return False, f"evidence 不在目标发言中: {anchor!r}"
    style_issue = _find_impression_style_issue(reply)
    if style_issue:
        return False, style_issue
    addressing_issue = _find_impression_addressing_issue(reply, is_querying_other=is_querying_other)
    if addressing_issue:
        return False, addressing_issue
    similar_reply, similarity = _find_similar_impression_reply(reply, recent_replies or [])
    if similarity >= IMPRESSION_SIMILARITY_THRESHOLD:
        return False, f"reply 与近期评价过于相似（{similarity:.0%}）: {similar_reply[:36]!r}"
    return True, ""


def _parse_impression_reply(raw_result: str) -> tuple[str, str]:
    """Parse impression model output into final reply and inner thoughts."""
    final_reply, inner_os, _evidence, _mode, _angle = _parse_impression_result(raw_result)
    return final_reply, inner_os


def _should_skip_random_chat(msg: str) -> bool:
    """Return True when a message should never trigger random chat."""
    if len(msg) < 2:
        return True
    if any(msg.startswith(prefix) for prefix in BLOCK_PREFIXES):
        return True
    return any(keyword in msg for keyword in BLOCK_KEYWORDS)


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


def save_my_response(group_id: str, bot_qq: str, content: str) -> None:
    """将 bot 自己的回复写入 SQLite 群日志（转调 core.record_bot_message，单一真相源）。"""
    record_bot_message(group_id, content, bot_qq)


# ================= 功能 1：默默记录群聊 =================
recorder = on_message(priority=1, block=False)
@recorder.handle()
async def _(event: GroupMessageEvent):
    if event.group_id not in AUTO_CHAT_GROUPS: return
    msg = event.get_plaintext().strip()
    if not msg or msg.startswith("/"): return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (group_id, user_id, nickname, content, message_id) VALUES (?, ?, ?, ?, ?)",
        (
            str(event.group_id),
            str(event.user_id),
            event.sender.card or event.sender.nickname,
            msg,
            str(event.message_id),
        ),
    )
    conn.commit()
    conn.close()


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
    group_id = str(event.group_id)

    target_id, target_name, is_querying_other, is_querying_bot = _resolve_impression_target(event, str(bot.self_id))

    if is_querying_bot:
        reply_segment = MessageSegment.reply(event.message_id)
        refusals = [
            "喂，别想着查我对自己的印象。",
            "……啧，查我干嘛，你现在没事干？",
            "无可奉告。"
        ]
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
    with sqlite3.connect(DB_PATH) as conn:
        history_rows, context_blocks = _load_impression_material(
            conn,
            group_id=group_id,
            target_id=target_id,
            current_message_id=current_message_id,
        )
        recent_impression_replies = _load_recent_impression_replies(
            conn,
            group_id=group_id,
            bot_id=str(bot.self_id),
        )

    if len(history_rows) < 10:
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

    system_prompt = _build_impression_system_prompt(
        persona=persona,
        state_overlay_prompt=state_overlay_prompt,
        target_name=target_name,
        is_querying_other=is_querying_other,
    )

    user_prompt = f"""
    以下材料只用于评价【{target_name}】。

    【本人整体发言样本（最近最多 {IMPRESSION_HISTORY_LIMIT} 条）】
    {history_text}

    【近期对话片段（最近 {IMPRESSION_RECENT_DAYS} 天，最多 {IMPRESSION_CONTEXT_MAX_BLOCKS} 段）】
    {context_text}

    请先从【{target_name}】本人的原话中选取 evidence，再综合一段时间内的表现形成整体印象。其他人的话只能帮助理解对话。
    最终回复必须遵守 system 中的称呼方式：{"用“她”谈论目标" if is_querying_other else "直接用“你”对本人说"}。
    """

    try:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        final_reply = ""
        for attempt in range(2):
            raw_result = await call_deepseek_api(
                messages,
                model_name=MODEL_NAME,
                force_json=True,
                temperature=0.8,
                presence_penalty=0.2,
                frequency_penalty=0.2,
                max_tokens=1024,
                timeout=60.0,
            )
            candidate_reply, _inner_os, evidence, mode, angle = _parse_impression_result(raw_result)
            is_valid, invalid_reason = _validate_impression_result(
                reply=candidate_reply,
                evidence=evidence,
                mode=mode,
                angle=angle,
                target_name=target_name,
                is_querying_other=is_querying_other,
                target_evidence_source=target_evidence_source,
                recent_replies=recent_impression_replies,
            )
            if is_valid:
                final_reply = candidate_reply
                break

            logger.warning(
                f"⚠️ [Impression] 结果校验失败（第 {attempt + 1} 次）: {invalid_reason}; "
                f"reply_len={len(candidate_reply)}, mode={mode!r}, angle={angle!r}"
            )
            if attempt == 0:
                messages.extend(
                    [
                        {"role": "assistant", "content": raw_result},
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过校验：{invalid_reason}。请重新阅读材料并输出完整 JSON；"
                                f"evidence 必须逐字来自【{target_name}】本人发言；angle 要写出不能套给别人的具体观察；"
                                "材料足够就用 specific 自然发挥；材料太碎就用 limited 明说暂时看不准，"
                                "不要写兴趣标签加性格结论的人物小传；"
                                f"称呼目标时必须{('使用“她”' if is_querying_other else '直接使用“你”')}。"
                            ),
                        },
                    ]
                )

        if not final_reply:
            if is_querying_other:
                final_reply = f"对{target_name}的印象是……她最近留下的话还是太碎了，现在硬下结论也没意思。"
            else:
                final_reply = f"对{target_name}的印象是……你最近留下的话还是太碎了，现在硬下结论也没意思。"

        save_my_response(group_id, str(bot.self_id), final_reply)

        thinking_time = random.uniform(3.0, 5.0)
        await asyncio.sleep(thinking_time)
        await um_cmd.finish(reply_segment + final_reply)
    except FinishedException:
        raise
    except Exception as e:
        logger.exception(f"群印象生成失败: {e}")
        await um_cmd.finish(reply_segment + "脑子短路了...")


# ================= 功能 3：随机插嘴 (AutoChat) =================
AUTO_CHAT_COOLDOWN = {}

random_chat = on_message(rule=is_in_auto_group, priority=99, block=False)

@random_chat.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now_ts = time.time()
    if is_sleeping():
        return
    now = datetime.datetime.now(TZ_CN)

    msg = event.get_plaintext().strip()
    if _should_skip_random_chat(msg): return

    group_id = str(event.group_id)
    last_time = AUTO_CHAT_COOLDOWN.get(group_id, 0)
    if time.time() < get_safe_until():
        return
    if now_ts - last_time < 10:
        return

    if random.random() > CHAT_PROBABILITY: return

    AUTO_CHAT_COOLDOWN[group_id] = now_ts

    reply_segment = MessageSegment.reply(event.message_id)
    group_context = get_group_context(
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

    system_prompt = _build_auto_chat_system_prompt(
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
    )

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
            inner_os = response_data.get("inner_os", "")
            if inner_os:
                logger.info(f"💦【小彰潜水OS】: {inner_os}")
            anchor = response_data.get("anchor", "").strip()
            reply = response_data.get("reply", "").strip()

            if not _is_grounded_random_reply(msg, anchor, reply):
                logger.warning(f"⚠️ [AutoChat] 当前消息锚点校验失败，静音丢弃: anchor={anchor!r} reply={reply[:40]!r}")
                return

            clean_msg = msg.strip("。，！？.!?~ \n\r")
            clean_reply = reply.strip("。，！？.!?~ \n\r")

            if len(clean_reply) >= 4 and (clean_reply in clean_msg or clean_msg in clean_reply):
                logger.warning(f"⚠️ [AutoChat] 触发片段/缝合复读拦截！静音丢弃: {reply}")
                return

        except json.JSONDecodeError:
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
                    return
                logger.info(f"🔧 插嘴救援成功，reply={repr(reply[:40])}")
                # reply 为空 = 模型决定静默，走正常静默流程
            else:
                return

        if "念叨" in reply or "自言自语" in reply: return
        if not reply or reply.strip() == "……": return
        if len(reply) < 2: return

        save_my_response(str(event.group_id), str(bot.self_id), reply)

        base_delay = random.uniform(1.5, 3.5)
        typing_delay = len(reply) * 0.15
        total_delay = base_delay + typing_delay
        if total_delay > 8: total_delay = 8

        await asyncio.sleep(total_delay)
        await random_chat.finish(reply_segment + reply)
    except FinishedException:
        raise
    except Exception as e:
        # 随机插嘴是尽力而为的可选行为：失败只记日志，不打扰群聊
        logger.debug(f"💦 随机插嘴流程异常，本次静默放弃: {e}")
