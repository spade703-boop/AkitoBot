"""Forging and reset commands for the RPG module."""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import LOCK, _display_name, _get_group, _load_data, _save_data, _today_str
from .boss import _active_world_boss, _ensure_boss_participant
from .config import _cfg, _error, _line
from .player import _ensure_player, _grant_equip, _resolve_group


def _forge_cost(fcfg: dict, times: int) -> int:
    costs = fcfg.get("costs", [])
    if isinstance(costs, list) and 0 <= times < len(costs):
        try:
            return int(costs[times])
        except (TypeError, ValueError):
            pass
    return int(fcfg.get("cost_base", 100)) * (times + 1)


def _forge_record(owner: dict, equip_rec: dict, today: str, *, no_equip_error: str, used_error: str, ok_key: str) -> tuple[bool, str]:
    if equip_rec.get("equip_date") != today:
        return False, _error(no_equip_error)
    if equip_rec.get("equip_used"):
        return False, _error(used_error)
    fcfg = _cfg("forge", {})
    times = int(equip_rec.get("equip_forge", 0))
    mx = int(fcfg.get("max_per_day", 5))
    if times >= mx:
        return False, _error("forge_max", max=mx)
    cost = _forge_cost(fcfg, times)
    points = int(owner.get("points", 0))
    if points < cost:
        return False, _error("forge_poor", cost=cost, total=points)
    owner["points"] = points - cost
    equip_rec["equip_forge"] = times + 1
    return True, _line(ok_key, forge=times + 1, cost=cost)


def _forge(user: dict, today: str) -> tuple[bool, str]:
    return _forge_record(
        user,
        user,
        today,
        no_equip_error="forge_no_equip",
        used_error="forge_broken",
        ok_key="forge_ok",
    )


def _sleep_blocked(user_id: str) -> bool:
    return is_sleeping() and str(user_id) != SUPERUSER_QQ


def _forge_world_boss(boss: dict, user_id: str, user: dict, today: str, *, rng=random) -> tuple[bool, str]:
    equip_rec = _ensure_boss_participant(boss, user_id, user, today, rng=rng)
    if equip_rec is None:
        return False, _error("forge_world_boss_no_equip")
    if equip_rec.get("equip_used"):
        return False, _error("forge_world_boss_used")
    fcfg = _cfg("forge", {})
    times = int(equip_rec.get("equip_forge", 0))
    mx = int(fcfg.get("max_per_day", 5))
    if times >= mx:
        return False, _error("forge_max", max=mx)
    cost = _forge_cost(fcfg, times)
    points = int(user.get("points", 0))
    if points < cost:
        return False, _error("forge_poor", cost=cost, total=points)
    user["points"] = points - cost
    equip_rec["equip_forge"] = times + 1
    return True, _line("forge_world_boss_ok", forge=times + 1, cost=cost)


def _rebuy_equip(user: dict, today: str) -> tuple[bool, str]:
    if user.get("equip_date") != today:
        return False, _error("rebuy_no_equip")
    if not user.get("equip_used"):
        return False, _error("rebuy_no_need")
    mx = int(_cfg("equip", {}).get("rebuy_max_per_day", 1))
    if int(user.get("equip_rebuy_count", 0)) >= mx:
        return False, _error("rebuy_limit", max=mx)
    cost = int(_cfg("equip", {}).get("rebuy_cost", 100))
    points = int(user.get("points", 0))
    if points < cost:
        return False, _error("rebuy_poor", cost=cost, total=points)
    user["points"] = points - cost
    user["equip_used"] = False
    user["equip_rebought"] = True
    user["equip_forge"] = 0
    user["equip_rebuy_count"] = int(user.get("equip_rebuy_count", 0)) + 1
    return True, _line("rebuy_ok", cost=cost)


def _reset_group_rpg_equip(group: dict, today: str, rng=random) -> int:
    reset = 0
    for user_id, rec in group.get("users", {}).items():
        if not isinstance(rec, dict):
            continue
        if rec.get("fortune_date") != today and rec.get("signin_last_date") != today:
            continue
        user = _ensure_player(group, user_id)
        _grant_equip(user, today, rng)
        reset += 1
    return reset


forge_cmd = on_command("强化今日装备", priority=5, block=True)


@forge_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await forge_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    if _sleep_blocked(event.get_user_id()):
        await forge_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        ok, result = _forge(user, today)
        if ok:
            _save_data(data)
    await forge_cmd.finish(MessageSegment.reply(event.message_id) + result)


boss_forge_cmd = on_command("强化世界BOSS装备", priority=5, block=True)


@boss_forge_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await boss_forge_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    if _sleep_blocked(event.get_user_id()):
        await boss_forge_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        boss = _active_world_boss(group, today)
        if not boss:
            await boss_forge_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_none"))
        user_id = event.get_user_id()
        user = _ensure_player(group, user_id, _display_name(event))
        ok, result = _forge_world_boss(boss, user_id, user, today, rng=random)
        if ok:
            _save_data(data)
    await boss_forge_cmd.finish(MessageSegment.reply(event.message_id) + result)


rebuy_cmd = on_command("购买装备", priority=5, block=True)


@rebuy_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rebuy_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    if _sleep_blocked(event.get_user_id()):
        await rebuy_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        ok, result = _rebuy_equip(user, today)
        if ok:
            _save_data(data)
    await rebuy_cmd.finish(MessageSegment.reply(event.message_id) + result)


reset_rpg_cmd = on_command("重置RPG功能", priority=5, block=True)


@reset_rpg_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await reset_rpg_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        reset = _reset_group_rpg_equip(group, today, random)
        _save_data(data)

    if reset:
        msg = f"本群 RPG 已重置，已为今天签到过的 {reset} 人重新发放今日装备。运势、连签和其他状态不变。"
    else:
        msg = "本群今天还没有可重发装备的 RPG 签到记录。"
    await reset_rpg_cmd.finish(MessageSegment.reply(event.message_id) + msg)
