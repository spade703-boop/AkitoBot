"""打怪（精简版）：用今日装备挑战随机野怪，有胜负有变数；打完装备损坏（每日一次）。

战力 = 今日装备战力（隐藏）；胜负 = 战力×随机系数×运势系数×事件 与 怪 power_req 比较。
经验按等级（胜/负不同）发放；掉落按 怪 drops × (胜负系数 × 运势 drop_factor)。纯逻辑拆出便于单测。
"""

from __future__ import annotations

from collections import Counter
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ, is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _render_with_ats,
    _save_data,
    _today_str,
)
from .analytics import record_battle
from .boss import _cleanup_stale_world_boss, _maybe_spawn_world_boss_lines
from .combat import _buff_active, _eff_monster, _monsters, _today_buff
from .config import _cfg, _copy, _error, _line
from .inventory import _roll_drops
from .player import _ensure_player, _resolve_group
from .rewards import _settle_solo

# ==================== 播报渲染 ====================

def _hunt_event_line(out: dict) -> str:
    """事件行：优先用胜负专属文案，缺失时回退通用事件文案。"""
    m = out["monster"]
    if out.get("event"):
        copy_table = _cfg("copy", {})
        support_scene = str(out.get("support_scene", ""))
        flipped_by_support = (
            (support_scene in {"toya_rescue", "duo_combo"} or out.get("battle_guard_triggered"))
            and not bool(out.get("base_win", out.get("win")))
            and bool(out.get("win"))
        )
        result_key = f"event_{out['event']}_{'win' if out['win'] else 'lose'}"
        if flipped_by_support:
            event_key = f"event_{out['event']}"
        else:
            event_key = result_key if isinstance(copy_table, dict) and copy_table.get(result_key) else f"event_{out['event']}"
        return _render_with_ats(random.choice(_copy(event_key)), {"monster": m.get("name", "")})
    return ""


def _hunt_reward_lines(out: dict) -> list[str]:
    """普通结果行：胜负 → 双倍 → 掉落 → 升级 → 今日增益。"""
    m = out["monster"]
    lines: list[str] = []
    reward_new_level = int(out.get("reward_new_level", out.get("new_level", out.get("old_level", 1))))
    lines.append(_line("hunt_win" if out["win"] else "hunt_lose",
                       monster=m.get("name", ""), exp=out["exp_gain"], points=out["points_gain"]))
    if out.get("exp_buffed"):
        lines.append(_line("hunt_exp_buffed"))
    drops = out.get("drops") or []
    if drops:
        summary = "、".join(f"{n} ×{c}" for n, c in Counter(drops).items())
        lines.append(_line("hunt_loot", loot=summary))
    if reward_new_level > out["old_level"]:
        lines.append(_line("levelup", level=out["old_level"], newlevel=reward_new_level))
    if _buff_active(out.get("buff")):  # 今日增益生效才揭示（平时无感）
        lines.append(_line("daily_buff", buff=out["buff"].get("name", "")))
    return lines


def _hunt_support_lines(out: dict) -> list[str]:
    scene = str(out.get("support_scene", ""))
    if not scene:
        return []
    lines: list[str] = []
    if scene in {"toya_rescue", "duo_combo"}:
        turn_line = _line("hunt_fail_turn")
        if turn_line:
            lines.append(turn_line)
    key = {
        "akito_success": "support_akito_success",
        "akito_fail": "support_akito_fail",
        "toya_rescue": "support_toya_rescue",
        "duo_combo": "support_duo_combo",
    }.get(scene, "")
    if not key:
        return lines
    line = _line(
        key,
        monster=out["monster"].get("name", ""),
        exp=int(out.get("support_exp", 0)),
        points=int(out.get("support_points", 0)),
    )
    if line:
        lines.append(line)
    return lines


