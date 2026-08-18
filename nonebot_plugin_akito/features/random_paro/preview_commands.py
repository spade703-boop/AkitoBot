"""Superuser-only preview command registrations for random_paro."""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment

from ...core import SUPERUSER_QQ
from .commands import event_display_name, render_draw_result_preview_image, resolve_group_command
from .preview import (
    render_egg_rank_preview_image,
    render_personal_preview_image,
    render_rank_preview_image,
)
from .store import PARO_DATA


def _superuser(event: Event) -> bool:
    return str(event.get_user_id()) == SUPERUSER_QQ


def _register_rank_preview(command, scope: str, renderer):
    @command.handle()
    async def _handler(event: Event):
        if not _superuser(event):
            return
        group_id, rejection = resolve_group_command(event)
        if rejection:
            await command.finish(MessageSegment.reply(event.message_id) + rejection)
        if group_id is None:
            return
        await command.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(await renderer(scope)))


test_daily_rank_cmd = on_command("测试每日排行", priority=5, block=True)
test_history_rank_cmd = on_command("测试历史排行", priority=5, block=True)
test_daily_egg_rank_cmd = on_command("测试每日做饭排行", priority=5, block=True)
test_history_egg_rank_cmd = on_command("测试历史做饭排行", priority=5, block=True)
_register_rank_preview(test_daily_rank_cmd, "daily", render_rank_preview_image)
_register_rank_preview(test_history_rank_cmd, "history", render_rank_preview_image)
_register_rank_preview(test_daily_egg_rank_cmd, "daily", render_egg_rank_preview_image)
_register_rank_preview(test_history_egg_rank_cmd, "history", render_egg_rank_preview_image)


test_my_paro_cmd = on_command("测试我的派生", priority=5, block=True)


@test_my_paro_cmd.handle()
async def _test_personal_handler(event: Event):
    if not _superuser(event):
        return
    group_id, rejection = resolve_group_command(event)
    if rejection:
        await test_my_paro_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    await test_my_paro_cmd.finish(
        MessageSegment.reply(event.message_id) + MessageSegment.image(await render_personal_preview_image())
    )


test_egg_cmd = on_command("test做饭", priority=5, block=True)
test_multi_cmd = on_command("test多派生", priority=5, block=True)
test_fr_cmd = on_command("test狐兔彩蛋", priority=5, block=True)
test_foxbun_cmd = on_command("test狐兔饭", priority=5, block=True)


def _register_draw_preview(command, results_factory):
    @command.handle()
    async def _handler(event: Event):
        if not _superuser(event):
            return
        nickname = event_display_name(event)
        image = await render_draw_result_preview_image(results_factory(), remaining=2, nickname=nickname)
        await command.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))


def _pools():
    akito = PARO_DATA.get("akito_pool") or ["黑百合", "白骑"]
    toya = PARO_DATA.get("toya_pool") or ["王子冬", "黑骑"]
    return akito, toya


_register_draw_preview(test_egg_cmd, lambda: [("Callboy彰", "Callboy冬", True, None)])
_register_draw_preview(
    test_multi_cmd,
    lambda: (
        lambda pools: [
            (pools[0][0], pools[1][0], True, None),
            (pools[0][1 % len(pools[0])], pools[1][1 % len(pools[1])], True, None),
            (pools[0][0], pools[1][1 % len(pools[1])], False, None),
        ]
    )(_pools()),
)
_register_draw_preview(
    test_fr_cmd,
    lambda: (
        lambda pools: [
            (pools[0][0], pools[1][0], False, "fox"),
            (pools[0][1 % len(pools[0])], pools[1][1 % len(pools[1])], False, None),
            (pools[0][0], pools[1][1 % len(pools[1])], False, "foxrabbit"),
        ]
    )(_pools()),
)
_register_draw_preview(
    test_foxbun_cmd,
    lambda: (
        lambda pools: [
            (pools[0][0], pools[1][0], True, None),
            (pools[0][1 % len(pools[0])], pools[1][1 % len(pools[1])], False, "foxbun"),
            (pools[0][0], pools[1][1 % len(pools[1])], False, None),
        ]
    )(_pools()),
)


__all__ = [name for name in globals() if name.endswith("_cmd")]
