"""奖励系统：经验计算、掉落处理、积分发放、装备消耗。

本模块包含单人/组队线的奖励结算逻辑，依赖战斗核心和事件系统。
"""

from __future__ import annotations

import random

from . import combat, events, inventory, utils
from .config import _cfg
from .player import _combat_power, _consume_equip, _level_of
from .types import ActiveBattleView, RpgUserRecord


def _challenge_exp(win: bool, level: int) -> int:
    """打怪经验：按等级（胜/负不同）。"""
    c = _cfg("challenge", {})
    if win:
        return int(c.get("win_exp_base", 60)) + level * int(c.get("win_exp_per_level", 10))
    return int(c.get("lose_exp_base", 15)) + level * int(c.get("lose_exp_per_level", 2))


def _challenge_points(win: bool, user: RpgUserRecord) -> int:
    """打怪积分（少量）：把「打怪赚分 → 送礼攒羁绊 → 组队」串成闭环。替换装（equip_rebought）积分打对折。"""
    c = _cfg("challenge", {})
    pts = int(c.get("win_points", 30)) if win else int(c.get("lose_points", 10))
    if user.get("equip_rebought"):
        pts = int(pts * float(_cfg("equip", {}).get("rebuy_points_mult", 0.5)))
    return pts


def _rebuy_exp_mult() -> float:
    ecfg = _cfg("equip", {})
    return float(ecfg.get("rebuy_exp_mult", ecfg.get("rebuy_points_mult", 0.5)))


def _rebuy_points_mult() -> float:
    return float(_cfg("equip", {}).get("rebuy_points_mult", 0.5))


def _solo_cfg() -> dict:
    cfg = _cfg("solo", {})
    return cfg if isinstance(cfg, dict) else {}


def _solo_power_bonus() -> float:
    return max(0.0, float(_solo_cfg().get("power_bonus", 0.0)))


def _solo_exp_bonus(win: bool) -> float:
    key = "win_exp_bonus" if win else "lose_exp_bonus"
    return max(0.0, float(_solo_cfg().get(key, 0.0)))


def _battle_power(user: RpgUserRecord, active_supply: ActiveBattleView | None) -> float:
    """应用玩家自己的战备战力；组队时不会把效果扩散给队友。"""
    power = float(_combat_power(user))
    effect = active_supply.get("effect", {}) if active_supply else {}
    if effect.get("full_forge"):
        forge = int(user.get("equip_forge", 0))
        forge_cfg = _cfg("forge", {})
        missing = max(0, int(forge_cfg.get("max_per_day", 3)) - forge)
        power += missing * int(forge_cfg.get("step", 0))
    return power * float(effect.get("power_mult", 1.0))


def _apply_extra_rewards(
    user: RpgUserRecord,
    *,
    exp: int = 0,
    points: int = 0,
    exp_mult: float = 1.0,
    points_mult: float = 1.0,
) -> tuple[int, int, int, int]:
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


