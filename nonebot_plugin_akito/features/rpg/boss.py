"""世界 BOSS：在常规打怪后极低概率出现的全群共享目标。"""

from __future__ import annotations

from datetime import date, timedelta
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

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
    _weighted_choice,
)
from ..gift import _bond_level
from .config import _cfg, _copy, _error, _line
from .fortune import _fortune_by_key
from .player import _combat_power, _consume_equip, _ensure_player, _level_of, _resolve_group


def _world_boss_cfg() -> dict:
    cfg = _cfg("world_boss", {})
    return cfg if isinstance(cfg, dict) else {}


def _team_success_rate(bond_level: int) -> float:
    tcfg = _cfg("team", {})
    rate = float(tcfg.get("base_success", 0.35)) + (int(bond_level) - 1) * float(tcfg.get("per_level", 0.12))
    return max(float(tcfg.get("min_success", 0.10)), min(float(tcfg.get("max_success", 0.95)), rate))


def _roll_team_fail_flavor(rng=random) -> str:
    weights = _cfg("team", {}).get("fail_flavor", {})
    if not isinstance(weights, dict) or not weights:
        return ""
    cands = {key: int(weight) for key, weight in weights.items()}
    if sum(cands.values()) <= 0:
        return ""
    return _weighted_choice(cands, rng)


def _rpg_state(group: dict) -> dict:
    state = group.get("rpg")
    if not isinstance(state, dict):
        state = {}
        group["rpg"] = state
    return state


def _parse_iso_day(text) -> date | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _active_world_boss(group: dict, today: str, *, clear_stale: bool = True) -> dict | None:
    state = _rpg_state(group)
    boss = state.get("world_boss")
    if not isinstance(boss, dict):
        state.pop("world_boss", None)
        return None
    if boss.get("date") != today:
        if clear_stale:
            state.pop("world_boss", None)
        return None
    contributors = boss.get("contributors")
    if not isinstance(contributors, dict):
        contributors = {}
        boss["contributors"] = contributors
    return boss


def _recent_active_user_ids(group: dict, today: str) -> list[str]:
    cfg = _world_boss_cfg()
    window = max(1, int(cfg.get("activity_window_days", 7)))
    today_day = _parse_iso_day(today)
    if today_day is None:
        return []
    cutoff = today_day - timedelta(days=window - 1)
    user_ids: list[str] = []
    for uid, rec in group.get("users", {}).items():
        if not isinstance(rec, dict):
            continue
        last_days = [_parse_iso_day(rec.get("last_sign_in")), _parse_iso_day(rec.get("signin_last_date"))]
        if any(day is not None and cutoff <= day <= today_day for day in last_days):
            user_ids.append(str(uid))
    return user_ids


def _expected_daily_power(user: dict) -> int:
    ecfg = _cfg("equip", {})
    level = _level_of(int(user.get("exp", 0)))
    base = int(ecfg.get("base", 10))
    per_level = int(ecfg.get("per_level", 5))
    expected_roll = int(ecfg.get("var", 6)) // 2
    return max(1, base + level * per_level + expected_roll)


def _world_boss_snapshot(group: dict, today: str) -> dict:
    cfg = _world_boss_cfg()
    active_ids = _recent_active_user_ids(group, today)
    active_count = len(active_ids)
    min_users = max(1, int(cfg.get("activity_min_users", 3)))
    cap = max(min_users, int(cfg.get("activity_scale_cap", 12)))
    if active_count < min_users:
        return {
            "spawnable": False,
            "recent_active_count": active_count,
            "scale_count": min(active_count, cap),
            "avg_level": 1,
            "avg_power": 1,
            "max_hp": 1,
        }

    levels: list[int] = []
    powers: list[int] = []
    for uid in active_ids:
        rec = group.get("users", {}).get(uid, {})
        if not isinstance(rec, dict):
            continue
        levels.append(_level_of(int(rec.get("exp", 0))))
        powers.append(_expected_daily_power(rec))
    avg_level = max(1, round(sum(levels) / len(levels))) if levels else 1
    avg_power = max(1, round(sum(powers) / len(powers))) if powers else 1
    scale_count = min(active_count, cap)
    hp_factor = float(cfg.get("hp_factor", 1.0))
    max_hp = max(1, round(avg_power * scale_count * hp_factor))
    return {
        "spawnable": True,
        "recent_active_count": active_count,
        "scale_count": scale_count,
        "avg_level": avg_level,
        "avg_power": avg_power,
        "max_hp": max_hp,
    }


