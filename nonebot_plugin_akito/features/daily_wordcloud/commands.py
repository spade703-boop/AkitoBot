"""Superuser report queries, recomputation, and blocked-word management."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ, TZ_CN, WORDCLOUD_GROUPS
from . import analysis, store
from .jobs import ensure_report
from .render import render_report


def _is_superuser(event: Event) -> bool:
    return str(event.get_user_id()) == SUPERUSER_QQ


def _target_group_id(event: Event) -> str | None:
    group_id = getattr(event, "group_id", None)
    if group_id is None or int(group_id) not in WORDCLOUD_GROUPS:
        return None
    return str(group_id)


def _parse_date_argument(raw: str, *, today: date | None = None, default_yesterday: bool = False) -> tuple[date | None, str | None]:
    current_date = today or datetime.now(TZ_CN).date()
    value = raw.strip()
    if not value and default_yesterday:
        return current_date - timedelta(days=1), None
    if not value:
        return None, "请指定日期，例如：重算群聊词云 2026-08-29"
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, "日期格式应为 YYYY-MM-DD。"
    if parsed >= current_date:
        return None, "只能查询或重算已经结束的自然日。"
    return parsed, None


query_cmd = on_command("群聊词云", priority=5, block=True)


@query_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _is_superuser(event):
        return
    group_id = _target_group_id(event)
    if group_id is None:
        return
    report_date, error = _parse_date_argument(args.extract_plain_text(), default_yesterday=True)
    if error:
        await query_cmd.finish(error)
    assert report_date is not None

    report = await store.load_report(group_id, report_date.isoformat())
    if report is None:
        earliest_raw_date = datetime.now(TZ_CN).date() - timedelta(days=analysis.RAW_RETENTION_DAYS)
        if report_date < earliest_raw_date:
            await query_cmd.finish("该日期没有已保存的日报，原始消息也已超过 7 天留存期。")
        report = await ensure_report(group_id, report_date)
    if not report.get("frequencies"):
        await query_cmd.finish("该日暂无有效聊天文本。")
    image = await render_report(report)
    await query_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))


test_cmd = on_command("测试群聊词云", priority=5, block=True)


@test_cmd.handle()
async def _(event: Event):
    """Render a non-persistent demo card so an administrator can inspect the layout."""
    if not _is_superuser(event):
        return
    demo_report = {
        "group_id": str(getattr(event, "group_id", "demo")),
        "report_date": datetime.now(TZ_CN).date().isoformat(),
        "message_count": 128,
        "participant_count": 17,
        "frequencies": [
            ["彰冬", 36], ["练习", 29], ["咖啡", 24], ["直播", 20], ["活动", 17],
            ["周末", 15], ["新曲", 13], ["akito", 11], ["toya", 9], ["晚安", 8],
        ],
        "top_words": [
            {
                "word": "彰冬",
                "count": 36,
                "contributors": [
                    {"user_id": "10001", "nickname": "橘子汽水", "count": 12},
                    {"user_id": "10002", "nickname": "夜航星", "count": 9},
                    {"user_id": "10003", "nickname": "蓝莓苏打", "count": 7},
                    {"user_id": "10004", "nickname": "小熊饼干", "count": 5},
                    {"user_id": "10005", "nickname": "玻璃海", "count": 3},
                ],
            },
            {
                "word": "练习",
                "count": 29,
                "contributors": [
                    {"user_id": "10002", "nickname": "夜航星", "count": 10},
                    {"user_id": "10006", "nickname": "白桃乌龙", "count": 8},
                    {"user_id": "10001", "nickname": "橘子汽水", "count": 6},
                ],
            },
            {
                "word": "咖啡",
                "count": 24,
                "contributors": [
                    {"user_id": "10007", "nickname": "月面散步", "count": 11},
                    {"user_id": "10003", "nickname": "蓝莓苏打", "count": 7},
                    {"user_id": "10008", "nickname": "午后电台", "count": 6},
                ],
            },
        ],
    }
    image = await render_report(demo_report)
    await test_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))


rebuild_cmd = on_command("重算群聊词云", priority=5, block=True)


@rebuild_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _is_superuser(event):
        return
    group_id = _target_group_id(event)
    if group_id is None:
        return
    report_date, error = _parse_date_argument(args.extract_plain_text())
    if error:
        await rebuild_cmd.finish(error)
    assert report_date is not None

    earliest_raw_date = datetime.now(TZ_CN).date() - timedelta(days=analysis.RAW_RETENTION_DAYS)
    if report_date < earliest_raw_date:
        await rebuild_cmd.finish("该日期已超过 7 天原始消息留存期，无法重算。")
    report = await analysis.aggregate_report(group_id, report_date)
    if not report.get("frequencies"):
        await rebuild_cmd.finish("重算完成，但该日没有有效聊天文本。")
    image = await render_report(report)
    await rebuild_cmd.finish(
        MessageSegment.reply(event.message_id) + "重算完成。\n" + MessageSegment.image(image)
    )


blocked_words_cmd = on_command("词云屏蔽词", priority=5, block=True)


@blocked_words_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if not _is_superuser(event):
        return
    raw = args.extract_plain_text().strip()
    if not raw or raw in {"查看", "列表"}:
        words = await store.list_blocked_words()
        message = "当前没有额外的词云屏蔽词。" if not words else "词云屏蔽词：\n" + "、".join(words)
        await blocked_words_cmd.finish(message)

    command_parts = raw.split(None, 1)
    action = command_parts[0]
    values = command_parts[1] if len(command_parts) > 1 else ""
    words = analysis.parse_blocked_word_arguments(values)
    if action in {"添加", "屏蔽"}:
        if not words:
            await blocked_words_cmd.finish("用法：词云屏蔽词 添加 词1 词2")
        changed = await store.add_blocked_words(words, event.get_user_id())
        await blocked_words_cmd.finish(
            f"已新增 {changed} 个屏蔽词。需要修改近 7 天旧日报时，请执行“重算群聊词云 YYYY-MM-DD”。"
        )
    if action in {"取消", "删除", "移除"}:
        if not words:
            await blocked_words_cmd.finish("用法：词云屏蔽词 取消 词1 词2")
        changed = await store.remove_blocked_words(words)
        await blocked_words_cmd.finish(
            f"已取消 {changed} 个屏蔽词。需要修改近 7 天旧日报时，请执行“重算群聊词云 YYYY-MM-DD”。"
        )
    await blocked_words_cmd.finish("用法：词云屏蔽词 查看｜添加 词1 词2｜取消 词1 词2")
