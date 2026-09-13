"""普通打怪与组队共用的播报渲染。"""

from __future__ import annotations

from collections import Counter
import random

from ....core.game_store import _render_with_ats
from ..battle.combat import _buff_active
from ..battle.friend_support import render_friend_support_line
from ..config import _cfg, _copy, _line, _variant_line


def configure_helpers(*, cfg, copy, line, variant_line) -> None:
    globals()["_cfg"] = cfg
    globals()["_copy"] = copy
    globals()["_line"] = line
    globals()["_variant_line"] = variant_line


def _hunt_event_line(out: dict) -> str:
    monster = out["monster"]
    if not out.get("event"):
        return ""
    copy_table = _cfg("copy", {})
    support_scene = str(out.get("support_scene", ""))
    friend_support = out.get("friend_support")
    friend_rescue = isinstance(friend_support, dict) and friend_support.get("rescue_triggered")
    flipped = (
        (support_scene in {"toya_rescue", "duo_combo"} or out.get("battle_guard_triggered") or friend_rescue)
        and not bool(out.get("base_win", out.get("win")))
        and bool(out.get("win"))
    )
    result_key = f"event_{out['event']}_{'win' if out['win'] else 'lose'}"
    event_key = (
        f"event_{out['event']}"
        if flipped
        else result_key
        if isinstance(copy_table, dict) and copy_table.get(result_key)
        else f"event_{out['event']}"
    )
    return _render_with_ats(random.choice(_copy(event_key)), {"monster": monster.get("name", "")})


def _hunt_reward_lines(out: dict) -> list[str]:
    monster = out["monster"]
    lines = [
        _line(
            "hunt_win" if out["win"] else "hunt_lose",
            monster=monster.get("name", ""),
            exp=out["exp_gain"],
            points=out["points_gain"],
        )
    ]
    reward_level = int(out.get("reward_new_level", out.get("new_level", out.get("old_level", 1))))
    if out.get("exp_buffed"):
        lines.append(_line("hunt_exp_buffed"))
    drops = out.get("drops") or []
    if drops:
        lines.append(_line("hunt_loot", loot="、".join(f"{name} ×{count}" for name, count in Counter(drops).items())))
    if reward_level > out["old_level"]:
        lines.append(_line("levelup", level=out["old_level"], newlevel=reward_level))
    if _buff_active(out.get("buff")):
        lines.append(_line("daily_buff", buff=out["buff"].get("name", "")))
    return lines


def _hunt_support_lines(out: dict) -> list[str]:
    scene = str(out.get("support_scene", ""))
    if not scene:
        return []
    lines: list[str] = []
    if scene in {"toya_rescue", "duo_combo"}:
        lines.append(_line("hunt_fail_turn"))
    event_key = {
        "akito_success": "akito_success",
        "akito_fail": "akito_fail",
        "toya_rescue": "toya_rescue",
        "duo_combo": "duo_combo",
    }.get(scene, "")
    if not event_key:
        return lines
    support_line = _variant_line(
        "support",
        event_key,
        str(out.get("support_variant", "")),
        monster=out["monster"].get("name", ""),
        exp=int(out.get("support_exp", 0)),
        points=int(out.get("support_points", 0)),
    )
    if support_line:
        lines.append(support_line)
    return lines


def _hunt_friend_support_lines(out: dict) -> list:
    result = out.get("friend_support")
    if not isinstance(result, dict):
        return []
    line = render_friend_support_line(
        result, target_name=str(out.get("player_name", "冒险者")), battle_won=bool(out.get("win"))
    )
    return [line] if line else []


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
    shared = out.get("team_minor_parts") or []
    b_parts = out.get("team_minor_b_parts") or []
    a_parts = out.get("team_minor_a_parts") or []
    if shared:
        lines.append(_line("minor_encounter_team_reward", parts="、".join(str(part) for part in shared)))
    else:
        if b_parts:
            lines.append(
                _line("minor_encounter_team_member_reward", name=b_name, parts="、".join(str(part) for part in b_parts))
            )
        if a_parts:
            lines.append(
                _line("minor_encounter_team_member_reward", name=a_name, parts="、".join(str(part) for part in a_parts))
            )
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
    lines: list = []
    event_line = _hunt_event_line(out)
    if event_line:
        lines.append(event_line)
    lines.extend(_hunt_friend_support_lines(out))
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


__all__ = [
    "_battle_debuff_line",
    "_battle_supply_line",
    "_hunt_event_line",
    "_hunt_friend_support_lines",
    "_hunt_minor_lines",
    "_hunt_result_lines",
    "_hunt_reward_lines",
    "_hunt_support_lines",
    "_team_minor_lines",
]
