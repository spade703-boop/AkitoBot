"""奖励系统：经验计算、掉落处理、积分发放、装备消耗。

本模块包含单人/组队线的奖励结算逻辑，依赖战斗核心和事件系统。
"""

from __future__ import annotations

import random

from .....core.types import GroupRecord
from ... import utils
from ...config import _cfg
from ...inventory import inventory
from ...player.player import _combat_power, _level_of  # noqa: F401
from ...types import RpgUserRecord
from .. import combat, events, friend_support
from .calculations import (  # noqa: F401  (stable settlement namespace)
    _apply_extra_rewards,
    _apply_rewards,
    _battle_power,
    _challenge_exp,
    _challenge_points,
    _rebuy_exp_mult,
    _rebuy_points_mult,
    _solo_exp_bonus,
    _solo_power_bonus,
)
from .encounters import (  # noqa: F401  (stable settlement namespace)
    _apply_minor_encounter,
    _apply_support_bonus,
    _apply_team_minor_encounter,
    _support_bonus_exp,
    _support_bonus_points,
)


def _settle_solo(
    user: RpgUserRecord,
    today: str,
    *,
    direct: bool = False,
    group: GroupRecord | None = None,
    participant_ids: tuple[str, ...] | list[str] = (),
    excluded_user_ids: tuple[str, ...] | list[str] = (),
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
    friend_support_result = friend_support.roll_friend_support(
        group,
        participant_ids,
        excluded_user_ids=excluded_user_ids,
        rng=rng,
    )
    if friend_support_result:
        power_factor *= float(friend_support_result.get("power_mult", 1.0))
    res = combat.resolve_hunt(
        cp,
        eff,
        power_factor=power_factor,
        fortune_factor=fortune_factor,
        event=event_key,
    )
    base_win = bool(res["win"])
    if friend_support_result and friend_support_result.get("polarity") == "positive" and not res["win"]:
        rescue_chance = float(friend_support_result.get("rescue_chance", 0.0))
        if rescue_chance > 0 and rng.random() < rescue_chance:
            res["win"] = True
            friend_support_result["rescue_triggered"] = True
    support_scene = ""
    support_variant = ""
    if not friend_support_result:
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
    friend_exp_mult = float(friend_support_result.get("exp_mult", 1.0)) if friend_support_result else 1.0
    friend_points_mult = float(friend_support_result.get("points_mult", 1.0)) if friend_support_result else 1.0
    friend_drop_mult = float(friend_support_result.get("drop_mult", 1.0)) if friend_support_result else 1.0
    exp_mult *= friend_exp_mult
    drop_mult *= friend_drop_mult
    exp_bonus = _solo_exp_bonus(bool(res["win"])) if direct else 0.0
    rew = _apply_rewards(user, today, win=res["win"], monster=eff, event_key=event_key,
                         exp_bonus=exp_bonus, exp_mult=exp_mult, points_mult=friend_points_mult,
                         drop_mult=drop_mult,
                         battle_supply=battle_supply, rescue_exp_mult=guard_exp_mult, rng=rng)
    out = {**res, **rew, "monster": monster, "event": event_key, "elite": is_elite, "buff": buff,
           "support_scene": support_scene, "support_variant": support_variant,
           "friend_support": friend_support_result,
           "player_name": str(user.get("display_name") or "冒险者"),
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
    group: GroupRecord | None = None,
    participant_ids: tuple[str, ...] | list[str] = (),
    excluded_user_ids: tuple[str, ...] | list[str] = (),
    exp_bonus: float = 0.0,
    drop_bonus: float = 0.0,
    extra_power_mult: float = 1.0,
    extra_exp_mult: float = 1.0,
    extra_points_mult: float = 1.0,
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
    friend_support_result = friend_support.roll_friend_support(
        group,
        participant_ids,
        excluded_user_ids=excluded_user_ids,
        rng=rng,
    )
    if friend_support_result:
        power_factor *= float(friend_support_result.get("power_mult", 1.0))
    res = combat.resolve_hunt(
        cp,
        eff,
        power_factor=power_factor,
        fortune_factor=fortune_factor,
    )
    base_win = bool(res["win"])
    win = base_win
    if friend_support_result and friend_support_result.get("polarity") == "positive" and not win:
        rescue_chance = float(friend_support_result.get("rescue_chance", 0.0))
        if rescue_chance > 0 and rng.random() < rescue_chance:
            win = True
            friend_support_result["rescue_triggered"] = True
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
    friend_exp_mult = float(friend_support_result.get("exp_mult", 1.0)) if friend_support_result else 1.0
    friend_points_mult = float(friend_support_result.get("points_mult", 1.0)) if friend_support_result else 1.0
    friend_drop_mult = float(friend_support_result.get("drop_mult", 1.0)) if friend_support_result else 1.0
    exp_mult *= friend_exp_mult
    drop_mult *= friend_drop_mult
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
        points_mult=extra_points_mult * friend_points_mult,
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
        points_mult=extra_points_mult * friend_points_mult,
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
        "friend_support": friend_support_result,
        "battle_guard_owner": guard_owner,
        "battle_guard_name": guard_name,
        "b": b_reward,
        "a": a_reward,
    }
