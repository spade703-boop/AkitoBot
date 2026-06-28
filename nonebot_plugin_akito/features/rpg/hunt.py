"""打怪（精简版）：用今日装备挑战随机野怪，有胜负有变数；打完装备损坏（每日一次）。

战力 = 今日装备战力（隐藏）；胜负 = 战力×随机系数×运势系数×事件 与 怪 power_req 比较。
经验按等级（胜/负不同）发放；掉落按 怪 drops × (胜负系数 × 运势 drop_factor)。纯逻辑拆出便于单测。
"""

from __future__ import annotations

from collections import Counter
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageSegment

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
)
from .config import _cfg, _copy, _error, _line
from .fortune import _fortune_by_key
from .inventory import _add_item, _roll_drops
from .player import _combat_power, _consume_equip, _ensure_player, _level_of, _resolve_group

# ==================== 纯逻辑：遭遇 / 事件 / 胜负 / 经验 ====================

def _monsters() -> list[dict]:
    monsters = _cfg("monsters", [])
    return monsters if isinstance(monsters, list) and monsters else []


def _pick_monster(rng=random) -> dict:
    pool = _monsters()
    weights = [max(0, int(m.get("weight", 0))) for m in pool]
    if not pool or sum(weights) <= 0:
        return pool[0] if pool else {"name": "野怪", "power_req": 10}
    return rng.choices(pool, weights=weights, k=1)[0]


def _pick_encounter(rng=random) -> tuple[dict, bool]:
    """抽遭遇：先按权重抽怪，再按 combat.elite.chance 掷是否精英（精英更难打、胜则更肥）。"""
    monster = _pick_monster(rng)
    chance = float(_cfg("combat", {}).get("elite", {}).get("chance", 0.0))
    return monster, (rng.random() < chance)


def _today_buff() -> dict:
    """今日增益：以日期为种子从 daily_buffs 加权选一个（同一天全群一致、可单测）。

    返回含 name/exp_mult/drop_mult 的 spec；缺省/空表回落到「平日」（无效果、不外显）。
    """
    buffs = _cfg("daily_buffs", {})
    if not isinstance(buffs, dict) or not buffs:
        return {"key": "plain", "name": "平日", "exp_mult": 1.0, "drop_mult": 1.0}
    weights = {k: int(v.get("weight", 0)) for k, v in buffs.items()}
    key = _weighted_choice(weights, random.Random(_today_str()))
    spec = dict(buffs.get(key, {}))
    spec.setdefault("key", key)
    spec.setdefault("name", key)
    spec.setdefault("exp_mult", 1.0)
    spec.setdefault("drop_mult", 1.0)
    return spec


def _buff_active(buff: dict | None) -> bool:
    """今日增益是否真正生效（非平日）——决定是否在播报里揭示。"""
    return bool(buff) and (float(buff.get("exp_mult", 1.0)) != 1.0 or float(buff.get("drop_mult", 1.0)) != 1.0)


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


def _fortune_combat_factor(user: dict, today: str) -> float:
    """当日隐藏运势给打怪的战力系数（关闭或未签到则 1.0）。"""
    ccfg = _cfg("combat", {})
    if not ccfg.get("fortune_affects_hunt", True) or user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("combat_factor", 1.0))


def _fortune_drop_factor(user: dict, today: str) -> float:
    """当日隐藏运势给掉落的概率系数（未签到则 1.0）。"""
    if user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("drop_factor", 1.0))


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


def _challenge_exp(win: bool, level: int) -> int:
    """打怪经验：按等级（胜/负不同）。"""
    c = _cfg("challenge", {})
    if win:
        return int(c.get("win_exp_base", 60)) + level * int(c.get("win_exp_per_level", 10))
    return int(c.get("lose_exp_base", 15)) + level * int(c.get("lose_exp_per_level", 2))


def _challenge_points(win: bool) -> int:
    """打怪积分（少量）：把「打怪赚分 → 送礼攒羁绊 → 组队」串成闭环。"""
    c = _cfg("challenge", {})
    return int(c.get("win_points", 30)) if win else int(c.get("lose_points", 10))


def _apply_rewards(user: dict, today: str, *, win: bool, monster: dict, event_key: str = "",
                   exp_bonus: float = 0.0, exp_mult: float = 1.0, drop_mult: float = 1.0) -> dict:
    """给单个玩家结算（经验[含看破/双倍卡/组队加成/精英/今日增益] + 掉落 + 积分）并消耗其今日装备，记一次战绩。

    `exp_mult`/`drop_mult` 由调用方算好（精英 × 今日增益）传入。
    返回奖励明细 {exp_gain, exp_buffed, drops, points_gain, old_level, new_level}（不含播报）。
    单刷与组队（双方各调一次）共用本函数：胜负由调用方判定后传入。
    """
    ccfg = _cfg("combat", {})
    old_exp = int(user.get("exp", 0))
    level = _level_of(old_exp)
    exp_gain = _challenge_exp(win, level)
    if win and event_key == "insight":
        exp_gain = int(exp_gain * float(ccfg.get("events", {}).get("insight", {}).get("exp_mult", 1.5)))
    if exp_bonus:
        exp_gain = int(exp_gain * (1.0 + float(exp_bonus)))  # 组队羁绊加成
    if exp_mult != 1.0:
        exp_gain = int(exp_gain * float(exp_mult))           # 精英 × 今日增益
    buffed = False
    if int(user.get("exp_buff_uses", 0)) > 0:  # 双倍经验卡
        exp_gain *= int(user.get("exp_buff_mult", 2))
        buffed = True
        user["exp_buff_uses"] = int(user["exp_buff_uses"]) - 1
    user["exp"] = old_exp + exp_gain

    cc = _cfg("challenge", {})
    base_drop = float(cc.get("win_drop_mult", 1.0) if win else cc.get("lose_drop_mult", 0.3))
    drops = _roll_drops(monster, mult=base_drop * _fortune_drop_factor(user, today) * float(drop_mult))
    for d in drops:
        _add_item(user, d, 1)

    points_gain = _challenge_points(win)
    user["points"] = int(user.get("points", 0)) + points_gain
    user["hunt_total"] = int(user.get("hunt_total", 0)) + 1   # 战绩：累计打怪
    if win:
        user["hunt_wins"] = int(user.get("hunt_wins", 0)) + 1  # 战绩：累计胜场
    _consume_equip(user)  # 今日装备损坏
    return {
        "exp_gain": exp_gain, "exp_buffed": buffed, "drops": drops,
        "points_gain": points_gain, "old_level": level, "new_level": _level_of(user["exp"]),
    }


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