def _apply_rewards(user: RpgUserRecord, today: str, *, win: bool, monster: dict, event_key: str = "",
                   exp_bonus: float = 0.0, exp_mult: float = 1.0, drop_mult: float = 1.0,
                   battle_supply: ActiveBattleView | None = None, rescue_exp_mult: float = 1.0,
                   rng=random) -> dict:
    """给单个玩家结算（经验[含看破/单刷或组队额外加成/双倍卡/精英/今日增益] + 掉落 + 积分）并消耗其今日装备，记一次战绩。

    `exp_mult`/`drop_mult` 由调用方算好（精英 × 今日增益）传入；怪物自身可用 `reward_exp_mult` 微调经验。
    返回奖励明细 {exp_gain, exp_buffed, drops, points_gain, old_level, new_level}（不含播报）。
    单刷与组队（双方各调一次）共用本函数：胜负由调用方判定后传入。
    """
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
        exp_gain = int(exp_gain * (1.0 + float(exp_bonus)))  # 额外经验加成（单刷补偿 / 组队加成）
    monster_exp_mult = float(monster.get("reward_exp_mult", 1.0))
    if monster_exp_mult != 1.0:
        exp_gain = int(exp_gain * monster_exp_mult)
    if exp_mult != 1.0:
        exp_gain = int(exp_gain * float(exp_mult))           # 精英 × 今日增益
    supply_effect = battle_supply.get("effect", {}) if battle_supply else {}
    supply_exp_mult = float(supply_effect.get("exp_mult", 1.0))
    if supply_exp_mult != 1.0:
        exp_gain = int(exp_gain * supply_exp_mult)
    if rescue_exp_mult != 1.0:
        exp_gain = int(exp_gain * float(rescue_exp_mult))
    buffed = False
    exp_buff_pending = int(user.get("exp_buff_uses", 0)) > 0
    exp_buff_deferred = bool(battle_supply and exp_buff_pending)
    if exp_buff_pending and not battle_supply:  # 常规战备优先，护符本身不压制双倍经验卡
        exp_gain *= int(user.get("exp_buff_mult", 2))
        buffed = True
        user["exp_buff_uses"] = int(user["exp_buff_uses"]) - 1
    if user.get("equip_rebought"):
        exp_gain = int(exp_gain * _rebuy_exp_mult())
    exp_gain = int(exp_gain * debuff_exp_mult)
    user["exp"] = old_exp + exp_gain

    cc = _cfg("challenge", {})
    base_drop = float(cc.get("win_drop_mult", 1.0) if win else cc.get("lose_drop_mult", 0.3))
    drops = inventory._roll_drops(
        monster,
        rng=rng,
        mult=(
            base_drop
            * utils._fortune_drop_factor(user, today)
            * float(drop_mult)
            * float(supply_effect.get("drop_mult", 1.0))
            * debuff_drop_mult
        ),
    )
    for d in drops:
        inventory._add_item(user, d, 1)

    points_gain = _challenge_points(win, user)
    points_gain = int(points_gain * debuff_points_mult)
    user["points"] = int(user.get("points", 0)) + points_gain
    user["hunt_total"] = int(user.get("hunt_total", 0)) + 1   # 战绩：累计打怪
    if win:
        user["hunt_wins"] = int(user.get("hunt_wins", 0)) + 1  # 战绩：累计胜场
    supply_uses_left = inventory._consume_battle_supply(user) if battle_supply else 0
    debuff_uses_left = inventory._consume_battle_debuff(user) if battle_debuff else 0
    _consume_equip(user)  # 今日装备损坏
    return {
        "exp_gain": exp_gain, "exp_buffed": buffed, "monster_exp_mult": monster_exp_mult, "drops": drops,
        "points_gain": points_gain, "old_level": level, "new_level": _level_of(user["exp"]),
        "battle_supply_name": str(battle_supply.get("name", "")) if battle_supply else "",
        "battle_supply_parts": inventory._battle_supply_parts(battle_supply),
        "battle_supply_uses_left": supply_uses_left,
        "exp_buff_suppressed": exp_buff_deferred,
        "battle_debuff_name": str(battle_debuff.get("name", "")) if battle_debuff else "",
        "battle_debuff_exp_mult": debuff_exp_mult,
        "battle_debuff_points_mult": debuff_points_mult,
        "battle_debuff_drop_mult": debuff_drop_mult,
        "battle_debuff_uses_left": debuff_uses_left,
    }


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
        rtype = str(reward.get("type", ""))
        if rtype == "item":
            name = str(reward.get("name", ""))
            if name and amount > 0:
                inventory._add_item(user, name, amount)
                parts.append(f"{name} ×{amount}")
        elif rtype == "exp":
            label = str(reward.get("label", "额外经验"))
            extra_exp, _pts, _old, level_after = _apply_extra_rewards(
                user,
                exp=amount,
                exp_mult=float(out.get("battle_debuff_exp_mult", 1.0)),
            )
            level_before = min(level_before, _old)
            parts.append(f"{label}（经验 +{extra_exp}）")
        elif rtype == "points":
            label = str(reward.get("label", "额外积分"))
            _exp, extra_points, _old, level_after = _apply_extra_rewards(
                user,
                points=amount,
                points_mult=float(out.get("battle_debuff_points_mult", 1.0)),
            )
            level_before = min(level_before, _old)
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
    b_total_exp = 0
    b_total_points = 0
    a_total_exp = 0
    a_total_points = 0

    base_exp = int(spec.get("exp", 0)) // 2
    base_points = int(spec.get("points", 0)) // 2
    b_exp, b_points, _b_before, _b_after = _apply_extra_rewards(
        b,
        exp=base_exp,
        points=base_points,
        exp_mult=float((out.get("b") or {}).get("battle_debuff_exp_mult", 1.0)),
        points_mult=float((out.get("b") or {}).get("battle_debuff_points_mult", 1.0)),
    )
    a_exp, a_points, _a_before, _a_after = _apply_extra_rewards(
        a,
        exp=base_exp,
        points=base_points,
        exp_mult=float((out.get("a") or {}).get("battle_debuff_exp_mult", 1.0)),
        points_mult=float((out.get("a") or {}).get("battle_debuff_points_mult", 1.0)),
    )
    if b_exp:
        b_total_exp += b_exp
        b_parts.append(f"经验 +{b_exp}")
    if a_exp:
        a_total_exp += a_exp
        a_parts.append(f"经验 +{a_exp}")
    if b_points:
        b_total_points += b_points
        b_parts.append(f"积分 +{b_points}")
    if a_points:
        a_total_points += a_points
        a_parts.append(f"积分 +{a_points}")
    reward = events._roll_minor_reward(spec, rng=rng) if spec.get("rewards") else {}
    if reward:
        amount = max(0, int(reward.get("amount", 1)))
        rtype = str(reward.get("type", ""))
        if rtype == "item":
            name = str(reward.get("name", ""))
            if name and amount > 0:
                inventory._add_item(b, name, amount)
                inventory._add_item(a, name, amount)
                part = f"{name} ×{amount}"
                b_parts.append(part)
                a_parts.append(part)
        elif rtype == "exp":
            label = str(reward.get("label", "额外经验"))
            split_amount = amount // 2
            b_extra, _b_pts, _b_before, _b_after = _apply_extra_rewards(
                b,
                exp=split_amount,
                exp_mult=float((out.get("b") or {}).get("battle_debuff_exp_mult", 1.0)),
            )
            a_extra, _a_pts, _a_before, _a_after = _apply_extra_rewards(
                a,
                exp=split_amount,
                exp_mult=float((out.get("a") or {}).get("battle_debuff_exp_mult", 1.0)),
            )
            if b_extra:
                b_total_exp += b_extra
                b_parts.append(f"{label}（经验 +{b_extra}）")
            if a_extra:
                a_total_exp += a_extra
                a_parts.append(f"{label}（经验 +{a_extra}）")
        elif rtype == "points":
            label = str(reward.get("label", "额外积分"))
            split_amount = amount // 2
            _b_exp, b_extra, _b_before, _b_after = _apply_extra_rewards(
                b,
                points=split_amount,
                points_mult=float((out.get("b") or {}).get("battle_debuff_points_mult", 1.0)),
            )
            _a_exp, a_extra, _a_before, _a_after = _apply_extra_rewards(
                a,
                points=split_amount,
                points_mult=float((out.get("a") or {}).get("battle_debuff_points_mult", 1.0)),
            )
            if b_extra:
                b_total_points += b_extra
                b_parts.append(f"{label}（积分 +{b_extra}）")
            if a_extra:
                a_total_points += a_extra
                a_parts.append(f"{label}（积分 +{a_extra}）")
    out["team_minor_event"] = event_key
    out["team_minor_parts"] = list(b_parts) if b_parts == a_parts else []
    out["team_minor_b_parts"] = b_parts
    out["team_minor_a_parts"] = a_parts
    out["team_minor_b"] = {
        "exp_gain": b_total_exp,
        "points_gain": b_total_points,
        "old_level": b_old,
        "new_level": _level_of(int(b.get("exp", 0))),
    }
    out["team_minor_a"] = {
        "exp_gain": a_total_exp,
        "points_gain": a_total_points,
        "old_level": a_old,
        "new_level": _level_of(int(a.get("exp", 0))),
    }