def _spawn_world_boss(group: dict, today: str, user_id: str, rng=random, snapshot: dict | None = None) -> dict | None:
    snap = snapshot or _world_boss_snapshot(group, today)
    if not snap.get("spawnable"):
        return None
    boss_names = _world_boss_cfg().get("boss_names", [])
    if not isinstance(boss_names, list) or not boss_names:
        boss_names = ["赤鳞灾龙"]
    boss = {
        "date": today,
        "name": str(rng.choice(boss_names)),
        "max_hp": int(snap["max_hp"]),
        "hp": int(snap["max_hp"]),
        "recent_active_count": int(snap["recent_active_count"]),
        "scale_count": int(snap["scale_count"]),
        "avg_level": int(snap["avg_level"]),
        "avg_power": int(snap["avg_power"]),
        "contributors": {},
        "spawned_by": str(user_id),
    }
    _rpg_state(group)["world_boss"] = boss
    return boss


def _maybe_spawn_world_boss(group: dict, today: str, user_id: str, rng=random) -> dict | None:
    if _active_world_boss(group, today):
        return None
    snapshot = _world_boss_snapshot(group, today)
    if not snapshot.get("spawnable"):
        return None
    if float(rng.random()) >= float(_world_boss_cfg().get("spawn_chance", 0.001)):
        return None
    return _spawn_world_boss(group, today, user_id, rng=rng, snapshot=snapshot)


def _maybe_spawn_world_boss_lines(group: dict, today: str, user_id: str, rng=random) -> list[str]:
    boss = _maybe_spawn_world_boss(group, today, user_id, rng=rng)
    return _world_boss_spawn_lines(boss) if boss else []


def _fortune_combat_factor(user: dict, today: str) -> float:
    if user.get("fortune_date") != today:
        return 1.0
    return float(_fortune_by_key(user.get("fortune", "")).get("combat_factor", 1.0))


def _boss_damage(user: dict, today: str, *, virtual_bonus: int = 0, rng=random) -> int:
    cfg = _world_boss_cfg()
    factor = rng.uniform(float(cfg.get("damage_factor_min", 0.92)), float(cfg.get("damage_factor_max", 1.08)))
    power = max(1, _combat_power(user) + int(virtual_bonus))
    return max(1, int(power * _fortune_combat_factor(user, today) * factor))


def _allocate_exact(total: int, weights: dict[str, int]) -> dict[str, int]:
    total = max(0, int(total))
    cleaned = {str(key): max(0, int(weight)) for key, weight in weights.items()}
    result = {key: 0 for key in cleaned}
    denom = sum(cleaned.values())
    if total <= 0 or denom <= 0:
        return result

    ranked: list[tuple[float, str]] = []
    used = 0
    for key, weight in cleaned.items():
        share = total * weight / denom
        base = int(share)
        result[key] = base
        used += base
        ranked.append((share - base, key))

    remain = total - used
    ranked.sort(key=lambda item: (-item[0], item[1]))
    for _frac, key in ranked[:remain]:
        result[key] += 1
    return result


def _apply_world_boss_damage(boss: dict, hits: dict[str, int]) -> dict[str, int]:
    current_hp = max(0, int(boss.get("hp", 0)))
    positive_hits = {str(uid): max(0, int(dmg)) for uid, dmg in hits.items() if int(dmg) > 0}
    if current_hp <= 0 or not positive_hits:
        return {str(uid): 0 for uid in hits}

    total_raw = sum(positive_hits.values())
    actual = positive_hits if total_raw <= current_hp else _allocate_exact(current_hp, positive_hits)

    contributors = boss.setdefault("contributors", {})
    for uid, dmg in actual.items():
        contributors[uid] = int(contributors.get(uid, 0)) + int(dmg)
    boss["hp"] = current_hp - sum(actual.values())

    result = {str(uid): 0 for uid in hits}
    result.update(actual)
    return result


def _world_boss_reward_cfg() -> dict:
    rewards = _world_boss_cfg().get("rewards", {})
    return rewards if isinstance(rewards, dict) else {}


