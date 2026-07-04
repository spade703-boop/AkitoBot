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
    _add_intimacy,
    _display_name,
    _first_at_qq,
    _get_group,
    _get_intimacy,
    _load_data,
    _pair_key,
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
)
from ..gift import _bond_level
from .boss import _cleanup_stale_world_boss, _maybe_spawn_world_boss_lines
from .config import _copy, _cfg, _error, _line
from .hunt import _apply_team_minor_encounter, _buff_active, _hunt_result_lines, _settle_coop, _settle_solo, _team_minor_lines
from .player import _ensure_player, _resolve_group


def _team_success_rate(bond_level: int) -> float:
    """组队成功率：正羁绊提速，负羁绊缓降，并钳在 [min, max]。"""
    t = _cfg("team", {})
    level = int(bond_level)
    base = float(t.get("base_success", 0.35))
    if level >= 1:
        rate = base + (level - 1) * float(t.get("per_level", 0.12))
    else:
        rate = base + (level - 1) * float(t.get("negative_per_level", t.get("per_level", 0.12)))
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


def _team_power_bonus() -> float:
    """组队成功时的基础战力协作加成。"""
    return max(0.0, float(_cfg("team", {}).get("power_bonus", 0.0)))


def _support_chance() -> float:
    cfg = _cfg("support", {})
    if not isinstance(cfg, dict):
        return 0.0
    return max(0.0, min(1.0, float(cfg.get("chance", 0.03))))


def _roll_team_fail_rescue(rng=random) -> bool:
    """组队失败后的额外援护判定。"""
    chance = _support_chance()
    return chance > 0 and rng.random() < chance


def _negative_team_cfg() -> dict:
    """负羁绊磨合事件配置。"""
    cfg = _cfg("team", {}).get("negative", {})
    return cfg if isinstance(cfg, dict) else {}


def _negative_team_event_spec(event_key: str) -> dict:
    """读取负羁绊事件配置。"""
    events = _negative_team_cfg().get("events", {})
    if not isinstance(events, dict):
        return {}
    spec = events.get(event_key, {})
    return spec if isinstance(spec, dict) else {}


def _negative_team_event_chance(intimacy: int) -> float:
    """负羁绊越深，越容易触发额外磨合事件。"""
    value = int(intimacy)
    if value >= 0:
        return 0.0
    cfg = _negative_team_cfg()
    mild = int(cfg.get("mild_threshold", -50))
    deep = int(cfg.get("deep_threshold", -300))
    if value <= deep:
        return float(cfg.get("chance_deep", 0.75))
    if value <= mild:
        return float(cfg.get("chance_medium", 0.55))
    return float(cfg.get("chance_mild", 0.35))


def _roll_negative_team_event(intimacy: int, rng=random) -> str:
    """负羁绊组队时，额外抽一次磨合/摩擦事件。"""
    chance = _negative_team_event_chance(intimacy)
    if chance <= 0 or rng.random() >= chance:
        return ""
    events = _negative_team_cfg().get("events", {})
    if not isinstance(events, dict):
        return ""
    cands = {key: int(spec.get("weight", 0)) for key, spec in events.items() if isinstance(spec, dict)}
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _team_bond_daily_pairs(group: dict, today: str) -> dict:
    """按天记录每对群友的组队羁绊增长次数，避免刷取。"""
    rpg = group.setdefault("rpg", {})
    daily = rpg.get("team_bond_daily")
    if not isinstance(daily, dict) or daily.get("date") != today:
        daily = {"date": today, "pairs": {}}
        rpg["team_bond_daily"] = daily
    pairs = daily.get("pairs")
    if not isinstance(pairs, dict):
        pairs = {}
        daily["pairs"] = pairs
    return pairs


def _grant_team_bond(group: dict, uid1: str, uid2: str, today: str, *, win: bool, extra: int = 0) -> int:
    """成功组队后，按天给该 pair 小幅增长羁绊。"""
    t = _cfg("team", {})
    limit = max(0, int(t.get("bond_gain_daily_limit", 1)))
    if limit <= 0:
        return 0
    pairs = _team_bond_daily_pairs(group, today)
    key = _pair_key(uid1, uid2)
    if int(pairs.get(key, 0)) >= limit:
        return 0
    gain = max(0, int(t.get("bond_gain_base", 0)))
    if win:
        gain += max(0, int(t.get("bond_gain_win_bonus", 0)))
    gain += max(0, int(extra))
    if gain <= 0:
        return 0
    _add_intimacy(group, uid1, uid2, gain)
    pairs[key] = int(pairs.get(key, 0)) + 1
    return gain


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
    if out.get("negative_event"):
        msg = msg + "\n" + _line(f"team_negative_event_{out['negative_event']}")
    bonus_parts: list[str] = []
    if float(out.get("power_bonus", 0.0)) > 0:
        bonus_parts.append(f"战力 +{int(round(float(out.get('power_bonus', 0.0)) * 100))}%")
    if float(out.get("exp_bonus", 0.0)) > 0:
        bonus_parts.append(f"经验 +{int(round(float(out.get('exp_bonus', 0.0)) * 100))}%")
    if float(out.get("drop_bonus", 0.0)) > 0:
        bonus_parts.append(f"掉落 +{int(round(float(out.get('drop_bonus', 0.0)) * 100))}%")
    if bonus_parts:
        msg = msg + "\n" + _line("team_bonus", parts=" / ".join(bonus_parts))
    if int(out.get("bond_gain", 0)) > 0:
        msg = msg + "\n" + _line("team_bond_gain", amount=int(out["bond_gain"]))
    msg = msg + "\n" + _member_line(out["b"], b_name)
    msg = msg + "\n" + _member_line(out["a"], a_name)
    if _buff_active(out.get("buff")):
        msg = msg + "\n" + _line("daily_buff", buff=out["buff"].get("name", ""))
    for line in _team_minor_lines(out, b_name, a_name):
        msg = msg + "\n" + line
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


