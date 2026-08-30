"""Midnight publishing and retention jobs for daily word-cloud reports."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from nonebot import get_bot, get_driver, require
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402

from ...core import TZ_CN, WORDCLOUD_GROUPS, grant_safety_pass  # noqa: E402
from . import analysis, store  # noqa: E402
from .render import render_report  # noqa: E402

_PUBLISH_LOCK = asyncio.Lock()


async def ensure_report(group_id: str, report_date: date) -> dict[str, Any]:
    existing = await store.load_report(group_id, report_date.isoformat())
    if existing is not None:
        return existing
    return await analysis.aggregate_report(group_id, report_date)


async def publish_group_report(bot: Bot, group_id: int, report_date: date) -> bool:
    report = await ensure_report(str(group_id), report_date)
    if not report.get("frequencies"):
        return False
    if not await store.report_needs_delivery(str(group_id), report_date.isoformat()):
        return False

    image = await render_report(report)
    grant_safety_pass(10)
    await bot.send_group_msg(group_id=group_id, message=MessageSegment.image(image))
    await store.mark_report_sent(str(group_id), report_date.isoformat())
    return True


async def publish_daily_reports(*, bot: Bot | None = None, today: date | None = None) -> int:
    """Publish yesterday once per configured group and prune expired raw bodies."""
    async with _PUBLISH_LOCK:
        current_date = today or datetime.now(TZ_CN).date()
        report_date = current_date - timedelta(days=1)
        current_bot = bot or (get_bot() if WORDCLOUD_GROUPS else None)
        sent_count = 0

        for group_id in WORDCLOUD_GROUPS:
            try:
                assert current_bot is not None
                if await publish_group_report(current_bot, group_id, report_date):
                    sent_count += 1
                    await asyncio.sleep(1)
            except Exception as exc:
                logger.error(f"[每日词云] 群 {group_id} 的 {report_date.isoformat()} 日报处理失败: {exc}")

        try:
            deleted = await store.cleanup_raw_messages(analysis.retention_cutoff(current_date))
            if deleted:
                logger.info(f"[每日词云] 已清理 {deleted} 条超过 {analysis.RAW_RETENTION_DAYS} 天的原始消息")
        except Exception as exc:
            logger.error(f"[每日词云] 清理过期原始消息失败: {exc}")
        return sent_count


@scheduler.scheduled_job(
    "cron",
    hour=0,
    minute=0,
    id="daily_group_wordcloud",
    timezone=TZ_CN,
    max_instances=1,
    coalesce=True,
)
async def daily_group_wordcloud() -> None:
    await publish_daily_reports()


async def recover_yesterday_report(bot: Bot) -> None:
    """Retry only yesterday's unsent report when a bot connects."""
    await publish_daily_reports(bot=bot)


_driver = get_driver()
if hasattr(_driver, "on_bot_connect"):
    _driver.on_bot_connect(recover_yesterday_report)
