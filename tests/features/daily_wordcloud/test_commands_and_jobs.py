from __future__ import annotations

from datetime import date, datetime
from unittest import mock

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.core import TZ_CN
from nonebot_plugin_akito.features.daily_wordcloud import commands, jobs


def test_date_argument_defaults_to_yesterday_and_rejects_current_day():
    parsed, error = commands._parse_date_argument("", today=date(2026, 8, 30), default_yesterday=True)
    current, current_error = commands._parse_date_argument("2026-08-30", today=date(2026, 8, 30))
    backfill_current, backfill_error = commands._parse_date_argument(
        "2026-08-30",
        today=date(2026, 8, 30),
        allow_today=True,
    )

    assert (parsed, error) == (date(2026, 8, 29), None)
    assert current is None
    assert "已经结束" in current_error
    assert (backfill_current, backfill_error) == (date(2026, 8, 30), None)


async def test_non_superuser_cannot_manage_blocked_words():
    with mock.patch.object(commands.store, "add_blocked_words", new=mock.AsyncMock()) as add_words:
        result = await commands.blocked_words_cmd.handlers[0](
            Event(group_id=1001, user_id="not-superuser"),
            Message("添加 akito"),
        )

    assert result is None
    add_words.assert_not_awaited()


async def test_superuser_can_add_blocked_words():
    with mock.patch.object(commands.store, "add_blocked_words", new=mock.AsyncMock(return_value=2)) as add_words:
        with pytest.raises(FinishedException) as exc_info:
            await commands.blocked_words_cmd.handlers[0](
                Event(group_id=1001, user_id="9001"),
                Message("添加 akito coffee"),
            )

    add_words.assert_awaited_once_with(["akito", "coffee"], "9001")
    assert "已新增 2 个" in str(exc_info.value)


@pytest.mark.parametrize(
    ("command", "handler_name"),
    [
        ("添加", "blocked_words_cmd"),
        ("取消", "blocked_words_cmd"),
        ("添加", "excluded_users_cmd"),
        ("取消", "excluded_users_cmd"),
    ],
)
async def test_management_commands_silently_ignore_missing_arguments(command, handler_name):
    matcher = getattr(commands, handler_name)
    with (
        mock.patch.object(commands.store, "add_blocked_words", new=mock.AsyncMock()) as add_words,
        mock.patch.object(commands.store, "remove_blocked_words", new=mock.AsyncMock()) as remove_words,
        mock.patch.object(commands.store, "add_excluded_user_ids", new=mock.AsyncMock()) as add_users,
        mock.patch.object(commands.store, "remove_excluded_user_ids", new=mock.AsyncMock()) as remove_users,
    ):
        result = await matcher.handlers[0](Event(group_id=1001, user_id="9001"), Message(command))

    assert result is None
    add_words.assert_not_awaited()
    remove_words.assert_not_awaited()
    add_users.assert_not_awaited()
    remove_users.assert_not_awaited()


async def test_superuser_can_render_non_persistent_demo_image():
    with mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"demo-image")) as render_report:
        with pytest.raises(FinishedException) as exc_info:
            await commands.test_cmd.handlers[0](Event(group_id=9999, user_id="9001"))

    render_report.assert_awaited_once()
    demo_report = render_report.await_args.args[0]
    assert len(demo_report["message_volume"]) == 8
    assert sum(item["count"] for item in demo_report["message_volume"]) == demo_report["message_count"]
    assert "[image]" in str(exc_info.value)


async def test_superuser_can_render_wordcloud_command_help_image():
    with mock.patch.object(commands, "render_command_help", new=mock.AsyncMock(return_value=b"help-image")) as render_help:
        with pytest.raises(FinishedException) as exc_info:
            await commands.help_cmd.handlers[0](Event(group_id=1001, user_id="9001"))

    render_help.assert_awaited_once()
    assert "[image]" in str(exc_info.value)


async def test_group_member_can_render_live_wordcloud():
    commands._LIVE_LAST_REQUEST.clear()
    report = {"frequencies": [["hello", 2]], "top_words": []}
    with (
        mock.patch.object(commands.analysis, "aggregate_current_report", new=mock.AsyncMock(return_value=report)) as aggregate,
        mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"live-image")) as render_report,
    ):
        with pytest.raises(FinishedException) as exc_info:
            await commands.live_cmd.handlers[0](Event(group_id=1001, user_id="ordinary-user"))

    aggregate.assert_awaited_once_with("1001")
    render_report.assert_awaited_once_with(report)
    assert "[image]" in str(exc_info.value)


async def test_live_wordcloud_cooldown_is_shared_by_group_members():
    commands._LIVE_LAST_REQUEST.clear()
    assert commands.LIVE_COOLDOWN_SECONDS == 30 * 60
    report = {"frequencies": [["hello", 2]], "top_words": []}
    with (
        mock.patch.object(commands.analysis, "aggregate_current_report", new=mock.AsyncMock(return_value=report)) as aggregate,
        mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"live-image")),
    ):
        with pytest.raises(FinishedException):
            await commands.live_cmd.handlers[0](Event(group_id=1001, user_id="first-user"))
        with pytest.raises(FinishedException) as exc_info:
            await commands.live_cmd.handlers[0](Event(group_id=1001, user_id="second-user"))

    aggregate.assert_awaited_once_with("1001")
    assert "实时词云正在冷却" in str(exc_info.value)


