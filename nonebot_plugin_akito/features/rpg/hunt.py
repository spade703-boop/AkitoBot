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


def _build_hunt_broadcast(out: dict, user_id: str, old_level: int, new_level: int):
    """遭遇 →（事件）→ 结果 →（双倍）→（掉落）→（升级），合并单条消息，仅遭遇行带真 @。"""
    m = out["monster"]
    lines = [_render_with_ats(random.choice(_copy("hunt_encounter")), {"a": user_id, "monster": m.get("name", "")})]
    if out["event"]:
        lines.append(_render_with_ats(random.choice(_copy(f"event_{out['event']}")), {"monster": m.get("name", "")}))
    lines.append(_line("hunt_win" if out["win"] else "hunt_lose", monster=m.get("name", ""), exp=out["exp_gain"]))
    if out.get("exp_buffed"):
        lines.append(_line("hunt_exp_buffed"))
    drops = out.get("drops") or []
    if drops:
        summary = "、".join(f"{n} ×{c}" for n, c in Counter(drops).items())
        lines.append(_line("hunt_loot", loot=summary))
    if new_level > old_level:
        lines.append(_line("levelup", level=old_level, newlevel=new_level))
    msg = lines[0]
    for ln in lines[1:]:
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

        # 闸门：今日装备（替代精力做每日一次限制）
        if user.get("equip_date") != today:
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if user.get("equip_used") and not is_superuser:
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        cp = _combat_power(user)
        monster = _pick_monster()
        margin = cp / max(1, int(monster.get("power_req", 1)))
        event_key = _roll_hunt_event(margin)
        fortune_factor = _fortune_combat_factor(user, today)
        ccfg = _cfg("combat", {})
        power_factor = random.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))

        out = resolve_hunt(cp, monster, power_factor=power_factor, fortune_factor=fortune_factor, event=event_key)

        old_exp = int(user.get("exp", 0))
        level = _level_of(old_exp)
        exp_gain = _challenge_exp(out["win"], level)
        if out["win"] and event_key == "insight":
            exp_gain = int(exp_gain * float(ccfg.get("events", {}).get("insight", {}).get("exp_mult", 1.5)))
        # 双倍经验卡
        if int(user.get("exp_buff_uses", 0)) > 0:
            exp_gain *= int(user.get("exp_buff_mult", 2))
            out["exp_buffed"] = True
            user["exp_buff_uses"] = int(user["exp_buff_uses"]) - 1
        out["exp_gain"] = exp_gain
        user["exp"] = old_exp + exp_gain

        # 掉落：怪 drops × (胜负系数 × 运势 drop_factor)
        cc = _cfg("challenge", {})
        drop_mult = float(cc.get("win_drop_mult", 1.0) if out["win"] else cc.get("lose_drop_mult", 0.3))
        drop_mult *= _fortune_drop_factor(user, today)
        drops = _roll_drops(monster, mult=drop_mult)
        for d in drops:
            _add_item(user, d, 1)
        out["drops"] = drops

        _consume_equip(user)  # 今日装备损坏
        new_level = _level_of(user["exp"])
        _save_data(data)

    broadcast = _build_hunt_broadcast(out, user_id, level, new_level)
    await hunt_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)