def _settle_solo(
    user: RpgUserRecord,
    today: str,
    *,
    direct: bool = False,
    rng=random,
    buff: dict | None = None,
) -> dict:
    """单刷完整结算：遭遇(含精英) → 事件 → 胜负（随机系数 + 隐藏运势）→ 发奖（含今日增益）→ 消耗装备。

    `direct=True` 仅用于直接执行「今日打怪」的主动单人线，吃到小额稳定性与经验补偿；
    组队失败后退化成单刷时保持 False，不额外吃这层补偿。
    """
    ccfg = _cfg("combat", {})
    buff = buff or combat._today_buff()
    level = combat._encounter_level(user)
    monster, is_elite = combat._pick_encounter(level, rng)
    eff = combat._eff_monster(monster, is_elite)
    battle_supply = inventory._active_battle_supply(user)
    cp = _battle_power(user, battle_supply)
    margin = cp / max(1, int(eff.get("power_req", 1)))
    event_key = events._roll_hunt_event(margin, rng)
    fortune_factor = utils._fortune_combat_factor(
        user,
        today,
        enabled=bool(ccfg.get("fortune_affects_hunt", True)),
    )
    power_factor = rng.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))
    power_factor *= combat._rookie_power_factor(level)
    if direct:
        power_factor *= 1.0 + _solo_power_bonus()
    res = combat.resolve_hunt(
        cp,
        eff,
        power_factor=power_factor,
        fortune_factor=fortune_factor,
        event=event_key,
    )
    base_win = bool(res["win"])
    support_scene = events._roll_solo_support_scene(bool(res["win"]), rng)
    support_variant = events._roll_support_variant(rng) if support_scene else ""
    if not res["win"] and support_scene in {"toya_rescue", "duo_combo"}:
        res["win"] = True
    battle_guard = inventory._active_battle_supply(user, guard=True)
    guard_triggered = bool(not res["win"] and battle_guard)
    guard_uses_left = 0
    guard_exp_mult = 1.0
    if guard_triggered and battle_guard is not None:
        res["win"] = True
        guard_exp_mult = float(battle_guard["effect"].get("rescue_exp_mult", 1.0))
        guard_uses_left = inventory._consume_battle_supply(user, guard=True)
    exp_mult, drop_mult = combat._reward_mults(buff, is_elite, res["win"])
    exp_bonus = _solo_exp_bonus(bool(res["win"])) if direct else 0.0
    rew = _apply_rewards(user, today, win=res["win"], monster=eff, event_key=event_key,
                         exp_bonus=exp_bonus, exp_mult=exp_mult, drop_mult=drop_mult,
                         battle_supply=battle_supply, rescue_exp_mult=guard_exp_mult, rng=rng)
    out = {**res, **rew, "monster": monster, "event": event_key, "elite": is_elite, "buff": buff,
           "support_scene": support_scene, "support_variant": support_variant,
           "base_win": base_win, "direct_solo": direct,
           "battle_guard_triggered": guard_triggered,
           "battle_guard_name": str(battle_guard.get("name", "")) if guard_triggered and battle_guard else "",
           "battle_guard_uses_left": guard_uses_left}
    _apply_support_bonus(user, out)
    out["reward_new_level"] = int(out.get("new_level", out.get("old_level", 1)))
    _apply_minor_encounter(user, out, rng=rng)
    return out


