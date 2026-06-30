"""组队打怪：在单刷基础上补一层“拉人合作”的轻量玩法。"""

from __future__ import annotations

from collections import Counter
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _first_at_qq,
    _get_group,
    _get_intimacy,
    _load_data,
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
)
from ..gift import _bond_level
from .config import _copy, _cfg, _error, _line
from .hunt import _buff_active, _hunt_result_lines, _settle_coop, _settle_solo
from .player import _ensure_player, _resolve_group


def _team_success_rate(bond_level: int) -> float:
    """组队成功率：base + (羁绊等级-1)*step，并钳在 [min, max]。"""
    t = _cfg("team", {})
    rate = float(t.get("base_success", 0.35)) + (int(bond_level) - 1) * float(t.get("per_level", 0.12))
    return max(float(t.get("min_success", 0.10)), min(float(t.get("max_success", 0.95)), rate))


def _team_exp_bonus(bond_level: int) -> float:
    """组队经验加成：Lv1 无加成，后续随羁绊递增并封顶。"""
    t = _cfg("team", {})
    return max(
        0.0,
        min(float(t.get("exp_bonus_max", 0.50)), (int(bond_level) - 1) * float(t.get("exp_bonus_per_level", 0.05))),
    )


def _team_drop_bonus(bond_level: int) -> float:
    """组队掉落加成独立配置，强化合作收益但不直接推高积分。"""
    t = _cfg("team", {})
    return max(
        0.0,
        min(float(t.get("drop_bonus_max", 0.40)), (int(bond_level) - 1) * float(t.get("drop_bonus_per_level", 0.08))),
    )


def _roll_fail_flavor(rng=random) -> str:
    """组队失败时抽一条前置氛围事件。"""
    weights = _cfg("team", {}).get("fail_flavor", {})
    if not isinstance(weights, dict) or not weights:
        return ""
    cands = {key: int(weight) for key, weight in weights.items()}
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _member_line(rew: dict, name: str) -> str:
    """组队成功时单个成员的收益行。"""
    loot = ""
    if rew.get("drops"):
        loot = "，掉落 " + "、".join(f"{n} ×{c}" for n, c in Counter(rew["drops"]).items())
    levelup = f"，升级 Lv{rew['old_level']}→Lv{rew['new_level']}" if rew["new_level"] > rew["old_level"] else ""
    return _line("team_member", name=name, exp=rew["exp_gain"], points=rew["points_gain"], loot=loot, levelup=levelup)


def _build_coop_broadcast(out: dict, b_id: str, a_id: str, b_name: str, a_name: str):
    """组队成功：先报战况，再报协作事件/加成，最后给成员收益。"""
    name = out["monster"].get("name", "")
    if out.get("elite"):
        name = "精英·" + name
    head = random.choice(_copy("team_win" if out["win"] else "team_lose"))
    msg = _render_with_ats(head, {"a": b_id, "b": a_id, "monster": name})
    if out.get("team_event"):
        msg = msg + "\n" + _line(f"team_event_{out['team_event']}")
    if out.get("exp_bonus") or out.get("drop_bonus"):
        msg = msg + "\n" + _line(
            "team_bonus",
            exp_pct=int(round(float(out.get("exp_bonus", 0.0)) * 100)),
            drop_pct=int(round(float(out.get("drop_bonus", 0.0)) * 100)),
        )
    msg = msg + "\n" + _member_line(out["b"], b_name)
    msg = msg + "\n" + _member_line(out["a"], a_name)
    if _buff_active(out.get("buff")):
        msg = msg + "\n" + _line("daily_buff", buff=out["buff"].get("name", ""))
    return msg


def _build_fail_broadcast(out: dict, b_id: str, a_name: str, fail_event: str = ""):
    """组队没拉动：先补气氛，再退化为发起人的单刷结果。"""
    msg = ""
    if fail_event:
        msg = _line(f"team_fail_event_{fail_event}", b_name=a_name) + "\n"
    msg = msg + _render_with_ats(random.choice(_copy("team_fail")), {"a": b_id, "b_name": a_name})
    for ln in _hunt_result_lines(out):
        msg = msg + "\n" + ln
    return msg


team_cmd = on_command("组队", priority=5, block=True)


@team_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    initiator = event.get_user_id()
    is_superuser = initiator == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    target = _first_at_qq(getattr(event, "original_message", None))
    if not target or target == "all":
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_need_target"))
    if target == initiator:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_self"))
    if target == str(getattr(bot, "self_id", "")):
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_bot"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        b = _ensure_player(group, initiator, _display_name(event))

        if b.get("equip_date") != today:
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if b.get("equip_used") and not is_superuser:
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        a = _ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if a.get("equip_date") != today:
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))
        if a.get("equip_used"):
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_broken"))

        bond_level = _bond_level(_get_intimacy(group, initiator, target))["level"]
        success = random.random() < _team_success_rate(bond_level)

        if success:
            out = _settle_coop(
                b,
                a,
                today,
                exp_bonus=_team_exp_bonus(bond_level),
                drop_bonus=_team_drop_bonus(bond_level),
            )
            _save_data(data)
            b_name = b.get("display_name") or f"群友{initiator}"
            msg = _build_coop_broadcast(out, initiator, target, b_name, a_name)
        else:
            out = _settle_solo(b, today)
            _save_data(data)
            msg = _build_fail_broadcast(out, initiator, a_name, _roll_fail_flavor())

    await team_cmd.finish(MessageSegment.reply(event.message_id) + msg)
