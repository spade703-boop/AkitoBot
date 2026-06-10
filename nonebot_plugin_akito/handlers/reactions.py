"""被动反应：戳一戳互动、深夜自我吐槽。"""

import asyncio
import datetime
import random
import time

from nonebot import on, on_notice
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import NoticeEvent, PokeNotifyEvent
from nonebot.log import logger

from ..core import (
    AKITO_STATUS,
    ALLOWED_CHAT_GROUPS,
    DAILY_ROUTINE,
    SLEEP_DB,
    TZ_CN,
    get_daily_activity,
    get_last_complaint,
    get_memory_key,
    get_safe_until,
    get_user_memory,
    grant_safety_pass,
    is_sleeping,
    set_last_complaint,
    sleep_block,
)

# 注：冬弥去向推断已并入 handlers/chat.py 主对话引擎（core.life_state.get_toya_anchor），
# 原独立的「冬弥呢」指令（toya_status_cmd / get_toya_location_reply）已移除。


def _has_wl2_implant(mem: dict) -> bool:
    """Return True when the current memory indicates WL2 mode."""
    return any(item.get("id") == "WL2" for item in mem.get("temp_implants", []))


def _resolve_poke_reactions(current_state: object, daily_routine: dict) -> list[str]:
    """Return poke replies from the current state, falling back to defaults."""
    reactions = current_state.get("poke", []) if isinstance(current_state, dict) else []
    return reactions or daily_routine.get("fallback_poke", ["喂，别乱戳啊。"])


def _should_skip_self_complaint(
    *,
    sleeping: bool,
    now_ts: float,
    safe_until: float,
    last_complaint: float,
    group_key: str,
    superuser_times: dict[str, float],
) -> bool:
    """Return True when the late-night self complaint should not be sent."""
    if not sleeping or now_ts < safe_until or now_ts - last_complaint < 10:
        return True
    return now_ts - superuser_times.get(group_key, 0) < 30


# --- 1. 戳一戳互动 ---
poke = on_notice()
@poke.handle()
async def _(bot: Bot, event: NoticeEvent):
    is_poke = isinstance(event, PokeNotifyEvent) or getattr(event, "sub_type", "") == "poke"
    if not is_poke or str(getattr(event, "target_id", "")) != str(bot.self_id): return
    group_id = getattr(event, "group_id", None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS: return

    mem = get_user_memory(get_memory_key(event))
    if _has_wl2_implant(mem):
        grant_safety_pass(5)
        await poke.finish(random.choice([
            "……别碰我，我现在没心情理人。",
            "……干什么？",
            "有事快说。",
            "啧，你很闲吗？"
        ]))

    result = sleep_block("sleep_poke", silent_chance=0.0,
                         fallback="（这只松饼正在睡觉，没有理会你的戳）")
    if result:
        grant_safety_pass(5)
        await poke.finish(result)
        return

    now_full = datetime.datetime.now(TZ_CN)
    get_daily_activity(now_full.hour, now_full.weekday(), now_full.minute)
    current_state = AKITO_STATUS.get("cached_content", "")
    reactions = _resolve_poke_reactions(current_state, DAILY_ROUTINE)

    await asyncio.sleep(random.uniform(0.5, 1.5))
    grant_safety_pass(5)
    await poke.finish(random.choice(reactions))


# --- 2. 自身消息监控 (自我吐槽) ---
async def is_self_message(bot: Bot, event: Event) -> bool:
    """规则：判断是否是 bot 自己发出的消息（post_type == message_sent）。"""
    return getattr(event, "post_type", "") == "message_sent"

self_monitor = on(rule=is_self_message, priority=99, block=False)
@self_monitor.handle()
async def _(bot: Bot, event: Event):
    group_id = getattr(event, "group_id", None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS:
        return

    now_ts = time.time()
    # 该群30秒内由超管触发过回复，跳过抱怨（防止测试时刷抱怨）
    # 使用 per-group 时间戳，避免超管在 A 群说话后误压制 B 群的抱怨
    group_key = str(getattr(event, "group_id", "private"))
    superuser_times = AKITO_STATUS.get("last_superuser_trigger_time", {})
    if _should_skip_self_complaint(
        sleeping=is_sleeping(),
        now_ts=now_ts,
        safe_until=get_safe_until(),
        last_complaint=get_last_complaint(),
        group_key=group_key,
        superuser_times=superuser_times,
    ):
        return
    set_last_complaint(now_ts)
    await asyncio.sleep(random.uniform(2.0, 4.0))
    grant_safety_pass(10)
    try:
        group_id = getattr(event, "group_id", None)
        if group_id: await bot.send_group_msg(group_id=group_id, message=random.choice(SLEEP_DB.get("complaints", ["……困……"])))
    except Exception as e:
        logger.debug(f"😴 自我吐槽发送失败，忽略: {e}")
