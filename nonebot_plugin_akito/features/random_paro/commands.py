"""Production and admin command handlers for random_paro."""

from __future__ import annotations

import asyncio
import os
import random
import time

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from ...core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ
from .assets import find_avatar
from .draw import (
    DRAW_LIMIT,
    DRAW_WINDOW,
    build_draw_limit_message,
    draw_results,
    get_fixed_side,
    parse_draw_request,
    prune_draw_history,
    resolve_directional_draw,
)
from .draw_images import render_composite, render_multi, render_pool_image, render_text_only
from .stats import _cooldown_store, _record_group_draw_stats
from .store import PARO_DATA, _save, _save_stats
from .views import (
    _build_draw_result_page_data,
    _render_html_page,
    render_egg_rank_image,
    render_paro_rank_image,
    render_personal_paro_image,
)

PARO_USE_HTML_RENDER = os.environ.get("PARO_USE_HTML_RENDER", "1").strip().lower() not in {"0", "false"}
_DRAW_LOCKS: dict[str, asyncio.Lock] = {}
_PARO_HELP_TEXT = (
    "🎲 派生抽取器\n"
    "━━━━━━━━━━━━━━\n"
    "· 抽派生 — 双方随机抽取\n"
    "· 抽派生 彰人 [派生名] — 固定彰人，冬弥随机\n"
    "· 抽派生 冬弥 [派生名] — 固定冬弥，彰人随机\n"
    "· 我的派生 — 查看个人累计抽取、做饭次数和个人派生 TOP 3\n"
    "· 每日排行 / 历史排行 — 查看本群派生抽取排行\n"
    "· 每日做饭排行 / 历史做饭排行 — 查看本群做饭彩蛋排行\n"
    "· 查看彰人派生 / 查看冬弥派生 — 查看当前派生池\n\n"
    "💡 30 分钟内最多抽 3 次；定向抽取也会计入个人累计和全群派生角色榜累计。"
)


def resolve_group_command(event: Event) -> tuple[int | None, str | None]:
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None, "该指令仅支持群聊使用。"
    if group_id not in ALLOWED_CHAT_GROUPS:
        return None, None
    return int(group_id), None


def event_display_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        display_name = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name
    return f"用户{event.get_user_id()}"


async def render_draw_result_image(
    results: list[tuple[str, str, bool, str | None]], remaining: int, nickname: str
) -> bytes:
    data = _build_draw_result_page_data(results, remaining, nickname)
    return await _render_html_page(
        "draw_result.html", data, fallback=lambda: render_multi(results, remaining, nickname)
    )


async def render_draw_result_preview_image(results, remaining: int, nickname: str) -> bytes:
    if PARO_USE_HTML_RENDER:
        return await render_draw_result_image(results, remaining, nickname)
    return render_multi(results, remaining, nickname)


help_cmd = on_command("派生帮助", priority=5, block=True)


@help_cmd.handle()
async def _help_handler(event: Event, args: Message = CommandArg()):
    if args and args.extract_plain_text().strip():
        return
    group_id = getattr(event, "group_id", None)
    if group_id is None or group_id not in ALLOWED_CHAT_GROUPS:
        return
    await help_cmd.finish(MessageSegment.reply(event.message_id) + _PARO_HELP_TEXT)


draw_cmd = on_command("抽派生", priority=5, block=True)


@draw_cmd.handle()
async def _draw_handler(event: Event, args: Message = CommandArg()):
    group_id, rejection = resolve_group_command(event)
    if rejection:
        await draw_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    user_id = event.get_user_id()
    lock = _DRAW_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        akito_pool = PARO_DATA.get("akito_pool", [])
        toya_pool = PARO_DATA.get("toya_pool", [])
        if not akito_pool:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id) + "彰人的派生池还是空的，先用 /添加彰人派生 添加一些吧。"
            )
        if not toya_pool:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id) + "冬弥的派生池还是空的，先用 /添加冬弥派生 添加一些吧。"
            )

        count, directional = parse_draw_request(args.extract_plain_text())
        fixed_a, fixed_b, directional_error = resolve_directional_draw(directional, akito_pool, toya_pool)
        if directional_error:
            await draw_cmd.finish(directional_error)

        now = time.time()
        cooldowns = _cooldown_store()
        previous_history = list(cooldowns.get(user_id, []))
        history = prune_draw_history(previous_history, now, DRAW_WINDOW)
        cooldowns[user_id] = history
        if history != previous_history:
            _save_stats()
        remaining_before = DRAW_LIMIT - len(history)
        limit_message = build_draw_limit_message(
            remaining_before=remaining_before,
            requested_count=count,
            history=history,
            now_ts=now,
        )
        if limit_message:
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + limit_message)

        nickname = event_display_name(event)
        results = draw_results(count, fixed_a=fixed_a, fixed_b=fixed_b, akito_pool=akito_pool, toya_pool=toya_pool)
        history.extend([now] * count)
        cooldowns[user_id] = history
        remaining = DRAW_LIMIT - len(history)
        _record_group_draw_stats(
            group_id=group_id,
            user_id=user_id,
            display_name=nickname,
            results=results,
            fixed_side=get_fixed_side(fixed_a, fixed_b),
            fixed_name=fixed_a or fixed_b,
            requested_count=count,
            now_ts=now,
        )
        await asyncio.sleep(random.uniform(0.4, 0.8))
        if PARO_USE_HTML_RENDER:
            image = await render_draw_result_image(results, remaining, nickname)
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))

        if count == 1 and not any(fox_type for _, _, _, fox_type in results):
            akito, toya, is_egg, _ = results[0]
            if is_egg:
                text_lines = [
                    f"@{nickname}：",
                    "对，就是你，你是被选中的彰冬姐，",
                    [
                        ("奖励你现在来做", "#000000", True),
                        (akito, "#FF7722", True),
                        ("×", "#000000", True),
                        (toya, "#0077DD", True),
                        ("的饭！", "#000000", True),
                    ],
                ]
            else:
                text_lines = [
                    [
                        ("你抽到的派生是：", "#000000", False),
                        (akito, "#FF7722", True),
                        ("×", "#000000", True),
                        (toya, "#0077DD", True),
                        ("。", "#000000", False),
                    ],
                    f"（30分钟内剩余 {remaining} 次）",
                ]
            if find_avatar("彰人", akito) and find_avatar("冬弥", toya):
                image = render_composite(akito, toya, text_lines)
                await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))
            if is_egg:
                await draw_cmd.finish(
                    MessageSegment.reply(event.message_id) + MessageSegment.image(render_text_only(text_lines))
                )
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id)
                + f"你抽到的派生是：{akito}×{toya}。（30分钟内剩余 {remaining} 次）"
            )
        await draw_cmd.finish(
            MessageSegment.reply(event.message_id) + MessageSegment.image(render_multi(results, remaining, nickname))
        )