def _battle_supply_line(reward: dict) -> str:
    name = str(reward.get("battle_supply_name", ""))
    if not name:
        return ""
    parts = list(reward.get("battle_supply_parts") or [])
    if reward.get("exp_buff_suppressed"):
        parts.append("双倍经验卡暂缓且未消耗")
    return _line(
        "battle_supply_active",
        name=name,
        parts=" / ".join(str(part) for part in parts),
        uses=int(reward.get("battle_supply_uses_left", 0)),
    )


def _battle_debuff_line(reward: dict) -> str:
    name = str(reward.get("battle_debuff_name", ""))
    if not name:
        return ""
    return _line(
        "battle_debuff_active",
        name=name,
        exp=int(round((1.0 - float(reward.get("battle_debuff_exp_mult", 1.0))) * 100)),
        points=int(round((1.0 - float(reward.get("battle_debuff_points_mult", 1.0))) * 100)),
        drop=int(round((1.0 - float(reward.get("battle_debuff_drop_mult", 1.0))) * 100)),
        uses=int(reward.get("battle_debuff_uses_left", 0)),
    )


def _hunt_minor_lines(out: dict) -> list[str]:
    scene = str(out.get("minor_event", ""))
    if not scene:
        return []
    lines = [_line(f"minor_encounter_{scene}")]
    parts = out.get("minor_reward_parts") or []
    if parts:
        lines.append(_line("minor_encounter_reward", parts="、".join(str(part) for part in parts)))
    if int(out.get("minor_new_level", 0)) > int(out.get("minor_old_level", 0)):
        lines.append(
            _line(
                "minor_encounter_levelup",
                level=int(out.get("minor_old_level", 0)),
                newlevel=int(out.get("minor_new_level", 0)),
            )
        )
    return [line for line in lines if line]


def _team_minor_lines(out: dict, b_name: str, a_name: str) -> list[str]:
    scene = str(out.get("team_minor_event", ""))
    if not scene:
        return []
    lines = [_line(f"minor_encounter_team_{scene}")]
    shared_parts = out.get("team_minor_parts") or []
    b_parts = out.get("team_minor_b_parts") or []
    a_parts = out.get("team_minor_a_parts") or []
    if shared_parts:
        lines.append(_line("minor_encounter_team_reward", parts="、".join(str(part) for part in shared_parts)))
    else:
        if b_parts:
            lines.append(_line("minor_encounter_team_member_reward", name=b_name, parts="、".join(str(part) for part in b_parts)))
        if a_parts:
            lines.append(_line("minor_encounter_team_member_reward", name=a_name, parts="、".join(str(part) for part in a_parts)))
    for info, name in ((out.get("team_minor_b") or {}, b_name), (out.get("team_minor_a") or {}, a_name)):
        if int(info.get("new_level", 0)) > int(info.get("old_level", 0)):
            lines.append(
                _line(
                    "minor_encounter_team_member_levelup",
                    name=name,
                    level=int(info.get("old_level", 0)),
                    newlevel=int(info.get("new_level", 0)),
                )
            )
    return [line for line in lines if line]


def _hunt_result_lines(out: dict) -> list:
    """结果行（不含遭遇行）：事件、特判播报、普通结算按场景顺序拼接。"""
    lines: list = []
    event_line = _hunt_event_line(out)
    if event_line:
        lines.append(event_line)
    support_lines = _hunt_support_lines(out)
    if out.get("support_scene") in {"toya_rescue", "duo_combo"}:
        lines.extend(support_lines)
    if out.get("battle_guard_triggered"):
        lines.append(_line("battle_guard_triggered", name=out.get("battle_guard_name", "神官的护符")))
    lines.extend(_hunt_reward_lines(out))
    supply_line = _battle_supply_line(out)
    if supply_line:
        lines.append(supply_line)
    debuff_line = _battle_debuff_line(out)
    if debuff_line:
        lines.append(debuff_line)
    if out.get("support_scene") in {"akito_success", "akito_fail"}:
        lines.extend(support_lines)
    lines.extend(_hunt_minor_lines(out))
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

hunt_cmd = on_command("今日打怪", priority=5, block=True)


