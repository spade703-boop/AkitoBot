"""组队打怪：打怪前 `组队 @某人` 合力出击。

定调：直接 @ 即组队，不设开关/确认；但**有成功率**——拉不动就失败（只消耗发起人当天的打怪次数 → 退化为单刷）。
成功率与**羁绊（亲密度）等级**正相关，把「送礼攒羁绊 → 组队更易成功」串成闭环。

依赖方向：本模块读 `game_store._get_intimacy` + `gift._bond_level` —— 一条 rpg→gift 单向依赖
（gift 拥有羁绊体系、rpg 组队消费它）；gift 不依赖 rpg，无环。战斗结算复用 hunt（合力/单刷共用一套发奖）。
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
    _first_at_qq,
    _get_group,
    _get_intimacy,
    _load_data,
    _render_with_ats,
    _save_data,
    _today_str,
)
from ..gift import _bond_level  # rpg→gift 单向依赖：消费 gift 的羁绊等级
from .config import _cfg, _copy, _error, _line
from .hunt import _buff_active, _hunt_result_lines, _settle_coop, _settle_solo
from .player import _ensure_player, _equip_intact, _resolve_group

# ==================== 纯逻辑：成功率 / 经验加成（按羁绊等级） ====================

def _team_success_rate(bond_level: int) -> float:
    """组队成功率：base + (羁绊等级-1)*step，clamp 到 [min, max]。羁绊越高越容易拉动。

    羁绊等级以「Hot Dogs(min=0)」为 Lv1；负档（结怨）等级 ≤0 → 落到 min_success 仍可硬拉一把。
    """
    t = _cfg("team", {})
    rate = float(t.get("base_success", 0.35)) + (int(bond_level) - 1) * float(t.get("per_level", 0.12))
    return max(float(t.get("min_success", 0.10)), min(float(t.get("max_success", 0.95)), rate))


def _team_exp_bonus(bond_level: int) -> float:
    """组队经验加成系数：每级 +per，封顶 max，封底 0（Lv1 无加成）。"""
    t = _cfg("team", {})
    return max(0.0, min(float(t.get("exp_bonus_max", 0.50)),
                        (int(bond_level) - 1) * float(t.get("exp_bonus_per_level", 0.05))))


# ==================== 播报组装 ====================

def _member_line(rew: dict, name: str) -> str:
    """组队成功时单个成员的收益行（文本名，不 @）：经验/积分 +（掉落）（升级）。"""
    loot = ""
    if rew.get("drops"):
        loot = "，掉落 " + "、".join(f"{n} ×{c}" for n, c in Counter(rew["drops"]).items())
    levelup = f"，升级 Lv{rew['old_level']}→Lv{rew['new_level']}" if rew["new_level"] > rew["old_level"] else ""
    return _line("team_member", name=name, exp=rew["exp_gain"], points=rew["points_gain"], loot=loot, levelup=levelup)


def _build_coop_broadcast(out: dict, b_id: str, a_id: str, b_name: str, a_name: str):
    """组队成功：合力胜负行（@ 双方，精英冠名）+ 两条成员收益行（文本名）+（今日增益生效则补一行）。"""
    name = out["monster"].get("name", "")
    if out.get("elite"):
        name = "精英·" + name
    head = random.choice(_copy("team_win" if out["win"] else "team_lose"))
    msg = _render_with_ats(head, {"a": b_id, "b": a_id, "monster": name})
    msg = msg + "\n" + _member_line(out["b"], b_name)
    msg = msg + "\n" + _member_line(out["a"], a_name)
    if _buff_active(out.get("buff")):
        msg = msg + "\n" + _line("daily_buff", buff=out["buff"].get("name", ""))
    return msg


def _build_fail_broadcast(out: dict, b_id: str, a_name: str):
    """组队失败：拉不动的前缀（@ 发起人、文本提到对方）+ 发起人单刷结果行。"""
    msg = _render_with_ats(random.choice(_copy("team_fail")), {"a": b_id, "b_name": a_name})
    for ln in _hunt_result_lines(out):
        msg = msg + "\n" + ln
    return msg


# ==================== 指令：组队 ====================

team_cmd = on_command("组队", aliases={"组队挑战"}, priority=5, block=True)


@team_cmd.handle()
async def _(bot: Bot, event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    initiator = event.get_user_id()
    is_superuser = initiator == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    target = _first_at_qq(getattr(event, "original_message", None))
    if not target or target == "all":
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_need_target"))
    if target == initiator:
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_self"))
    if target == str(getattr(bot, "self_id", "")):
        await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_bot"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        b = _ensure_player(group, initiator, _display_name(event))

        # 发起人闸门：今日装备未损坏（与打怪一致）
        if b.get("equip_date") != today:
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if b.get("equip_used") and not is_superuser:
            await team_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        a = _ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if _equip_intact(a, today):
            bond_level = _bond_level(_get_intimacy(group, initiator, target))["level"]
            success = random.random() < _team_success_rate(bond_level)
        else:
            bond_level, success = 0, False  # 对方今天没装备（没签到 / 已打过）→ 拉不动

        if success:
            out = _settle_coop(b, a, today, exp_bonus=_team_exp_bonus(bond_level))
            _save_data(data)
            b_name = b.get("display_name") or f"群友{initiator}"
            msg = _build_coop_broadcast(out, initiator, target, b_name, a_name)
        else:
            out = _settle_solo(b, today)  # 失败：只消耗发起人装备，单刷
            _save_data(data)
            msg = _build_fail_broadcast(out, initiator, a_name)

    await team_cmd.finish(MessageSegment.reply(event.message_id) + msg)
