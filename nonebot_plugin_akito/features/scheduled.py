import asyncio
import random
import time
import datetime
from nonebot import get_bot, require
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler

from ..core import MEMORY_DB, save_memory, TARGET_GROUPS, REACTIONS_DB, TZ_CN, grant_safety_pass

# ==============================================================================
# 定时任务模块 (SCHEDULED TASKS)
# ==============================================================================

@scheduler.scheduled_job("interval", hours=1, id="clean_expired_memory")
async def clean_expired_memory():
    """每小时清理一次过期的临时记忆"""
    now = time.time()
    cleaned_count = 0
    for key in list(MEMORY_DB.keys()):
        memory = MEMORY_DB[key]
        if "temp_implants" in memory:
            original_len = len(memory["temp_implants"])
            memory["temp_implants"] = [
                i for i in memory["temp_implants"]
                if i.get("expire_at", i.get("expire_time", 0)) > now
            ]
            cleaned_count += (original_len - len(memory["temp_implants"]))

    if cleaned_count > 0:
        save_memory()
        logger.info(f"🧹 定时任务：已清理 {cleaned_count} 条过期临时记忆")


@scheduler.scheduled_job("cron", hour=6, minute=0, id="akito_morning", timezone=TZ_CN)
async def akito_morning():
    """早安问候"""
    try:
        bot = get_bot()
        morning_quotes = REACTIONS_DB.get("greetings", {}).get("morning") or ["早。"]
        msg = random.choice(morning_quotes)
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
async def akito_night():
    """晚安问候"""
    try:
        bot = get_bot()
        night_quotes = REACTIONS_DB.get("greetings", {}).get("night") or ["晚安。"]
        msg = random.choice(night_quotes)
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