my_paro_cmd = on_command("我的派生", priority=5, block=True)


@my_paro_cmd.handle()
async def _my_paro_handler(event: Event):
    group_id, rejection = resolve_group_command(event)
    if rejection:
        await my_paro_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    await my_paro_cmd.finish(
        MessageSegment.reply(event.message_id)
        + MessageSegment.image(
            await render_personal_paro_image(group_id, event.get_user_id(), event_display_name(event))
        )
    )


def _make_rank_handler(command, scope: str, builder):
    @command.handle()
    async def _handler(event: Event):
        group_id, rejection = resolve_group_command(event)
        if rejection:
            await command.finish(MessageSegment.reply(event.message_id) + rejection)
        if group_id is None:
            return
        await command.finish(
            MessageSegment.reply(event.message_id) + MessageSegment.image(await builder(group_id, scope))
        )


daily_rank_cmd = on_command("每日排行", priority=5, block=True)
history_rank_cmd = on_command("历史排行", priority=5, block=True)
daily_egg_rank_cmd = on_command("每日做饭排行", priority=5, block=True)
history_egg_rank_cmd = on_command("历史做饭排行", priority=5, block=True)
_make_rank_handler(daily_rank_cmd, "daily", render_paro_rank_image)
_make_rank_handler(history_rank_cmd, "history", render_paro_rank_image)
_make_rank_handler(daily_egg_rank_cmd, "daily", render_egg_rank_image)
_make_rank_handler(history_egg_rank_cmd, "history", render_egg_rank_image)


add_akito_cmd = on_command("添加彰人派生", priority=5, block=True)
add_toya_cmd = on_command("添加冬弥派生", priority=5, block=True)
del_akito_cmd = on_command("删除彰人派生", priority=5, block=True)
del_toya_cmd = on_command("删除冬弥派生", priority=5, block=True)


def _admin_pool_handler(command, key: str, action: str):
    @command.handle()
    async def _handler(event: Event, args: Message = CommandArg()):
        if str(event.get_user_id()) != SUPERUSER_QQ:
            return
        name = args.extract_plain_text().strip()
        label = "彰人" if key == "akito_pool" else "冬弥"
        if not name:
            verb = "添加" if action == "add" else "删除"
            await command.finish(f"请告诉我要{verb}的派生名称")
        pool = PARO_DATA.setdefault(key, [])
        if action == "add":
            pool.append(name)
            _save()
            await command.finish(f"已将「{name}」加入{label}的派生池（当前共 {len(pool)} 个）。")
        if name not in pool:
            await command.finish(f"{label}的派生池里没有「{name}」这个条目。")
        pool.remove(name)
        _save()
        await command.finish(f"已从{label}的派生池中删除「{name}」。")


_admin_pool_handler(add_akito_cmd, "akito_pool", "add")
_admin_pool_handler(add_toya_cmd, "toya_pool", "add")
_admin_pool_handler(del_akito_cmd, "akito_pool", "delete")
_admin_pool_handler(del_toya_cmd, "toya_pool", "delete")


view_akito_cmd = on_command("查看彰人派生", priority=5, block=True)
view_toya_cmd = on_command("查看冬弥派生", priority=5, block=True)


def _view_pool_handler(command, key: str, title: str):
    @command.handle()
    async def _handler(event: Event):
        if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
            return
        pool = PARO_DATA.get(key, [])
        if not pool:
            await command.finish(f"{title}目前是空的。")
        await command.finish(MessageSegment.image(render_pool_image(title, pool)))


_view_pool_handler(view_akito_cmd, "akito_pool", "彰人的派生池")
_view_pool_handler(view_toya_cmd, "toya_pool", "冬弥的派生池")


__all__ = [
    name
    for name in globals()
    if name.endswith("_cmd") or name.startswith("render_") or name.startswith("resolve_") or name.startswith("event_")
]
