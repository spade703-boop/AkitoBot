"""普通战斗的基础奖励计算与发放。"""

from __future__ import annotations

import random

from ... import utils
from ...config import _cfg
from ...inventory import inventory
from ...player.player import _combat_power, _consume_equip, _level_of
from ...types import ActiveBattleView, RpgUserRecord


def _challenge_exp(win: bool, level: int) -> int:
    c = _cfg("challenge", {})
    if win:
        return int(c.get("win_exp_base", 60)) + level * int(c.get("win_exp_per_level", 10))
    return int(c.get("lose_exp_base", 15)) + level * int(c.get("lose_exp_per_level", 2))


def _challenge_points(win: bool, user: RpgUserRecord) -> int:
    c = _cfg("challenge", {})
    points = int(c.get("win_points", 30)) if win else int(c.get("lose_points", 10))
    if user.get("equip_rebought"):
        points = int(points * float(_cfg("equip", {}).get("rebuy_points_mult", 0.5)))
    return points


def _rebuy_exp_mult() -> float:
    config = _cfg("equip", {})
    return float(config.get("rebuy_exp_mult", config.get("rebuy_points_mult", 0.5)))


def _rebuy_points_mult() -> float:
    return float(_cfg("equip", {}).get("rebuy_points_mult", 0.5))


def _solo_cfg() -> dict:
    config = _cfg("solo", {})
    return config if isinstance(config, dict) else {}


def _solo_power_bonus() -> float:
    return max(0.0, float(_solo_cfg().get("power_bonus", 0.0)))


def _solo_exp_bonus(win: bool) -> float:
    key = "win_exp_bonus" if win else "lose_exp_bonus"
    return max(0.0, float(_solo_cfg().get(key, 0.0)))


def _battle_power(user: RpgUserRecord, active_supply: ActiveBattleView | None) -> float:
    power = float(_combat_power(user))
    effect = active_supply.get("effect", {}) if active_supply else {}
    if effect.get("full_forge"):
        forge = int(user.get("equip_forge", 0))
        forge_cfg = _cfg("forge", {})
        missing = max(0, int(forge_cfg.get("max_per_day", 3)) - forge)
        power += missing * int(forge_cfg.get("step", 0))
    return power * float(effect.get("power_mult", 1.0))


def _apply_extra_rewards(user: RpgUserRecord, *, exp: int = 0, points: int = 0,
                         exp_mult: float = 1.0, points_mult: float = 1.0) -> tuple[int, int, int, int]:
    level_before = _level_of(int(user.get("exp", 0)))
    exp_gain = max(0, int(exp))
    points_gain = max(0, int(points))
    if user.get("equip_rebought"):
        exp_gain = int(exp_gain * _rebuy_exp_mult())
        points_gain = int(points_gain * _rebuy_points_mult())
    exp_gain = int(exp_gain * float(exp_mult))
    points_gain = int(points_gain * float(points_mult))
    if exp_gain:
        user["exp"] = int(user.get("exp", 0)) + exp_gain
    if points_gain:
        user["points"] = int(user.get("points", 0)) + points_gain
    level_after = _level_of(int(user.get("exp", 0)))
    return exp_gain, points_gain, level_before, level_after


