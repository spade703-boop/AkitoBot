"""冒险补给：每周限量的积分出口与普通挑战战备投放。"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _record_weekly_investment,
    _save_data,
    _today_str,
    _week_key,
    _weekly_investment,
    _weighted_choice,
    register_points_status_hook,
)
from .analytics import record_supply_open
from .config import _cfg, _error, _line
from .inventory import _add_item, _item_by_name
from .player import _ensure_player, _level_of, _resolve_group


def _supply_cfg() -> dict:
    config = _cfg("adventure_supply", {})
    return config if isinstance(config, dict) else {}


def _supply_costs() -> list[int]:
    return [int(cost) for cost in _supply_cfg().get("weekly_costs", [])]


def _pick_supply_item(rng=random) -> str:
    pool = _supply_cfg().get("pool", [])
    weights = {
        str(entry.get("item", "")): int(entry.get("weight", 0))
        for entry in pool
        if isinstance(entry, dict) and str(entry.get("item", ""))
    }
    return _weighted_choice(weights, rng)


def _supply_points_status(user: dict, today: str) -> str:
    costs = _supply_costs()
    if not costs:
        return ""
    weekly = user.get("weekly_investment")
    used = 0
    if isinstance(weekly, dict) and weekly.get("week") == _week_key(today):
        used = max(0, int(weekly.get("supply_count", 0)))
    total = len(costs)
    used = min(used, total)
    if used >= total:
        return f"· 冒险补给：今日不可开启（本周次数已用完 {used}/{total}）"
    cost = costs[used]
    points = int(user.get("points", 0))
    if points >= cost:
        return f"· 冒险补给：今日可开启 ✅（本周 {used}/{total}，下次消耗 {cost} 积分）"
    return f"· 冒险补给：今日不可开启（本周 {used}/{total}，下次需要 {cost} 积分，当前 {points}）"


register_points_status_hook(_supply_points_status)


supply_cmd = on_command("开启冒险补给", priority=5, block=True)


@supply_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await supply_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if args and args.extract_plain_text().strip():
        return

    user_id = event.get_user_id()
    if is_sleeping() and user_id != SUPERUSER_QQ:
        await supply_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    costs = _supply_costs()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, user_id, _display_name(event))
        weekly = _weekly_investment(user, today)
        used = int(weekly.get("supply_count", 0))
        if used >= len(costs):
            await supply_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("supply_limit", max=len(costs))
            )

        cost = costs[used]
        points = int(user.get("points", 0))
        if points < cost:
            await supply_cmd.finish(
                MessageSegment.reply(event.message_id)
                + _error("supply_poor", count=used + 1, cost=cost, total=points)
            )

        item_name = _pick_supply_item(random)
        item_effect = str((_item_by_name(item_name) or {}).get("desc", "详见冒险帮助"))
        exp_gain = max(0, int(_supply_cfg().get("exp", 0)))
        old_level = _level_of(int(user.get("exp", 0)))
        user["points"] = points - cost
        user["exp"] = int(user.get("exp", 0)) + exp_gain
        _add_item(user, item_name, 1)
        weekly = _record_weekly_investment(
            user,
            today,
            supply_count=1,
            supply_spent=cost,
        )
        record_supply_open(group, today, points_spent=cost, exp_gained=exp_gain)
        new_level = _level_of(int(user.get("exp", 0)))
        _save_data(data)

    levelup = f"，升级 Lv{old_level}→Lv{new_level}" if new_level > old_level else ""
    result = _line(
        "supply_open",
        cost=cost,
        count=int(weekly["supply_count"]),
        max=len(costs),
        name=item_name,
        effect=item_effect,
        exp=exp_gain,
        levelup=levelup,
    )
    await supply_cmd.finish(MessageSegment.reply(event.message_id) + result)