def _settle_coop(
    b: RpgUserRecord,
    a: RpgUserRecord,
    today: str,
    *,
    exp_bonus: float = 0.0,
    drop_bonus: float = 0.0,
    extra_power_mult: float = 1.0,
    extra_exp_mult: float = 1.0,
    extra_drop_mult: float = 1.0,
    rng=random,
) -> dict:
    """组队合力结算：合力战力（B+A）打一只怪（含精英）、胜负共享；双方各按自身等级/运势/今日增益发奖、各自消耗装备。

    返回 {win, monster, elite, buff, team_event, exp_bonus, drop_bonus, b, a}。
    组队会额外结算平均运势、协作事件，以及随羁绊提升的经验/掉落加成。
    `extra_*` 预留给外层组队关系事件做二次修正。
    """
    ccfg = _cfg("combat", {})
    buff = combat._today_buff()
    level = max(combat._encounter_level(b), combat._encounter_level(a))
    monster, is_elite = combat._pick_encounter(level, rng)
    eff = combat._eff_monster(monster, is_elite)
    b_supply = inventory._active_battle_supply(b)
    a_supply = inventory._active_battle_supply(a)
    cp = _battle_power(b, b_supply) + _battle_power(a, a_supply)
    margin = cp / max(1, int(eff.get("power_req", 1)))
    team_event = events._roll_coop_event(rng)
    event_spec = events._coop_event_spec(team_event)
    fortune_enabled = bool(ccfg.get("fortune_affects_hunt", True))
    fortune_factor = (
        utils._fortune_combat_factor(b, today, enabled=fortune_enabled)
        + utils._fortune_combat_factor(a, today, enabled=fortune_enabled)
    ) / 2.0
    power_bonus = utils._team_power_bonus()
    power_factor = rng.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))
    power_factor *= 1.0 + power_bonus
    if margin > 0 and event_spec.get("power_mult") is not None:
        power_factor *= float(event_spec.get("power_mult", 1.0))
    power_factor *= float(extra_power_mult)
    res = combat.resolve_hunt(
        cp,
        eff,
        power_factor=power_factor,
        fortune_factor=fortune_factor,
    )
    base_win = bool(res["win"])
    win = base_win
    guard_owner = ""
    guard_name = ""
    guard_exp_mult = {"b": 1.0, "a": 1.0}
    if not win:
        for owner, user in (("b", b), ("a", a)):
            guard = inventory._active_battle_supply(user, guard=True)
            if not guard:
                continue
            guard_owner = owner
            guard_name = str(guard.get("name", ""))
            guard_exp_mult[owner] = float(guard["effect"].get("rescue_exp_mult", 1.0))
            inventory._consume_battle_supply(user, guard=True)
            win = True
            break
    exp_mult, drop_mult = combat._reward_mults(buff, is_elite, win)
    exp_mult *= float(event_spec.get("exp_mult", 1.0))
    exp_mult *= float(extra_exp_mult)
    drop_mult *= float(event_spec.get("drop_mult", 1.0))
    drop_mult *= 1.0 + float(drop_bonus)
    drop_mult *= float(extra_drop_mult)
    b_reward = _apply_rewards(
        b,
        today,
        win=win,
        monster=eff,
        exp_bonus=exp_bonus,
        exp_mult=exp_mult,
        drop_mult=drop_mult,
        battle_supply=b_supply,
        rescue_exp_mult=guard_exp_mult["b"],
        rng=rng,
    )
    a_reward = _apply_rewards(
        a,
        today,
        win=win,
        monster=eff,
        exp_bonus=exp_bonus,
        exp_mult=exp_mult,
        drop_mult=drop_mult,
        battle_supply=a_supply,
        rescue_exp_mult=guard_exp_mult["a"],
        rng=rng,
    )
    return {
        "win": win,
        "base_win": base_win,
        "monster": monster,
        "elite": is_elite,
        "buff": buff,
        "team_event": team_event,
        "power_bonus": power_bonus,
        "exp_bonus": exp_bonus,
        "drop_bonus": drop_bonus,
        "battle_guard_owner": guard_owner,
        "battle_guard_name": guard_name,
        "b": b_reward,
        "a": a_reward,
    }
