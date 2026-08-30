from __future__ import annotations

from datetime import date
from unittest import mock

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

from nonebot_plugin_akito.features.daily_wordcloud import commands, jobs


def test_date_argument_defaults_to_yesterday_and_rejects_current_day():
    parsed, error = commands._parse_date_argument("", today=date(2026, 8, 30), default_yesterday=True)
    current, current_error = commands._parse_date_argument("2026-08-30", today=date(2026, 8, 30))

    assert (parsed, error) == (date(2026, 8, 29), None)
    assert current is None
    assert "已经结束" in current_error


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
    assert "[image]" in str(exc_info.value)


async def test_superuser_can_render_wordcloud_command_help_image():
    with mock.patch.object(commands, "render_command_help", new=mock.AsyncMock(return_value=b"help-image")) as render_help:
        with pytest.raises(FinishedException) as exc_info:
            await commands.help_cmd.handlers[0](Event(group_id=1001, user_id="9001"))

    render_help.assert_awaited_once()
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
    )
    assert "[image]" in str(exc_info.value)


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
