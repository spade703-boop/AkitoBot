"""定时任务：每小时清理过期临时记忆、世界BOSS跨天收尾、每日早安 / 晚安群发问候。"""

import asyncio
import random
import time

from nonebot import get_bot, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from ...core import MEMORY_DB, REACTIONS_DB, TARGET_GROUPS, TZ_CN, grant_safety_pass, save_memory  # noqa: E402
from ...core.game_store import LOCK, _get_group, _load_data, _save_data, _today_str  # noqa: E402
from ..rpg.boss import _cleanup_stale_world_boss  # noqa: E402

# ==============================================================================
# 定时任务模块 (SCHEDULED TASKS)
# ==============================================================================


def _clean_memory_db(memory_db: dict, now_ts: float) -> int:
    """Prune expired temp implants in-place and return the total removed count."""
    cleaned_count = 0
    for key in list(memory_db.keys()):
        memory = memory_db[key]
        if "temp_implants" not in memory:
            continue
        original_len = len(memory["temp_implants"])
        memory["temp_implants"] = [
            item for item in memory["temp_implants"]
            if item.get("expire_at", item.get("expire_time", 0)) > now_ts
        ]
        cleaned_count += original_len - len(memory["temp_implants"])
    return cleaned_count


def _get_scheduled_greeting(period: str, reactions_db: dict) -> str:
    """Pick the morning or night greeting with a safe default."""
    defaults = {"morning": ["早。"], "night": ["晚安。"]}
    quotes = reactions_db.get("greetings", {}).get(period) or defaults[period]
    return random.choice(quotes)


def _collect_world_boss_settlements(data: dict, today: str, target_groups: list[int]) -> list[tuple[int, str]]:
    """Collect cross-day world boss settlement broadcasts for groups with stale bosses."""
    broadcasts: list[tuple[int, str]] = []
    changed = False
    for gid in target_groups:
        group = _get_group(data, gid)
        lines, group_changed = _cleanup_stale_world_boss(group, today)
        if not lines:
            continue
        changed = changed or group_changed
        broadcasts.append((int(gid), "\n".join(lines)))
    if changed:
        _save_data(data)
    return broadcasts


@scheduler.scheduled_job("interval", hours=1, id="clean_expired_memory")
async def clean_expired_memory() -> None:
    """每小时清理一次过期的临时记忆"""
    cleaned_count = _clean_memory_db(MEMORY_DB, time.time())

    if cleaned_count > 0:
        save_memory()
        logger.info(f"🧹 定时任务：已清理 {cleaned_count} 条过期临时记忆")


@scheduler.scheduled_job("cron", hour=0, minute=0, id="world_boss_settlement", timezone=TZ_CN)
async def world_boss_settlement() -> None:
    """跨天时主动结算昨天未完成的世界BOSS，并向对应群广播。"""
    try:
        data = _load_data()
        today = _today_str()
        async with LOCK:
            broadcasts = _collect_world_boss_settlements(data, today, TARGET_GROUPS)
        if not broadcasts:
            return

        bot = get_bot()
        grant_safety_pass(10)
        for gid, msg in broadcasts:
            try:
                await bot.send_group_msg(group_id=gid, message=msg)
                logger.info(f"[定时任务] 已向群 {gid} 发送世界BOSS跨天结算")
                await asyncio.sleep(1)
            except Exception as e_inner:
                logger.error(f"[定时任务] 世界BOSS结算发送给群 {gid} 失败: {e_inner}")
    except Exception as e:
        logger.error(f"[定时任务] 世界BOSS跨天结算执行出错: {e}")


@scheduler.scheduled_job("cron", hour=6, minute=0, id="akito_morning", timezone=TZ_CN)
async def akito_morning() -> None:
    """早安问候"""
    try:
        bot = get_bot()
        msg = _get_scheduled_greeting("morning", REACTIONS_DB)
        grant_safety_pass(10)
        for gid in TARGET_GROUPS:
            try:
                await bot.send_group_msg(group_id=gid, message=msg)
                logger.info(f"[定时任务] 已向群 {gid} 发送早安")
                await asyncio.sleep(1)
            except Exception as e_inner:
                logger.error(f"[定时任务] 发送给群 {gid} 失败: {e_inner}")
    except Exception as e:
        logger.error(f"[定时任务] 早安任务执行出错: {e}")


@scheduler.scheduled_job("cron", hour=23, minute=50, id="akito_night", timezone=TZ_CN)
async def akito_night() -> None:
    """晚安问候"""
    try:
        bot = get_bot()
        msg = _get_scheduled_greeting("night", REACTIONS_DB)
        grant_safety_pass(10)
        for gid in TARGET_GROUPS:
            try:
                await bot.send_group_msg(group_id=gid, message=msg)
                logger.info(f"[定时任务] 已向群 {gid} 发送晚安")
                await asyncio.sleep(1)
            except Exception as e_inner:
                logger.error(f"[定时任务] 发送给群 {gid} 失败: {e_inner}")
    except Exception as e:
        logger.error(f"[定时任务] 晚安任务执行出错: {e}")
