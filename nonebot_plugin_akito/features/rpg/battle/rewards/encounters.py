"""援护与小奇遇奖励入账。"""

from __future__ import annotations

import random

from ...inventory import inventory
from ...player.player import _level_of
from ...types import RpgUserRecord
from .. import events
from .calculations import _apply_extra_rewards, _challenge_exp, _challenge_points, _rebuy_exp_mult


def _support_bonus_exp(scene: str, user: RpgUserRecord, level: int) -> int:
    ratio = float(events._support_spec(scene).get("exp_ratio", 0.0))
    if ratio <= 0:
        return 0
    exp = int(_challenge_exp(True, level) * ratio)
    if user.get("equip_rebought"):
        exp = int(exp * _rebuy_exp_mult())
    return max(0, exp)


def _support_bonus_points(scene: str, user: RpgUserRecord) -> int:
    ratio = float(events._support_spec(scene).get("points_ratio", 0.0))
    if ratio <= 0:
        return 0
    return max(0, int(_challenge_points(True, user) * ratio))


def _apply_support_bonus(user: RpgUserRecord, out: dict) -> None:
    scene = str(out.get("support_scene", ""))
    if scene not in {"akito_success", "akito_fail", "duo_combo"}:
        out["support_exp"] = 0
        out["support_points"] = 0
        return
    bonus_exp = _support_bonus_exp(scene, user, int(out.get("old_level", 1)))
    bonus_points = _support_bonus_points(scene, user)
    bonus_exp = int(bonus_exp * float(out.get("battle_debuff_exp_mult", 1.0)))
    bonus_points = int(bonus_points * float(out.get("battle_debuff_points_mult", 1.0)))
    if bonus_exp:
        user["exp"] = int(user.get("exp", 0)) + bonus_exp
    if bonus_points:
        user["points"] = int(user.get("points", 0)) + bonus_points
    out["support_exp"] = bonus_exp
    out["support_points"] = bonus_points
    out["new_level"] = _level_of(int(user.get("exp", 0)))


def _apply_minor_encounter(user: RpgUserRecord, out: dict, *, rng=random) -> None:
    out["minor_event"] = ""
    out["minor_reward_parts"] = []
    out["minor_old_level"] = int(out.get("new_level", out.get("old_level", 1)))
    out["minor_new_level"] = int(out.get("new_level", out.get("old_level", 1)))
    if not out.get("direct_solo"):
        return
    event_key = events._roll_minor_encounter(bool(out.get("win")), rng=rng)
    if not event_key:
        return
    spec = events._minor_event_spec(event_key)
    parts: list[str] = []
    exp_gain, points_gain, level_before, level_after = _apply_extra_rewards(
        user,
        exp=int(spec.get("exp", 0)),
        points=int(spec.get("points", 0)),
        exp_mult=float(out.get("battle_debuff_exp_mult", 1.0)),
        points_mult=float(out.get("battle_debuff_points_mult", 1.0)),
    )
    if exp_gain:
        parts.append(f"经验 +{exp_gain}")
    if points_gain:
        parts.append(f"积分 +{points_gain}")
    reward = events._roll_minor_reward(spec, rng=rng) if spec.get("rewards") else {}
    if reward:
        amount = max(0, int(reward.get("amount", 1)))
        reward_type = str(reward.get("type", ""))
        if reward_type == "item":
            name = str(reward.get("name", ""))
            if name and amount > 0:
                inventory._add_item(user, name, amount)
                parts.append(f"{name} ×{amount}")
        elif reward_type == "exp":
            label = str(reward.get("label", "额外经验"))
            extra_exp, _points, old_level, level_after = _apply_extra_rewards(
                user, exp=amount, exp_mult=float(out.get("battle_debuff_exp_mult", 1.0))
            )
            level_before = min(level_before, old_level)
            parts.append(f"{label}（经验 +{extra_exp}）")
        elif reward_type == "points":
            label = str(reward.get("label", "额外积分"))
            _exp, extra_points, old_level, level_after = _apply_extra_rewards(
                user, points=amount, points_mult=float(out.get("battle_debuff_points_mult", 1.0))
            )
            level_before = min(level_before, old_level)
            parts.append(f"{label}（积分 +{extra_points}）")
    out["minor_event"] = event_key
    out["minor_reward_parts"] = parts
    out["minor_old_level"] = level_before
    out["minor_new_level"] = level_after
    out["new_level"] = level_after


