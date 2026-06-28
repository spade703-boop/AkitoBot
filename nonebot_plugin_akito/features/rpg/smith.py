"""强化：唯一的积分出口——花积分给今日装备加战力（当天有效、次日随装备重置）。

逐次涨价（第 n 次 = cost_base*n）、每日有上限。提高今天打怪的胜率。战力为隐藏值，反馈走文案。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment

from ...core import is_sleeping
from ...core.game_store import LOCK, _display_name, _get_group, _load_data, _save_data, _today_str
from .config import _cfg, _error, _line
from .player import _ensure_player, _resolve_group


def _forge(user: dict, today: str) -> tuple[bool, str]:
    """花积分强化今日装备一次。返回 (是否成功, 文案)；失败不扣分。"""
    if user.get("equip_date") != today:
        return False, _error("forge_no_equip")
    if user.get("equip_used"):
        return False, _error("forge_broken")
    fcfg = _cfg("forge", {})
    times = int(user.get("equip_forge", 0))
    mx = int(fcfg.get("max_per_day", 5))
    if times >= mx:
        return False, _error("forge_max", max=mx)
    cost = int(fcfg.get("cost_base", 100)) * (times + 1)
    points = int(user.get("points", 0))
    if points < cost:
        return False, _error("forge_poor", cost=cost, total=points)
    user["points"] = points - cost
    user["equip_forge"] = times + 1
    return True, _line("forge_ok", forge=times + 1, cost=cost)


forge_cmd = on_command("强化", aliases={"锻造", "强化装备"}, priority=5, block=True)


@forge_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await forge_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if is_sleeping():
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
