"""Daily group word-cloud collection and report entrypoint."""

from __future__ import annotations

import time

from nonebot import on_message
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.log import logger

from ...core import WORDCLOUD_GROUPS
from . import analysis, store

recorder = on_message(priority=2, block=False)


@recorder.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if event.group_id not in WORDCLOUD_GROUPS or str(event.user_id) == str(bot.self_id):
        return
    content = event.get_plaintext().strip()
    if not analysis.is_recordable_text(content):
        return
    message_id = str(getattr(event, "message_id", "")).strip()
    if not message_id:
        return
    sender = event.sender
    nickname = (sender.card or sender.nickname or f"用户{event.user_id}").strip()
    event_time = int(getattr(event, "time", time.time()))
    try:
        await store.record_raw_message(
            str(event.group_id),
            str(event.user_id),
            nickname,
            content,
            message_id,
            event_time,
        )
    except Exception as exc:
        logger.error(f"[每日词云] 记录群 {event.group_id} 消息失败: {exc}")


from . import commands, jobs  # noqa: E402,F401