def _world_boss_kill_lines(group: dict, boss: dict) -> list[str]:
    rewards = _world_boss_reward_cfg()
    contributors = {
        str(uid): max(0, int(dmg))
        for uid, dmg in boss.get("contributors", {}).items()
        if int(dmg) > 0
    }
    lines = [_line("world_boss_kill", monster=boss.get("name", "世界BOSS"))]
    if not contributors:
        _rpg_state(group).pop("world_boss", None)
        return lines

    scale_count = max(1, int(boss.get("scale_count", 1)))
    exp_pool = max(0, int(rewards.get("exp_pool_per_scale", 60)) * scale_count)
    points_pool = max(0, int(rewards.get("points_pool_per_scale", 16)) * scale_count)
    exp_fixed = max(0, int(rewards.get("exp_fixed", 20)))
    points_fixed = max(0, int(rewards.get("points_fixed", 5)))
    exp_alloc = _allocate_exact(exp_pool, contributors)
    points_alloc = _allocate_exact(points_pool, contributors)

    ranked = sorted(contributors.items(), key=lambda item: (-item[1], item[0]))
    for uid, damage in ranked:
        user = _ensure_player(group, uid)
        old_level = _level_of(int(user.get("exp", 0)))
        exp_gain = exp_fixed + int(exp_alloc.get(uid, 0))
        points_gain = points_fixed + int(points_alloc.get(uid, 0))
        user["exp"] = int(user.get("exp", 0)) + exp_gain
        user["points"] = int(user.get("points", 0)) + points_gain
        new_level = _level_of(int(user.get("exp", 0)))
        levelup = f"，升级 Lv{old_level}→Lv{new_level}" if new_level > old_level else ""
        name = user.get("display_name") or f"用户{uid}"
        lines.append(
            _line(
                "world_boss_reward",
                name=name,
                damage=damage,
                exp=exp_gain,
                points=points_gain,
                levelup=levelup,
            )
        )

    _rpg_state(group).pop("world_boss", None)
    return lines


def _world_boss_spawn_lines(boss: dict | None) -> list[str]:
    if not boss:
        return []
    return [
        _line("world_boss_spawn", monster=boss.get("name", "世界BOSS")),
        _line("world_boss_status_hp", hp=boss.get("hp", 0), max_hp=boss.get("max_hp", 0), percent=100),
        _line(
            "world_boss_status_scale",
            recent_active=boss.get("recent_active_count", 0),
            scale_count=boss.get("scale_count", 0),
        ),
        _line("world_boss_spawn_scale"),
        _line("world_boss_status_hint"),
    ]


def _world_boss_status_lines(group: dict, today: str) -> list[str]:
    boss = _active_world_boss(group, today)
    if not boss:
        return [_error("boss_none")]

    hp = max(0, int(boss.get("hp", 0)))
    max_hp = max(1, int(boss.get("max_hp", 1)))
    percent = max(0, min(100, round(hp * 100 / max_hp)))
    lines = [
        _line("world_boss_status_head", monster=boss.get("name", "世界BOSS")),
        _line("world_boss_status_hp", hp=hp, max_hp=max_hp, percent=percent),
        _line(
            "world_boss_status_scale",
            recent_active=boss.get("recent_active_count", 0),
            scale_count=boss.get("scale_count", 0),
        ),
    ]

    contributors = {
        str(uid): max(0, int(dmg))
        for uid, dmg in boss.get("contributors", {}).items()
        if int(dmg) > 0
    }
    if not contributors:
        lines.append(_line("world_boss_status_empty"))
    else:
        lines.append(_line("world_boss_status_rank"))
        ranked = sorted(contributors.items(), key=lambda item: (-item[1], item[0]))[:5]
        for idx, (uid, damage) in enumerate(ranked, 1):
            rec = group.get("users", {}).get(uid, {})
            name = rec.get("display_name") if isinstance(rec, dict) else ""
            lines.append(_line("world_boss_status_entry", rank=idx, name=name or f"用户{uid}", damage=damage))
    lines.append(_line("world_boss_status_hint"))
    return lines


def _boss_at_line(key: str, ctx: dict):
    return _render_with_ats(random.choice(_copy(key)), ctx)


world_boss_cmd = on_command("世界BOSS", aliases={"世界 BOSS"}, priority=5, block=True)


@world_boss_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        lines = _world_boss_status_lines(group, today)

    await world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


