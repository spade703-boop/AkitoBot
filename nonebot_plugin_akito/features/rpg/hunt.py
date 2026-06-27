"""打野怪：消耗精力挑战野怪，按战力 + 随机事件结算，产出经验与积分。

战斗判定拆成纯函数（_pick_monster / _roll_hunt_event / resolve_hunt），便于注入桩 rng 做确定性单测；
指令 handler 只负责 加载/扣精力/落库/组装播报，与 gift 共享同一份存储与同一把锁。
"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import MessageSegment

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import (
    LOCK,
    _add_points,
    _display_name,
    _get_group,
    _load_data,
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
)
from .config import _cfg, _copy, _error
from .fortune import _fortune_by_key
from .player import (
    _combat_power,
    _ensure_player,
    _level_of,
    _power_for_level,
    _refill_stamina,
    _resolve_group,
    _stamina_cost,
)

# ==================== 纯逻辑：遭遇 / 事件 / 结算 ====================

def _monsters() -> list[dict]:
    monsters = _cfg("monsters", [])
    return monsters if isinstance(monsters, list) and monsters else []


def _pick_monster(rng=random) -> dict:
    """按 weight 加权抽一只野怪。"""
    pool = _monsters()
    weights = [max(0, int(m.get("weight", 0))) for m in pool]
    if not pool or sum(weights) <= 0:
        return pool[0] if pool else {"name": "野怪", "level": 1, "power_req": 10, "exp": 10, "points": 0}
    return rng.choices(pool, weights=weights, k=1)[0]


def _roll_hunt_event(margin: float, rng=random) -> str:
    """按战力优势分档抽随机事件 key（碾压→看破 / 劣势→爆发 / 其余→打滑），可能返回 '' 表示无事件。"""
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
    """当日运势给打野的战力修正系数（关闭或未签到则为 1.0）。"""
    ccfg = _cfg("combat", {})
    if not ccfg.get("fortune_affects_hunt", True):
        return 1.0
    if user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("combat_factor", 1.0))


def resolve_hunt(combat_power: int, monster: dict, *, power_factor: float,
                 fortune_factor: float = 1.0, event: str | None = None) -> dict:
    """纯结算：给定战力/野怪/随机系数/运势系数/事件，算出胜负与经验积分收益。不做 IO、不依赖全局 rng。"""
    ccfg = _cfg("combat", {})
    ev = ccfg.get("events", {}).get(event or "", {})

    effective = combat_power * float(power_factor) * float(fortune_factor)
    if "power_mult" in ev:
        effective *= float(ev["power_mult"])

    power_req = int(monster.get("power_req", 0))
    win = effective >= power_req

    if win:
        exp_gain = int(monster.get("exp", 0))
        if "exp_mult" in ev:
            exp_gain = int(exp_gain * float(ev["exp_mult"]))
        points_gain = int(monster.get("points", 0))
    else:
        exp_gain = int(int(monster.get("exp", 0)) * float(ccfg.get("lose_exp_ratio", 0.2)))
        points_gain = 0

    return {
        "win": win,
        "exp_gain": exp_gain,
        "points_gain": points_gain,
        "effective": int(effective),
        "event": event or "",
        "monster": monster,
    }


def _build_hunt_broadcast(out: dict, user_id: str, cost: int, stamina_left: int,
                          old_level: int, new_level: int):
    """遭遇 → (随机事件) → 结果 →（升级）多段合并为单条消息，仅遭遇行带真 @。"""
    m = out["monster"]
    lines = [_render_with_ats(
        random.choice(_copy("hunt_encounter")),
        {"a": user_id, "monster": m.get("name", ""), "mlevel": m.get("level", 1)},
    )]
    if out["event"]:
        lines.append(_render_with_ats(random.choice(_copy(f"event_{out['event']}")), {"monster": m.get("name", "")}))
    result_key = "hunt_win" if out["win"] else "hunt_lose"
    lines.append(_render_with_ats(random.choice(_copy(result_key)), {
        "monster": m.get("name", ""), "exp": out["exp_gain"], "points": out["points_gain"],
        "cost": cost, "stamina": stamina_left,
    }))
    if new_level > old_level:
        lines.append(_render_with_ats(random.choice(_copy("levelup")), {
            "level": old_level, "newlevel": new_level,
            "power": _power_for_level(old_level), "newpower": _power_for_level(new_level),
        }))

    msg = lines[0]
    for ln in lines[1:]:
        msg = msg + "\n" + ln
    return msg


# ==================== 指令：打野 ====================

hunt_cmd = on_command("打野", aliases={"打野怪"}, priority=5, block=True)


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
    cost = _stamina_cost()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, user_id, _display_name(event))
        _refill_stamina(user, today)

        stamina = int(user.get("stamina", 0))
        if not is_superuser and stamina < cost:
            await hunt_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("no_stamina", cost=cost, stamina=stamina)
            )

        user["stamina"] = max(0, stamina - cost)

        monster = _pick_monster()
        cp = _combat_power(user)
        margin = cp / max(1, int(monster.get("power_req", 1)))
        event_key = _roll_hunt_event(margin)
        fortune_factor = _fortune_combat_factor(user, today)
        ccfg = _cfg("combat", {})
        power_factor = random.uniform(float(ccfg.get("factor_min", 0.8)), float(ccfg.get("factor_max", 1.2)))

        old_exp = int(user.get("exp", 0))
        out = resolve_hunt(cp, monster, power_factor=power_factor,
                           fortune_factor=fortune_factor, event=event_key)
        user["exp"] = old_exp + out["exp_gain"]
        if out["points_gain"]:
            _add_points(group, user_id, out["points_gain"])

        old_level = _level_of(old_exp)
        new_level = _level_of(user["exp"])
        stamina_left = int(user.get("stamina", 0))
        _save_data(data)

    broadcast = _build_hunt_broadcast(out, user_id, cost, stamina_left, old_level, new_level)
    await hunt_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)
