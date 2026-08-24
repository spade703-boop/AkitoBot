"""事件系统：战斗事件、支援场景、小型遭遇的抽取与处理。

本模块包含单人/组队线的事件逻辑，不涉及战斗核心和奖励计算。
"""

from __future__ import annotations

import random

from ...core.game_store import _weighted_choice
from .config import _cfg
from .utils import _support_chance


def _roll_hunt_event(margin: float, rng=random) -> str:
    """按战力优势分档抽随机事件（碾压→看破 / 劣势→爆发 / 其余→打滑），可能返回 '' 表示无事件。"""
    ccfg = _cfg("combat", {})
    events = ccfg.get("events", {})
    crush = float(ccfg.get("crush_margin", 1.5))
    weak = float(ccfg.get("weak_margin", 0.8))
    if margin >= crush:
        key = "insight"
    elif margin < weak:
        key = "desperate"
    else:
        key = "slip"
    cands = {key: int(events.get(key, {}).get("weight", 0)), "": int(ccfg.get("no_event_weight", 60))}
    return _weighted_choice(cands, rng)


def _roll_coop_event(rng=random) -> str:
    """组队事件单独抽取；可返回空串表示本次只是普通配合。"""
    tcfg = _cfg("team", {})
    events = tcfg.get("events", {})
    if not isinstance(events, dict):
        return ""
    cands = {key: int(spec.get("weight", 0)) for key, spec in events.items() if isinstance(spec, dict)}
    cands[""] = int(tcfg.get("no_event_weight", 60))
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _coop_event_spec(event_key: str) -> dict:
    """读取组队事件配置，缺失时回退为空配置。"""
    events = _cfg("team", {}).get("events", {})
    if not isinstance(events, dict):
        return {}
    spec = events.get(event_key, {})
    return spec if isinstance(spec, dict) else {}


def _support_cfg() -> dict:
    cfg = _cfg("support", {})
    return cfg if isinstance(cfg, dict) else {}


def _support_spec(scene: str) -> dict:
    spec = _support_cfg().get(scene, {})
    return spec if isinstance(spec, dict) else {}


def _roll_support_variant(rng=random) -> str:
    """在支援场景确定后抽取展示版本，不参与支援概率判定。"""
    defaults = {"default": 1, "dogbin_fox": 1}
    raw = _support_cfg().get("variant_weights", defaults)
    if not isinstance(raw, dict):
        raw = defaults
    cands: dict[str, int] = {}
    for key, weight in raw.items():
        try:
            parsed = int(weight)
        except (TypeError, ValueError):
            continue
        if str(key).strip() and parsed > 0:
            cands[str(key)] = parsed
    if not cands:
        cands = defaults
    return _weighted_choice(cands, rng)


def _roll_solo_support_scene(win: bool, rng=random) -> str:
    """单刷特判：胜利仅彰人追击；失败时三种场景各占固定 3% 档位。"""
    chance = _support_chance()
    if chance <= 0:
        return ""
    roll = rng.random()
    if win:
        return "akito_success" if roll < chance else ""
    if roll < chance:
        return "akito_fail"
    if roll < chance * 2:
        return "toya_rescue"
    if roll < chance * 3:
        return "duo_combo"
    return ""


def _minor_cfg() -> dict:
    cfg = _cfg("minor_encounters", {})
    return cfg if isinstance(cfg, dict) else {}


def _minor_chance(*, team: bool = False) -> float:
    key = "team_chance" if team else "chance"
    return max(0.0, min(1.0, float(_minor_cfg().get(key, 0.0))))


def _minor_event_spec(event_key: str, *, team: bool = False) -> dict:
    key = "team_events" if team else "events"
    events = _minor_cfg().get(key, {})
    if not isinstance(events, dict):
        return {}
    spec = events.get(event_key, {})
    return spec if isinstance(spec, dict) else {}


def _minor_event_allowed(spec: dict, win: bool) -> bool:
    when = str(spec.get("when", "any"))
    if when == "win":
        return bool(win)
    if when == "lose":
        return not bool(win)
    return True


def _roll_minor_encounter(win: bool, *, team: bool = False, rng=random) -> str:
    chance = _minor_chance(team=team)
    if chance <= 0 or rng.random() >= chance:
        return ""
    key = "team_events" if team else "events"
    events = _minor_cfg().get(key, {})
    if not isinstance(events, dict):
        return ""
    cands = {
        event_key: int(spec.get("weight", 0))
        for event_key, spec in events.items()
        if isinstance(spec, dict) and _minor_event_allowed(spec, win)
    }
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _roll_minor_reward(spec: dict, rng=random) -> dict:
    rewards = spec.get("rewards", [])
    if not isinstance(rewards, list):
        return {}
    cands = {
        str(idx): int(reward.get("weight", 0))
        for idx, reward in enumerate(rewards)
        if isinstance(reward, dict)
    }
    if sum(cands.values()) <= 0:
        return {}
    picked = _weighted_choice(cands, rng)
    try:
        reward = rewards[int(picked)]
    except (TypeError, ValueError, IndexError):
        return {}
    return reward if isinstance(reward, dict) else {}