attack_world_boss_cmd = on_command("攻击世界BOSS", aliases={"攻击世界 BOSS"}, priority=5, block=True)


@attack_world_boss_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, user_id, _display_name(event))

        if user.get("equip_date") != today:
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if user.get("equip_used") and not is_superuser:
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        boss = _active_world_boss(group, today)
        if not boss:
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_none"))

        dealt = _apply_world_boss_damage(boss, {user_id: _boss_damage(user, today)}).get(str(user_id), 0)
        _consume_equip(user)

        head_key = "world_boss_attack_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_attack"
        lines = [
            _boss_at_line(
                head_key,
                {
                    "a": user_id,
                    "monster": boss.get("name", "世界BOSS"),
                    "damage": dealt,
                    "hp": boss.get("hp", 0),
                    "max_hp": boss.get("max_hp", 0),
                },
            )
        ]
        if int(boss.get("hp", 0)) <= 0:
            lines.extend(_world_boss_kill_lines(group, boss))
        _save_data(data)

    msg = lines[0]
    for line in lines[1:]:
        msg = msg + "\n" + line
    await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + msg)


team_world_boss_cmd = on_command(
    "组队世界BOSS",
    aliases={"组队世界 BOSS", "世界BOSS组队", "世界 BOSS 组队"},
    priority=5,
    block=True,
)


@team_world_boss_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    initiator = event.get_user_id()
    is_superuser = initiator == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    target = _first_at_qq(getattr(event, "original_message", None))
    if not target or target == "all":
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_need_target"))
    if target == initiator:
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_self"))
    if target == str(getattr(bot, "self_id", "")):
        await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_bot"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        boss = _active_world_boss(group, today)
        if not boss:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_none"))

        b = _ensure_player(group, initiator, _display_name(event))
        if b.get("equip_date") != today:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if b.get("equip_used") and not is_superuser:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_broken"))

        a = _ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if a.get("equip_date") != today:
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))
        if a.get("equip_used"):
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_broken"))

        bond_level = _bond_level(_get_intimacy(group, initiator, target))["level"]
        success = random.random() < _team_success_rate(bond_level)
        lines: list = []

        if success:
            team_bonus = int(_cfg("forge", {}).get("step", 0)) * int(_cfg("forge", {}).get("max_per_day", 0))
            dealt = _apply_world_boss_damage(
                boss,
                {
                    initiator: _boss_damage(b, today, virtual_bonus=team_bonus),
                    target: _boss_damage(a, today, virtual_bonus=team_bonus),
                },
            )
            _consume_equip(b)
            _consume_equip(a)
            b_name = b.get("display_name") or f"群友{initiator}"
            head_key = "world_boss_team_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_team_attack"
            lines.append(
                _boss_at_line(
                    head_key,
                    {
                        "a": initiator,
                        "b": target,
                        "monster": boss.get("name", "世界BOSS"),
                        "a_name": b_name,
                        "b_name": a_name,
                        "a_damage": dealt.get(str(initiator), 0),
                        "b_damage": dealt.get(str(target), 0),
                        "total_damage": sum(dealt.values()),
                        "hp": boss.get("hp", 0),
                        "max_hp": boss.get("max_hp", 0),
                    },
                )
            )
        else:
            fail_event = _roll_team_fail_flavor()
            if fail_event:
                lines.append(_line(f"world_boss_fail_event_{fail_event}", b_name=a_name))
            lines.append(
                _boss_at_line(
                    "world_boss_team_fail",
                    {
                        "a": initiator,
                        "b_name": a_name,
                        "monster": boss.get("name", "世界BOSS"),
                    },
                )
            )
            dealt = _apply_world_boss_damage(boss, {initiator: _boss_damage(b, today)})
            _consume_equip(b)
            lines.append(
                _boss_at_line(
                    "world_boss_attack_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_attack",
                    {
                        "a": initiator,
                        "monster": boss.get("name", "世界BOSS"),
                        "damage": dealt.get(str(initiator), 0),
                        "hp": boss.get("hp", 0),
                        "max_hp": boss.get("max_hp", 0),
                    },
                )
            )

        if int(boss.get("hp", 0)) <= 0:
            lines.extend(_world_boss_kill_lines(group, boss))
        _save_data(data)

    msg = lines[0]
    for line in lines[1:]:
        msg = msg + "\n" + line
    await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + msg)