def _settle_solo(user: dict, today: str) -> dict:
    """单刷完整结算：遭遇(含精英) → 事件 → 胜负（随机系数 + 隐藏运势）→ 发奖（含今日增益）→ 消耗装备。返回 out。"""
    ccfg = _cfg("combat", {})
    buff = _today_buff()
    monster, is_elite = _pick_encounter()
    eff = _eff_monster(monster, is_elite)
    cp = _combat_power(user)
    margin = cp / max(1, int(eff.get("power_req", 1)))
    event_key = _roll_hunt_event(margin)
    fortune_factor = _fortune_combat_factor(user, today)
    power_factor = random.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))
    res = resolve_hunt(cp, eff, power_factor=power_factor, fortune_factor=fortune_factor, event=event_key)
    exp_mult, drop_mult = _reward_mults(buff, is_elite, res["win"])
    rew = _apply_rewards(user, today, win=res["win"], monster=eff, event_key=event_key,
                         exp_mult=exp_mult, drop_mult=drop_mult)
    return {**res, **rew, "monster": monster, "event": event_key, "elite": is_elite, "buff": buff}


def _settle_coop(b: dict, a: dict, today: str, *, exp_bonus: float = 0.0) -> dict:
    """组队合力结算：合力战力（B+A）打一只怪（含精英）、胜负共享；双方各按自身等级/运势/今日增益发奖、各自消耗装备。

    返回 {win, monster, elite, buff, b: 奖励明细, a: 奖励明细}（不落库/不播报）。合力更易赢；不叠加随机事件/运势系数（保持简单）。
    """
    ccfg = _cfg("combat", {})
    buff = _today_buff()
    monster, is_elite = _pick_encounter()
    eff = _eff_monster(monster, is_elite)
    cp = _combat_power(b) + _combat_power(a)
    power_factor = random.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))
    res = resolve_hunt(cp, eff, power_factor=power_factor)
    win = res["win"]
    exp_mult, drop_mult = _reward_mults(buff, is_elite, win)
    return {
        "win": win,
        "monster": monster,
        "elite": is_elite,
        "buff": buff,
        "b": _apply_rewards(b, today, win=win, monster=eff, exp_bonus=exp_bonus, exp_mult=exp_mult, drop_mult=drop_mult),
        "a": _apply_rewards(a, today, win=win, monster=eff, exp_bonus=exp_bonus, exp_mult=exp_mult, drop_mult=drop_mult),
    }


def _hunt_result_lines(out: dict) -> list:
    """结果行（不含遭遇行）：事件 →（胜负 = 经验 + 积分）→ 双倍 → 掉落 → 升级。供单刷与组队失败单刷复用。"""
    m = out["monster"]
    lines: list = []
    if out.get("event"):
        lines.append(_render_with_ats(random.choice(_copy(f"event_{out['event']}")), {"monster": m.get("name", "")}))
    lines.append(_line("hunt_win" if out["win"] else "hunt_lose",
                       monster=m.get("name", ""), exp=out["exp_gain"], points=out["points_gain"]))
    if out.get("exp_buffed"):
        lines.append(_line("hunt_exp_buffed"))
    drops = out.get("drops") or []
    if drops:
        summary = "、".join(f"{n} ×{c}" for n, c in Counter(drops).items())
        lines.append(_line("hunt_loot", loot=summary))
    if out["new_level"] > out["old_level"]:
        lines.append(_line("levelup", level=out["old_level"], newlevel=out["new_level"]))
    if _buff_active(out.get("buff")):  # 今日增益生效才揭示（平时无感）
        lines.append(_line("daily_buff", buff=out["buff"].get("name", "")))
    return lines


def _build_hunt_broadcast(out: dict, user_id: str):
    """遭遇行（带真 @，精英走专属文案）+ 结果行，合并单条消息。"""
    m = out["monster"]
    enc_key = "hunt_encounter_elite" if out.get("elite") else "hunt_encounter"
    msg = _render_with_ats(random.choice(_copy(enc_key)), {"a": user_id, "monster": m.get("name", "")})
    for ln in _hunt_result_lines(out):
        msg = msg + "\n" + ln
    return msg


# ==================== 指令：打怪 ====================

hunt_cmd = on_command("打怪", aliases={"打野", "挑战"}, priority=5, block=True)


@hunt_cmd.handle()
async def _(bot: Bot, event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await hunt_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, user_id, _display_name(event))

        # 闸门：今日装备未损坏（替代精力做每日一次限制）
        if user.get("equip_date") != today:
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if user.get("equip_used") and not is_superuser:
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        out = _settle_solo(user, today)
        _save_data(data)

    broadcast = _build_hunt_broadcast(out, user_id)
    await hunt_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)
