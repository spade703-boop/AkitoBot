"""Command handlers for the RPG metrics dashboard."""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from ....core import SUPERUSER_QQ
from . import analytics

rpg_metrics_cmd = on_command("RPG数据", priority=5, block=True)
style_test_cmd = on_command("看板样式测试", priority=5, block=True)


@rpg_metrics_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = analytics._resolve_group(event)
    if rejection:
        await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = analytics._today_str()
    async with analytics.LOCK:
        data = analytics._load_data()
        group = analytics._get_group(data, group_id)
        page_data = analytics.build_metrics_page_data(group, today)
        report = analytics.build_metrics_report(group, today)
    image = await analytics._render_metrics_image(page_data)
    if image is not None:
        await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))
    await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + report)


@style_test_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = analytics._resolve_group(event)
    if rejection:
        await style_test_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = analytics._today_str()
    group = analytics._build_style_test_group(today)
    page_data = analytics.build_metrics_page_data(group, today)
    image = await analytics._render_metrics_image(page_data)
    reply_prefix = MessageSegment.reply(event.message_id)
    if image is not None:
        await style_test_cmd.finish(reply_prefix + MessageSegment.image(image))
    await style_test_cmd.finish(
        reply_prefix + "看板样式测试渲染失败，已切回文字预览。\n" + analytics.build_metrics_report(group, today)
    )