def _apply_team_minor_encounter(b: RpgUserRecord, a: RpgUserRecord, out: dict, *, rng=random) -> None:
    out["team_minor_event"] = ""
    out["team_minor_parts"] = []
    out["team_minor_b_parts"] = []
    out["team_minor_a_parts"] = []
    out["team_minor_b"] = {}
    out["team_minor_a"] = {}
    event_key = events._roll_minor_encounter(bool(out.get("win")), team=True, rng=rng)
    if not event_key:
        return
    spec = events._minor_event_spec(event_key, team=True)
    b_old = _level_of(int(b.get("exp", 0)))
    a_old = _level_of(int(a.get("exp", 0)))
    b_parts: list[str] = []
    a_parts: list[str] = []
    b_total_exp = b_total_points = a_total_exp = a_total_points = 0
    base_exp = int(spec.get("exp", 0)) // 2
    base_points = int(spec.get("points", 0)) // 2
    b_exp, b_points, _b_before, _b_after = _apply_extra_rewards(
        b, exp=base_exp, points=base_points,
        exp_mult=float((out.get("b") or {}).get("battle_debuff_exp_mult", 1.0)),
        points_mult=float((out.get("b") or {}).get("battle_debuff_points_mult", 1.0)),
    )
    a_exp, a_points, _a_before, _a_after = _apply_extra_rewards(
        a, exp=base_exp, points=base_points,
        exp_mult=float((out.get("a") or {}).get("battle_debuff_exp_mult", 1.0)),
        points_mult=float((out.get("a") or {}).get("battle_debuff_points_mult", 1.0)),
    )
    if b_exp:
        b_total_exp += b_exp; b_parts.append(f"经验 +{b_exp}")
    if a_exp:
        a_total_exp += a_exp; a_parts.append(f"经验 +{a_exp}")
    if b_points:
        b_total_points += b_points; b_parts.append(f"积分 +{b_points}")
    if a_points:
        a_total_points += a_points; a_parts.append(f"积分 +{a_points}")
    reward = events._roll_minor_reward(spec, rng=rng) if spec.get("rewards") else {}
    if reward:
        amount = max(0, int(reward.get("amount", 1)))
        reward_type = str(reward.get("type", ""))
        if reward_type == "item":
            name = str(reward.get("name", ""))
            if name and amount > 0:
                inventory._add_item(b, name, amount); inventory._add_item(a, name, amount)
                part = f"{name} ×{amount}"; b_parts.append(part); a_parts.append(part)
        elif reward_type == "exp":
            label = str(reward.get("label", "额外经验")); split_amount = amount // 2
            b_extra, _b_points, _b_before, _b_after = _apply_extra_rewards(
                b, exp=split_amount, exp_mult=float((out.get("b") or {}).get("battle_debuff_exp_mult", 1.0))
            )
            a_extra, _a_points, _a_before, _a_after = _apply_extra_rewards(
                a, exp=split_amount, exp_mult=float((out.get("a") or {}).get("battle_debuff_exp_mult", 1.0))
            )
            if b_extra:
                b_total_exp += b_extra; b_parts.append(f"{label}（经验 +{b_extra}）")
            if a_extra:
                a_total_exp += a_extra; a_parts.append(f"{label}（经验 +{a_extra}）")
        elif reward_type == "points":
            label = str(reward.get("label", "额外积分")); split_amount = amount // 2
            _b_exp, b_extra, _b_before, _b_after = _apply_extra_rewards(
                b, points=split_amount, points_mult=float((out.get("b") or {}).get("battle_debuff_points_mult", 1.0))
            )
            _a_exp, a_extra, _a_before, _a_after = _apply_extra_rewards(
                a, points=split_amount, points_mult=float((out.get("a") or {}).get("battle_debuff_points_mult", 1.0))
            )
            if b_extra:
                b_total_points += b_extra; b_parts.append(f"{label}（积分 +{b_extra}）")
            if a_extra:
                a_total_points += a_extra; a_parts.append(f"{label}（积分 +{a_extra}）")
    out["team_minor_event"] = event_key
    out["team_minor_parts"] = list(b_parts) if b_parts == a_parts else []
    out["team_minor_b_parts"] = b_parts; out["team_minor_a_parts"] = a_parts
    out["team_minor_b"] = {"exp_gain": b_total_exp, "points_gain": b_total_points,
                            "old_level": b_old, "new_level": _level_of(int(b.get("exp", 0)))}
    out["team_minor_a"] = {"exp_gain": a_total_exp, "points_gain": a_total_points,
                            "old_level": a_old, "new_level": _level_of(int(a.get("exp", 0)))}


__all__ = ["_support_bonus_exp", "_support_bonus_points", "_apply_support_bonus",
           "_apply_minor_encounter", "_apply_team_minor_encounter"]