async def test_group_member_can_use_today_argument_for_live_wordcloud():
    commands._LIVE_LAST_REQUEST.clear()
    report = {"frequencies": [["hello", 2]], "top_words": []}
    with (
        mock.patch.object(commands.analysis, "aggregate_current_report", new=mock.AsyncMock(return_value=report)),
        mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"live-image")),
    ):
        with pytest.raises(FinishedException) as exc_info:
            await commands.query_cmd.handlers[0](
                Event(group_id=1001, user_id="ordinary-user"),
                Message("今天"),
            )

    assert "[image]" in str(exc_info.value)


async def test_superuser_can_backfill_report_from_history():
    report = {"frequencies": [["hello", 2]], "top_words": []}
    bot = type("BotStub", (), {"self_id": "bot-id"})()
    with (
        mock.patch.object(commands.analysis, "aggregate_history_report", new=mock.AsyncMock(return_value=report)) as aggregate,
        mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"history-image")),
    ):
        with pytest.raises(FinishedException) as exc_info:
            await commands.backfill_cmd.handlers[0](
                bot,
                Event(group_id=1001, user_id="9001", message_id="backfill-1"),
                Message("2026-08-29"),
            )

    aggregate.assert_awaited_once_with(
        "1001",
        date(2026, 8, 29),
        excluded_user_ids={"bot-id"},
        pending_midnight_refresh=False,
        persist_raw_messages=False,
    )
    assert "[image]" in str(exc_info.value)


async def test_superuser_can_backfill_current_day_from_history():
    report = {"frequencies": [["hello", 2]], "top_words": []}
    bot = type("BotStub", (), {"self_id": "bot-id"})()
    current_date = datetime.now(TZ_CN).date()
    with (
        mock.patch.object(commands.analysis, "aggregate_history_report", new=mock.AsyncMock(return_value=report)) as aggregate,
        mock.patch.object(commands, "render_report", new=mock.AsyncMock(return_value=b"history-image")),
    ):
        with pytest.raises(FinishedException) as exc_info:
            await commands.backfill_cmd.handlers[0](
                bot,
                Event(group_id=1001, user_id="9001", message_id="backfill-today-1"),
                Message(current_date.isoformat()),
            )

    aggregate.assert_awaited_once_with(
        "1001",
        current_date,
        excluded_user_ids={"bot-id"},
        pending_midnight_refresh=True,
        persist_raw_messages=True,
    )
    assert "[image]" in str(exc_info.value)


async def test_midnight_refresh_rebuilds_a_current_day_backfill():
    report = {
        "frequencies": [["partial", 1]],
        "top_words": [],
        jobs.analysis.PENDING_MIDNIGHT_REFRESH_KEY: True,
    }
    refreshed_report = {"frequencies": [["complete", 3]], "top_words": []}
    with (
        mock.patch.object(jobs.store, "load_report", new=mock.AsyncMock(return_value=report)),
        mock.patch.object(jobs.analysis, "aggregate_report", new=mock.AsyncMock(return_value=refreshed_report)) as aggregate,
    ):
        actual = await jobs.ensure_report(
            "1001",
            date(2026, 8, 30),
            refresh_pending=True,
            excluded_user_ids={"bot-id"},
        )

    assert actual == refreshed_report
    aggregate.assert_awaited_once_with(
        "1001",
        date(2026, 8, 30),
    )


async def test_publish_group_report_marks_sent_only_after_success():
    report = {"frequencies": [["hello", 2]], "top_words": []}
    bot = mock.MagicMock()
    bot.send_group_msg = mock.AsyncMock()
    with (
        mock.patch.object(jobs, "ensure_report", new=mock.AsyncMock(return_value=report)),
        mock.patch.object(jobs.store, "report_needs_delivery", new=mock.AsyncMock(return_value=True)),
        mock.patch.object(jobs, "render_report", new=mock.AsyncMock(return_value=b"image")),
        mock.patch.object(jobs.store, "mark_report_sent", new=mock.AsyncMock()) as mark_sent,
    ):
        sent = await jobs.publish_group_report(bot, 1001, date(2026, 8, 29))

    assert sent is True
    bot.send_group_msg.assert_awaited_once()
    mark_sent.assert_awaited_once_with("1001", "2026-08-29")


async def test_publish_group_report_skips_empty_report():
    bot = mock.MagicMock()
    bot.send_group_msg = mock.AsyncMock()
    with mock.patch.object(
        jobs,
        "ensure_report",
        new=mock.AsyncMock(return_value={"frequencies": [], "top_words": []}),
    ):
        sent = await jobs.publish_group_report(bot, 1001, date(2026, 8, 29))

    assert sent is False
    bot.send_group_msg.assert_not_awaited()