def _apply_rewards(user: RpgUserRecord, today: str, *, win: bool, monster: dict,
                   event_key: str = "", exp_bonus: float = 0.0, exp_mult: float = 1.0,
                   points_mult: float = 1.0, drop_mult: float = 1.0,
                   battle_supply: ActiveBattleView | None = None,
                   rescue_exp_mult: float = 1.0, rng=random) -> dict:
    ccfg = _cfg("combat", {})
    battle_debuff = inventory._active_battle_debuff(user)
    debuff_effect = battle_debuff.get("effect", {}) if battle_debuff else {}
    debuff_exp_mult = float(debuff_effect.get("exp_mult", 1.0))
    debuff_points_mult = float(debuff_effect.get("points_mult", 1.0))
    debuff_drop_mult = float(debuff_effect.get("drop_mult", 1.0))
    old_exp = int(user.get("exp", 0))
    level = _level_of(old_exp)
    exp_gain = _challenge_exp(win, level)
    if win and event_key == "insight":
        exp_gain = int(exp_gain * float(ccfg.get("events", {}).get("insight", {}).get("exp_mult", 1.5)))
    if exp_bonus:
        exp_gain = int(exp_gain * (1.0 + float(exp_bonus)))
    monster_exp_mult = float(monster.get("reward_exp_mult", 1.0))
    if monster_exp_mult != 1.0:
        exp_gain = int(exp_gain * monster_exp_mult)
    if exp_mult != 1.0:
        exp_gain = int(exp_gain * float(exp_mult))
    supply_effect = battle_supply.get("effect", {}) if battle_supply else {}
    supply_exp_mult = float(supply_effect.get("exp_mult", 1.0))
    if supply_exp_mult != 1.0:
        exp_gain = int(exp_gain * supply_exp_mult)
    if rescue_exp_mult != 1.0:
        exp_gain = int(exp_gain * float(rescue_exp_mult))
    exp_buff_pending = int(user.get("exp_buff_uses", 0)) > 0
    exp_buff_deferred = bool(battle_supply and exp_buff_pending)
    buffed = False
    if exp_buff_pending and not battle_supply:
        exp_gain *= int(user.get("exp_buff_mult", 2))
        buffed = True
        user["exp_buff_uses"] = int(user["exp_buff_uses"]) - 1
    if user.get("equip_rebought"):
        exp_gain = int(exp_gain * _rebuy_exp_mult())
    exp_gain = int(exp_gain * debuff_exp_mult)
    user["exp"] = old_exp + exp_gain
    challenge = _cfg("challenge", {})
    base_drop = float(challenge.get("win_drop_mult", 1.0) if win else challenge.get("lose_drop_mult", 0.3))
    drops = inventory._roll_drops(monster, rng=rng, mult=(base_drop * utils._fortune_drop_factor(user, today)
        * float(drop_mult) * float(supply_effect.get("drop_mult", 1.0)) * debuff_drop_mult))
    for item_name in drops:
        inventory._add_item(user, item_name, 1)
    points_gain = int(_challenge_points(win, user) * debuff_points_mult * float(points_mult))
    user["points"] = int(user.get("points", 0)) + points_gain
    user["hunt_total"] = int(user.get("hunt_total", 0)) + 1
    if win:
        user["hunt_wins"] = int(user.get("hunt_wins", 0)) + 1
    supply_uses_left = inventory._consume_battle_supply(user) if battle_supply else 0
    debuff_uses_left = inventory._consume_battle_debuff(user) if battle_debuff else 0
    _consume_equip(user)
    return {
        "exp_gain": exp_gain, "exp_buffed": buffed, "monster_exp_mult": monster_exp_mult,
        "drops": drops, "points_gain": points_gain, "old_level": level,
        "new_level": _level_of(user["exp"]),
        "battle_supply_name": str(battle_supply.get("name", "")) if battle_supply else "",
        "battle_supply_parts": inventory._battle_supply_parts(battle_supply),
        "battle_supply_uses_left": supply_uses_left, "exp_buff_suppressed": exp_buff_deferred,
        "battle_debuff_name": str(battle_debuff.get("name", "")) if battle_debuff else "",
        "battle_debuff_exp_mult": debuff_exp_mult, "battle_debuff_points_mult": debuff_points_mult,
        "battle_debuff_drop_mult": debuff_drop_mult, "battle_debuff_uses_left": debuff_uses_left,
    }


__all__ = ["_challenge_exp", "_challenge_points", "_rebuy_exp_mult", "_rebuy_points_mult",
           "_solo_cfg", "_solo_power_bonus", "_solo_exp_bonus", "_battle_power",
           "_apply_extra_rewards", "_apply_rewards"]
