from __future__ import annotations

import random

from ...core.game_store import _weighted_choice
from .config import _cfg
from .fortune import _fortune_by_key


def _team_success_rate(bond_level: int) -> float:
    """组队成功率：正羁绊提速，负羁绊缓降，并钳在 [min, max]。"""
    tcfg = _cfg("team", {})
    level = int(bond_level)
    base = float(tcfg.get("base_success", 0.35))
    if level >= 1:
        rate = base + (level - 1) * float(tcfg.get("per_level", 0.12))
    else:
        rate = base + (level - 1) * float(tcfg.get("negative_per_level", tcfg.get("per_level", 0.12)))
    return max(float(tcfg.get("min_success", 0.10)), min(float(tcfg.get("max_success", 0.95)), rate))


def _team_power_bonus() -> float:
    """组队成功时的基础协作战力加成。"""
    return max(0.0, float(_cfg("team", {}).get("power_bonus", 0.0)))


def _support_chance() -> float:
    """援护/追击特判的基础概率。"""
    cfg = _cfg("support", {})
    if not isinstance(cfg, dict):
        return 0.0
    return max(0.0, min(1.0, float(cfg.get("chance", 0.03))))


def _roll_fail_flavor(rng=random) -> str:
    """组队失败时抽一条前置氛围事件。"""
    weights = _cfg("team", {}).get("fail_flavor", {})
    if not isinstance(weights, dict) or not weights:
        return ""
    cands = {key: int(weight) for key, weight in weights.items()}
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _fortune_combat_factor(user: dict, today: str, *, enabled: bool = True) -> float:
    """当日隐藏运势给打怪/世界BOSS的战力系数（未签到则 1.0）。"""
    if not enabled or user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("combat_factor", 1.0))


def _fortune_drop_factor(user: dict, today: str) -> float:
    """当日隐藏运势给掉落的概率系数（未签到则 1.0）。"""
    if user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("drop_factor", 1.0))