@hunt_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await hunt_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await hunt_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, stale_changed = _cleanup_stale_world_boss(group, today)
        user = _ensure_player(group, user_id, _display_name(event))

        # 闸门：今日装备未损坏（= 今天签到过且还没打 → 实现每日一次）
        if user.get("equip_date") != today:
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("need_equip")] if settlement_lines else [_error("need_equip")]
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
        if user.get("equip_used") and not is_superuser:
            if stale_changed:
                _save_data(data)
            lines = [*settlement_lines, _error("equip_broken")] if settlement_lines else [_error("equip_broken")]
            await hunt_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

        old_exp = int(user.get("exp", 0))
        old_points = int(user.get("points", 0))
        out = _settle_solo(user, today, direct=True)
        record_battle(
            group,
            today,
            mode="solo",
            user_ids=[user_id],
            outcome=out,
            exp_gained=int(user.get("exp", 0)) - old_exp,
            points_gained=int(user.get("points", 0)) - old_points,
        )
        boss_lines = _maybe_spawn_world_boss_lines(group, today, user_id, rng=random)
        _save_data(data)

    broadcast = _build_hunt_broadcast(out, user_id)
    if settlement_lines:
        broadcast = "\n".join(settlement_lines) + "\n" + broadcast
    if boss_lines:
        broadcast = broadcast + "\n" + "\n".join(boss_lines)
    await hunt_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)


# ==================== 指令：test打怪掉落（超管） ====================

test_drop_cmd = on_command("test打怪掉落", priority=5, block=True)


@test_drop_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await test_drop_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    # 解析参数：可指定怪名, 可选指定状态
    text = args.extract_plain_text().strip() if args else ""
    parts = text.split() if text else []
    target_name = parts[0] if parts else ""
    flags = set(parts[1:])

    monsters = _monsters()
    candidates = []
    if target_name:
        candidates = [m for m in monsters if m.get("name") == target_name]
        if not candidates:
            await test_drop_cmd.finish(
                MessageSegment.reply(event.message_id) + f"没找到怪「{target_name}」。可用的：{'/'.join(m.get('name','') for m in monsters)}"
            )

    buff = _today_buff()
    elite = "精英" in flags

    lines = ["🧪 掉落测试" + (f"（{target_name}{'·精英' if elite else ''}）" if target_name else "")]
    lines.append(f"今日增益：{buff.get('name','')} xp×{buff.get('exp_mult',1):.1f} drop×{buff.get('drop_mult',1):.1f}")
    lines.append("")

    mons_to_test = candidates if target_name else monsters
    for m in mons_to_test:
        eff = _eff_monster(m, elite)
        lines.append(f"【{eff.get('name','')}】power_req={eff.get('power_req',0)}")
        drops = m.get("drops", [])
        if not drops:
            lines.append("  无掉落配置")
        else:
            for d in drops:
                base = float(d.get("chance", 0))
                # 模拟 win 下的基础倍率
                win_mult = float(_cfg("challenge", {}).get("win_drop_mult", 1.0))
                fortune_factor = 1.0  # 无法模拟运势
                elite_mult = float(_cfg("combat", {}).get("elite", {}).get("drop_mult", 2.0)) if elite else 1.0
                buff_mult = float(buff.get("drop_mult", 1.0))
                full_mult = win_mult * fortune_factor * elite_mult * buff_mult
                effective = base * full_mult
                lines.append(f"  {d.get('item','?')}: 基础{d.get('chance',0)*100:.0f}% ×{full_mult:.2f} = {effective*100:.1f}%")
            # 模拟掷 20 次
            rolled = []
            for _ in range(20):
                r = _roll_drops(m, mult=win_mult * elite_mult * buff_mult)
                for item in r:
                    rolled.append(item)
            from collections import Counter
            counts = Counter(rolled)
            if counts:
                lines.append(f"  20次模拟掉落: {'  '.join(f'{n}×{c}' for n,c in counts.items())}")
            else:
                lines.append("  20次模拟掉落: 无")
        lines.append("")

    await test_drop_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
