"""强化：唯一的积分出口——花积分给今日装备加战力（当天有效、次日随装备重置）。

优先走 `forge.costs` 的分段费用；未配置时回退到旧的 cost_base*n 线性涨价。提高今天打怪的胜率。
战力为隐藏值，反馈走文案。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import is_sleeping
from ...core.game_store import LOCK, _display_name, _get_group, _load_data, _save_data, _today_str
from .config import _cfg, _error, _line
from .player import _ensure_player, _resolve_group


def _forge_cost(fcfg: dict, times: int) -> int:
    """优先按 costs 取分段费用；缺省时兼容旧配置的线性涨价。"""
    costs = fcfg.get("costs", [])
    if isinstance(costs, list) and 0 <= times < len(costs):
        try:
            return int(costs[times])
        except (TypeError, ValueError):
            pass
    return int(fcfg.get("cost_base", 100)) * (times + 1)


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
    cost = _forge_cost(fcfg, times)
    points = int(user.get("points", 0))
    if points < cost:
        return False, _error("forge_poor", cost=cost, total=points)
    user["points"] = points - cost
    user["equip_forge"] = times + 1
    return True, _line("forge_ok", forge=times + 1, cost=cost)


forge_cmd = on_command("强化今日装备", force_whitespace=True, priority=5, block=True)


@forge_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await forge_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        await forge_cmd.finish(
            MessageSegment.reply(event.message_id) + "格式是「强化今日装备」，不用带其他字。"
        )

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
