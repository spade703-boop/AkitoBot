"""战斗核心逻辑：怪物抽取、遭遇判定、胜负结算、增益系统。

本模块包含单人/组队共用的战斗基础逻辑，不依赖事件系统和奖励系统。
"""

from __future__ import annotations

import random

from ...core.game_store import _today_str, _weighted_choice
from .config import _cfg
from .player import _level_of


def _monsters() -> list[dict]:
    monsters = _cfg("monsters", [])
    return monsters if isinstance(monsters, list) and monsters else []


def _monster_weights(pool: list[dict]) -> list[int]:
    return [max(0, int(m.get("weight", 0))) for m in pool]


def _encounter_weights(level: int, pool: list[dict]) -> list[int] | None:
    """按等级读取遭遇权重；名称映射和旧版位置数组均可用。"""
    brackets = _cfg("combat", {}).get("encounter_brackets", [])
    if not isinstance(brackets, list):
        return None
    for bracket in brackets:
        if not isinstance(bracket, dict):
            return None
        max_level = bracket.get("max_level")
        if max_level is not None:
            try:
                max_level = int(max_level)
            except (TypeError, ValueError):
                return None
            if level > max_level:
                continue
        weights = bracket.get("weights")
        if isinstance(weights, dict):
            try:
                return [max(0, int(weights.get(str(monster.get("name", "")), 0))) for monster in pool]
            except (TypeError, ValueError):
                return None
        if isinstance(weights, list) and len(weights) == len(pool):
            try:
                return [max(0, int(weight)) for weight in weights]
            except (TypeError, ValueError):
                return None
        return None
    return None


def _pick_monster(level: int, rng=random) -> dict:
    pool = _monsters()
    if not pool:
        return {"name": "野怪", "power_req": 10}
    weights = _encounter_weights(level, pool) or _monster_weights(pool)
    if not pool or sum(weights) <= 0:
        return pool[0] if pool else {"name": "野怪", "power_req": 10}
    return rng.choices(pool, weights=weights, k=1)[0]


def _elite_chance(level: int) -> float:
    """低级阶段减少精英惊吓；后期回到常规精英概率。"""
    chance = float(_cfg("combat", {}).get("elite", {}).get("chance", 0.0))
    if level <= 3:
        return 0.0
    if level <= 7:
        return min(chance, 0.08)
    return chance


def _pick_encounter(level: int, rng=random) -> tuple[dict, bool]:
    """抽遭遇：先按等级分段怪池抽怪，再按该等级的精英概率掷是否精英。"""
    monster = _pick_monster(level, rng)
    chance = _elite_chance(level)
    return monster, (rng.random() < chance)


def _encounter_level(user: dict) -> int:
    """遭遇池用装备等级分段；没装备等级时回落到当前角色等级。"""
    level = int(user.get("equip_level", 0))
    return max(1, level or _level_of(int(user.get("exp", 0))))


def _rookie_power_factor(level: int) -> float:
    """单刷新手保护：前几级略抬战力，避免一天一把时连续挫败。"""
    if level <= 1:
        return 1.08
    if level <= 4:
        return 1.04
    return 1.0


def _buff_for_date(day: str) -> dict:
    """以日期为种子选取全群一致的当日增益。

    返回含 name/exp_mult/drop_mult 的 spec；缺省/空表回落到「平日」（无效果、不外显）。
    """
    buffs = _cfg("daily_buffs", {})
    if not isinstance(buffs, dict) or not buffs:
        return {"key": "plain", "name": "平日", "exp_mult": 1.0, "drop_mult": 1.0}
    weights = {k: int(v.get("weight", 0)) for k, v in buffs.items()}
    key = _weighted_choice(weights, random.Random(day))
    spec = dict(buffs.get(key, {}))
    spec.setdefault("key", key)
    spec.setdefault("name", key)
    spec.setdefault("exp_mult", 1.0)
    spec.setdefault("drop_mult", 1.0)
    return spec


def _today_buff() -> dict:
    return _buff_for_date(_today_str())


def _buff_active(buff: dict | None) -> bool:
    """今日增益是否真正生效（非平日）——决定是否在播报里揭示。"""
    return bool(buff) and (float(buff.get("exp_mult", 1.0)) != 1.0 or float(buff.get("drop_mult", 1.0)) != 1.0)


def _eff_monster(monster: dict, is_elite: bool) -> dict:
    """精英则把怪 power_req 按 elite.power_mult 放大（更难打）；否则原样返回。"""
    if not is_elite:
        return monster
    pm = float(_cfg("combat", {}).get("elite", {}).get("power_mult", 1.0))
    return {**monster, "power_req": int(int(monster.get("power_req", 1)) * pm)}


def _reward_mults(buff: dict, is_elite: bool, win: bool) -> tuple[float, float]:
    """今日增益 ×（精英且胜则再加成）→ (exp_mult, drop_mult)，喂 `_apply_rewards`。"""
    ecfg = _cfg("combat", {}).get("elite", {})
    exp_mult = float(buff.get("exp_mult", 1.0))
    drop_mult = float(buff.get("drop_mult", 1.0))
    if is_elite and win:
        exp_mult *= float(ecfg.get("exp_mult", 1.0))
        drop_mult *= float(ecfg.get("drop_mult", 1.0))
    return exp_mult, drop_mult


def resolve_hunt(combat_power: int, monster: dict, *, power_factor: float,
                 fortune_factor: float = 1.0, event: str | None = None) -> dict:
    """纯胜负判定：有效战力 vs 怪 power_req。返回 {win, effective, event, monster}（经验/掉落由调用方处理）。"""
    ev = _cfg("combat", {}).get("events", {}).get(event or "", {})
    effective = combat_power * float(power_factor) * float(fortune_factor)
    if "power_mult" in ev:
        effective *= float(ev["power_mult"])
    return {
        "win": effective >= int(monster.get("power_req", 0)),
        "effective": int(effective),
        "event": event or "",
        "monster": monster,
    }