def _join_broadcast_lines(lines: list):
    msg = None
    for line in lines:
        if not line:
            continue
        msg = line if msg is None else msg + "\n" + line
    return msg if msg is not None else ""


def _build_fail_rescue_broadcast(
    out: dict,
    initiator_id: str,
    target_id: str,
    initiator_name: str,
    target_name: str,
    fail_event: str,
):
    """组队失败后被援护拉回：先播特判，再进入正常双人结算。"""
    key = fail_event or "out_of_step"
    fail_line = _line(f"team_fail_event_{key}", b_name=target_name)
    turn_line = _render_with_ats(random.choice(_copy("team_fail_turn")), {"a": initiator_id, "b_name": target_name})
    rescue_line = _line(f"team_support_{key}", a_name=initiator_name, b_name=target_name)
    coop = _build_coop_broadcast(out, initiator_id, target_id, initiator_name, target_name)
    return _join_broadcast_lines([fail_line, turn_line, rescue_line, coop])


def _settle_team_result(group: dict, initiator: str, target: str, b: dict, a: dict, today: str, raw_intimacy: int, bond_level: int) -> dict:
    """统一的组队结算入口：普通成功与援护拉回后的组队都共用。"""
    negative_event = _roll_negative_team_event(raw_intimacy, random)
    negative_spec = _negative_team_event_spec(negative_event)
    out = _settle_coop(
        b,
        a,
        today,
        exp_bonus=_team_exp_bonus(bond_level),
        drop_bonus=_team_drop_bonus(bond_level),
        extra_power_mult=float(negative_spec.get("power_mult", 1.0)),
        extra_exp_mult=float(negative_spec.get("exp_mult", 1.0)),
        extra_drop_mult=float(negative_spec.get("drop_mult", 1.0)),
    )
    out["negative_event"] = negative_event
    out["bond_gain"] = _grant_team_bond(
        group,
        initiator,
        target,
        today,
        win=bool(out.get("win")),
        extra=int(negative_spec.get("bond_bonus", 0)),
    )
    _apply_team_minor_encounter(b, a, out)
    return out


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
        return
    if target == initiator:
        return
    if target == str(getattr(bot, "self_id", "")):
        return

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, stale_changed = _cleanup_stale_world_boss(group, today)
        b = _ensure_player(group, initiator, _display_name(event))

        if b.get("equip_date") != today:
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("need_equip")] if settlement_lines else [_error("need_equip")]
            await team_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
        if b.get("equip_used") and not is_superuser:
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("equip_broken")] if settlement_lines else [_error("equip_broken")]
            await team_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

        a = _ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if a.get("equip_date") != today:
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("team_target_no_signin")] if settlement_lines else [_error("team_target_no_signin")]
            await team_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
        if a.get("equip_used"):
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("team_target_broken")] if settlement_lines else [_error("team_target_broken")]
            await team_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

        raw_intimacy = _get_intimacy(group, initiator, target)
        bond_level = _bond_level(raw_intimacy)["level"]
        success = random.random() < _team_success_rate(bond_level)
        b_name = b.get("display_name") or f"群友{initiator}"
        fail_flavor = ""

        if success:
            out = _settle_team_result(group, initiator, target, b, a, today, raw_intimacy, bond_level)
            boss_lines = _maybe_spawn_world_boss_lines(group, today, initiator, rng=random)
            _save_data(data)
            msg = _build_coop_broadcast(out, initiator, target, b_name, a_name)
        else:
            fail_flavor = _roll_fail_flavor()
            if _roll_team_fail_rescue(random):
                out = _settle_team_result(group, initiator, target, b, a, today, raw_intimacy, bond_level)
                boss_lines = _maybe_spawn_world_boss_lines(group, today, initiator, rng=random)
                _save_data(data)
                msg = _build_fail_rescue_broadcast(out, initiator, target, b_name, a_name, fail_flavor)
            else:
                out = _settle_solo(b, today)
                boss_lines = _maybe_spawn_world_boss_lines(group, today, initiator, rng=random)
                _save_data(data)
                msg = _build_fail_broadcast(out, initiator, a_name, fail_flavor)
        if settlement_lines:
            msg = "\n".join(settlement_lines) + "\n" + msg
        if boss_lines:
            msg = msg + "\n" + "\n".join(boss_lines)

    await team_cmd.finish(MessageSegment.reply(event.message_id) + msg)
