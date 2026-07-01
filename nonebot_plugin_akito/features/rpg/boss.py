"""World boss flow for the lightweight group RPG."""

from __future__ import annotations

from datetime import date, timedelta
import math
import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger
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
from ..bond_pages import build_world_boss_rank_page_data
from ..bond_render import render_bond_page
from ..gift import _bond_level
from .config import _cfg, _copy, _error, _line
from .fortune import _fortune_by_key
from .player import _consume_equip, _ensure_player, _equip_power, _level_of, _resolve_group


def _world_boss_cfg() -> dict:
    cfg = _cfg("world_boss", {})
    return cfg if isinstance(cfg, dict) else {}


def _soft_scale_count(active_count: int, *, base_cap: int, extra_rate: float, max_cap: int) -> int:
    active_count = max(0, int(active_count))
    base_cap = max(1, int(base_cap))
    if active_count <= base_cap:
        return active_count
    extra_rate = max(0.0, float(extra_rate))
    max_cap = max(base_cap, int(max_cap))
    return min(max_cap, base_cap + math.ceil((active_count - base_cap) * extra_rate))


def _team_success_rate(bond_level: int) -> float:
    tcfg = _cfg("team", {})
    rate = float(tcfg.get("base_success", 0.35)) + (int(bond_level) - 1) * float(tcfg.get("per_level", 0.12))
    return max(float(tcfg.get("min_success", 0.10)), min(float(tcfg.get("max_success", 0.95)), rate))


def _team_power_bonus() -> float:
    return max(0.0, float(_cfg("team", {}).get("power_bonus", 0.0)))


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


def _active_world_boss(group: dict, today: str) -> dict | None:
    state = _rpg_state(group)
    boss = state.get("world_boss")
    if not isinstance(boss, dict):
        state.pop("world_boss", None)
        return None
    if boss.get("date") != today:
        return None
    contributors = boss.get("contributors")
    if not isinstance(contributors, dict):
        contributors = {}
        boss["contributors"] = contributors
    participants = boss.get("participants")
    if not isinstance(participants, dict):
        participants = {}
        boss["participants"] = participants
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
    base_cap = max(min_users, int(cfg.get("activity_scale_cap", 12)))
    if active_count < min_users:
        return {
            "spawnable": False,
            "recent_active_count": active_count,
            "scale_count": min(active_count, base_cap),
            "reward_scale_count": min(active_count, base_cap),
            "avg_level": 1,
            "avg_power": 1,
            "max_hp": 1,
        }

    avg_level, avg_power = _snapshot_averages(group, active_ids)
    scale_count = _soft_scale_count(
        active_count,
        base_cap=base_cap,
        extra_rate=float(cfg.get("hp_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("hp_scale_max", base_cap)),
    )
    reward_scale_count = _soft_scale_count(
        active_count,
        base_cap=base_cap,
        extra_rate=float(cfg.get("reward_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("reward_scale_max", base_cap)),
    )
    hp_factor = float(cfg.get("hp_factor", 1.0))
    max_hp = max(1, round(avg_power * scale_count * hp_factor))
    return {
        "spawnable": True,
        "recent_active_count": active_count,
        "scale_count": scale_count,
        "reward_scale_count": reward_scale_count,
        "avg_level": avg_level,
        "avg_power": avg_power,
        "max_hp": max_hp,
    }


def _snapshot_averages(group: dict, user_ids: list[str]) -> tuple[int, int]:
    levels: list[int] = []
    powers: list[int] = []
    for uid in user_ids:
        rec = group.get("users", {}).get(uid, {})
        if not isinstance(rec, dict):
            continue
        levels.append(_level_of(int(rec.get("exp", 0))))
        powers.append(_expected_daily_power(rec))
    avg_level = max(1, round(sum(levels) / len(levels))) if levels else 1
    avg_power = max(1, round(sum(powers) / len(powers))) if powers else _expected_daily_power({})
    return avg_level, avg_power


def _force_world_boss_snapshot(group: dict, today: str) -> dict:
    cfg = _world_boss_cfg()
    active_ids = _recent_active_user_ids(group, today)
    active_count = len(active_ids)
    base_cap = max(1, int(cfg.get("activity_scale_cap", 12)))
    seeded_count = max(active_count, 1)
    avg_level, avg_power = _snapshot_averages(group, active_ids)
    scale_count = _soft_scale_count(
        seeded_count,
        base_cap=base_cap,
        extra_rate=float(cfg.get("hp_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("hp_scale_max", base_cap)),
    )
    reward_scale_count = _soft_scale_count(
        seeded_count,
        base_cap=base_cap,
        extra_rate=float(cfg.get("reward_scale_extra_rate", 0.0)),
        max_cap=int(cfg.get("reward_scale_max", base_cap)),
    )
    hp_factor = float(cfg.get("hp_factor", 1.0))
    max_hp = max(1, round(avg_power * scale_count * hp_factor))
    return {
        "spawnable": True,
        "recent_active_count": active_count,
        "scale_count": scale_count,
        "reward_scale_count": reward_scale_count,
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
        boss_names = ["世界BOSS"]
    boss = {
        "date": today,
        "name": str(rng.choice(boss_names)),
        "max_hp": int(snap["max_hp"]),
        "hp": int(snap["max_hp"]),
        "recent_active_count": int(snap["recent_active_count"]),
        "scale_count": int(snap["scale_count"]),
        "reward_scale_count": int(snap.get("reward_scale_count", snap["scale_count"])),
        "avg_level": int(snap["avg_level"]),
        "avg_power": int(snap["avg_power"]),
        "contributors": {},
        "participants": {},
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


def _boss_participants(boss: dict) -> dict:
    participants = boss.get("participants")
    if not isinstance(participants, dict):
        participants = {}
        boss["participants"] = participants
    return participants


def _ensure_boss_participant(boss: dict, user_id: str, user: dict, today: str, *, rng=random) -> dict | None:
    if user.get("equip_date") != today:
        return None

    participants = _boss_participants(boss)
    rec = participants.get(str(user_id))
    if isinstance(rec, dict):
        rec.setdefault("equip_date", today)
        rec.setdefault("equip_level", int(user.get("equip_level", _level_of(int(user.get("exp", 0))))))
        rec.setdefault("equip_roll", 0)
        rec.setdefault("equip_forge", 0)
        rec.setdefault("equip_used", False)
        return rec

    ecfg = _cfg("equip", {})
    rec = {
        "equip_date": today,
        "equip_level": int(user.get("equip_level", _level_of(int(user.get("exp", 0))))),
        "equip_roll": rng.randint(0, int(ecfg.get("var", 6))),
        "equip_forge": 0,
        "equip_used": False,
    }
    participants[str(user_id)] = rec
    return rec


def _boss_damage(equip_rec: dict, fortune_user: dict, today: str, *, rng=random) -> int:
    cfg = _world_boss_cfg()
    factor = rng.uniform(float(cfg.get("damage_factor_min", 0.92)), float(cfg.get("damage_factor_max", 1.08)))
    power = max(1, _equip_power(equip_rec))
    return max(1, int(power * _fortune_combat_factor(fortune_user, today) * factor))


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


def _apply_team_bonus(hits: dict[str, int]) -> tuple[dict[str, int], int]:
    base_hits = {str(uid): max(0, int(dmg)) for uid, dmg in hits.items() if int(dmg) > 0}
    total = sum(base_hits.values())
    bonus_total = int(total * _team_power_bonus())
    if bonus_total <= 0 or total <= 0:
        return {str(uid): max(0, int(dmg)) for uid, dmg in hits.items()}, 0
    bonus = _allocate_exact(bonus_total, base_hits)
    merged = {str(uid): max(0, int(dmg)) for uid, dmg in hits.items()}
    for uid, extra in bonus.items():
        merged[uid] = merged.get(uid, 0) + extra
    return merged, bonus_total


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

    remaining = current_hp
    last_hit_uid = None
    for uid in hits:
        uid = str(uid)
        dealt = int(actual.get(uid, 0))
        if dealt <= 0:
            continue
        if dealt >= remaining:
            last_hit_uid = uid
            break
        remaining -= dealt
    if int(boss.get("hp", 0)) <= 0 and last_hit_uid is not None:
        boss["last_hit"] = last_hit_uid
    else:
        boss.pop("last_hit", None)

    result = {str(uid): 0 for uid in hits}
    result.update(actual)
    return result


def _world_boss_reward_cfg() -> dict:
    rewards = _world_boss_cfg().get("rewards", {})
    return rewards if isinstance(rewards, dict) else {}


def _world_boss_contributors(boss: dict) -> dict[str, int]:
    return {
        str(uid): max(0, int(dmg))
        for uid, dmg in boss.get("contributors", {}).items()
        if int(dmg) > 0
    }


def _world_boss_reward_values(boss: dict, *, reward_ratio: float = 1.0) -> dict[str, int]:
    rewards = _world_boss_reward_cfg()
    ratio = max(0.0, float(reward_ratio))
    reward_scale_count = max(1, int(boss.get("reward_scale_count", boss.get("scale_count", 1))))
    return {
        "exp_pool": max(0, round(int(rewards.get("exp_pool_per_scale", 60)) * reward_scale_count * ratio)),
        "points_pool": max(0, round(int(rewards.get("points_pool_per_scale", 8)) * reward_scale_count * ratio)),
        "exp_fixed": max(0, round(int(rewards.get("exp_fixed", 12)) * ratio)),
        "points_fixed": max(0, round(int(rewards.get("points_fixed", 2)) * ratio)),
    }


def _world_boss_reward_results(
    group: dict,
    contributors: dict[str, int],
    *,
    exp_pool: int,
    points_pool: int,
    exp_fixed: int,
    points_fixed: int,
    last_hit_uid: str | None = None,
) -> list[dict]:
    rows: list[dict] = []
    exp_alloc = _allocate_exact(exp_pool, contributors)
    points_alloc = _allocate_exact(points_pool, contributors)
    reward_cfg = _world_boss_reward_cfg()
    total_damage = max(1, sum(int(dmg) for dmg in contributors.values()))
    last_hit_uid = str(last_hit_uid) if last_hit_uid is not None else None

    ranked = sorted(contributors.items(), key=lambda item: (-item[1], item[0]))
    for rank, (uid, damage) in enumerate(ranked, 1):
        user = _ensure_player(group, uid)
        old_level = _level_of(int(user.get("exp", 0)))
        is_last_hit = last_hit_uid == uid
        exp_bonus = int(reward_cfg.get("last_hit_exp_bonus", 0)) if is_last_hit else 0
        points_bonus = int(reward_cfg.get("last_hit_points_bonus", 0)) if is_last_hit else 0
        exp_gain = int(exp_fixed) + int(exp_alloc.get(uid, 0)) + exp_bonus
        points_gain = int(points_fixed) + int(points_alloc.get(uid, 0)) + points_bonus
        user["exp"] = int(user.get("exp", 0)) + exp_gain
        user["points"] = int(user.get("points", 0)) + points_gain
        new_level = _level_of(int(user.get("exp", 0)))
        name = user.get("display_name") or f"用户{uid}"
        rows.append(
            {
                "rank": rank,
                "uid": uid,
                "name": name,
                "damage": int(damage),
                "damage_pct": round(int(damage) * 100 / total_damage),
                "exp": exp_gain,
                "points": points_gain,
                "exp_bonus": exp_bonus,
                "points_bonus": points_bonus,
                "old_level": old_level,
                "new_level": new_level,
                "levelup": new_level > old_level,
                "levelup_text": f"Lv{old_level}→Lv{new_level}" if new_level > old_level else "",
                "last_hit": is_last_hit,
            }
        )
    return rows


def _world_boss_reward_lines(rows: list[dict]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        extra = ""
        extra_parts: list[str] = []
        if int(row.get("exp_bonus", 0)) > 0:
            extra_parts.append(f"经验 +{int(row['exp_bonus'])}")
        if int(row.get("points_bonus", 0)) > 0:
            extra_parts.append(f"积分 +{int(row['points_bonus'])}")
        if extra_parts:
            extra = f"（尾刀奖励：{'、'.join(extra_parts)}）"
        lines.append(
            _line(
                "world_boss_reward",
                name=row.get("name", ""),
                damage=int(row.get("damage", 0)),
                exp=int(row.get("exp", 0)),
                points=int(row.get("points", 0)),
                levelup=(f"，升级 {row['levelup_text']}" if row.get("levelup_text") else ""),
            )
            + extra
        )
    return lines


def _world_boss_kill_settlement(group: dict, boss: dict, *, last_hit_uid: str | None = None) -> dict:
    contributors = _world_boss_contributors(boss)
    result = {
        "monster": str(boss.get("name", "世界BOSS")),
        "rows": [],
        "last_hit_uid": None,
        "last_hit_name": "",
        "last_hit_reward": {"exp": 0, "points": 0},
        "lines": [],
    }
    if not contributors:
        result["lines"] = [_line("world_boss_kill")]
        _rpg_state(group).pop("world_boss", None)
        return result

    normalized_last_hit = str(last_hit_uid) if last_hit_uid is not None else None
    if normalized_last_hit not in contributors:
        normalized_last_hit = None

    reward_values = _world_boss_reward_values(boss)
    rows = _world_boss_reward_results(
        group,
        contributors,
        exp_pool=reward_values["exp_pool"],
        points_pool=reward_values["points_pool"],
        exp_fixed=reward_values["exp_fixed"],
        points_fixed=reward_values["points_fixed"],
        last_hit_uid=normalized_last_hit,
    )
    result["rows"] = rows
    result["last_hit_uid"] = normalized_last_hit
    for row in rows:
        if row.get("uid") != normalized_last_hit:
            continue
        result["last_hit_name"] = str(row.get("name", ""))
        result["last_hit_reward"] = {
            "exp": int(row.get("exp_bonus", 0)),
            "points": int(row.get("points_bonus", 0)),
        }
        break
    result["lines"] = [_line("world_boss_kill")]
    if result["last_hit_name"]:
        result["lines"].append(_line("world_boss_last_hit", name=result["last_hit_name"]))

    _rpg_state(group).pop("world_boss", None)
    return result


def _world_boss_kill_lines(group: dict, boss: dict) -> list[str]:
    settlement = _world_boss_kill_settlement(group, boss, last_hit_uid=boss.get("last_hit"))
    if not settlement["rows"]:
        return settlement["lines"]
    return [*settlement["lines"], *_world_boss_reward_lines(settlement["rows"])]


def _world_boss_unfinished_lines(group: dict, boss: dict) -> list[str]:
    contributors = _world_boss_contributors(boss)
    if not contributors:
        return []

    reward_cfg = _world_boss_reward_cfg()
    total_damage = sum(contributors.values())
    max_hp = max(1, int(boss.get("max_hp", 1)))
    progress = max(0.0, min(1.0, total_damage / max_hp))
    reward_ratio = max(0.0, min(1.0, progress * float(reward_cfg.get("unfinished_reward_mult", 0.5))))
    reward_values = _world_boss_reward_values(boss, reward_ratio=reward_ratio)

    lines = [
        _line(
            "world_boss_expired",
            monster=boss.get("name", "世界BOSS"),
            progress=round(progress * 100),
            reward_percent=round(reward_ratio * 100),
        )
    ]
    rows = _world_boss_reward_results(
        group,
        contributors,
        exp_pool=reward_values["exp_pool"],
        points_pool=reward_values["points_pool"],
        exp_fixed=reward_values["exp_fixed"],
        points_fixed=reward_values["points_fixed"],
    )
    lines.extend(_world_boss_reward_lines(rows))
    return lines


def _cleanup_stale_world_boss(group: dict, today: str) -> tuple[list[str], bool]:
    state = _rpg_state(group)
    boss = state.get("world_boss")
    if not isinstance(boss, dict):
        changed = "world_boss" in state
        state.pop("world_boss", None)
        return [], changed
    if boss.get("date") == today:
        return [], False
    if int(boss.get("hp", 0)) <= 0:
        return _world_boss_kill_lines(group, boss), True
    lines = _world_boss_unfinished_lines(group, boss)
    state.pop("world_boss", None)
    return lines, True


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


async def _render_world_boss_settlement_image(settlement: dict) -> bytes | None:
    rows = settlement.get("rows", [])
    if not isinstance(rows, list) or not rows:
        return None
    try:
        page_data = build_world_boss_rank_page_data(
            settlement.get("monster", "世界BOSS"),
            rows,
        )
        return await render_bond_page("world_boss_rank.html", page_data, viewport_width=760)
    except Exception as e:
        logger.warning(f"world boss settlement render failed ({e}), falling back to text")
        return None


def _merge_lines_with_optional_image(lines: list, image_bytes: bytes | None = None):
    msg = lines[0]
    for line in lines[1:]:
        msg = msg + "\n" + line
    if image_bytes is not None:
        msg = msg + "\n" + MessageSegment.image(image_bytes)
    return msg


_TEST_WORLD_BOSS_ROWS: list[dict] = [
    {"rank": 1, "uid": "10001", "name": "测试冒险者01", "damage": 1280, "damage_pct": 29, "exp": 126, "points": 15, "exp_bonus": 0, "points_bonus": 0, "old_level": 9, "new_level": 10, "levelup": True, "levelup_text": "Lv9→Lv10", "last_hit": False},
    {"rank": 2, "uid": "10002", "name": "测试冒险者02", "damage": 1186, "damage_pct": 27, "exp": 118, "points": 14, "exp_bonus": 0, "points_bonus": 0, "old_level": 8, "new_level": 8, "levelup": False, "levelup_text": "", "last_hit": False},
    {"rank": 3, "uid": "10003", "name": "测试冒险者03", "damage": 1014, "damage_pct": 23, "exp": 111, "points": 15, "exp_bonus": 8, "points_bonus": 2, "old_level": 7, "new_level": 8, "levelup": True, "levelup_text": "Lv7→Lv8", "last_hit": True},
    {"rank": 4, "uid": "10004", "name": "测试冒险者04", "damage": 462, "damage_pct": 10, "exp": 57, "points": 7, "exp_bonus": 0, "points_bonus": 0, "old_level": 6, "new_level": 6, "levelup": False, "levelup_text": "", "last_hit": False},
    {"rank": 5, "uid": "10005", "name": "测试冒险者05", "damage": 258, "damage_pct": 6, "exp": 39, "points": 5, "exp_bonus": 0, "points_bonus": 0, "old_level": 5, "new_level": 5, "levelup": False, "levelup_text": "", "last_hit": False},
    {"rank": 6, "uid": "10006", "name": "测试冒险者06", "damage": 181, "damage_pct": 4, "exp": 31, "points": 4, "exp_bonus": 0, "points_bonus": 0, "old_level": 4, "new_level": 4, "levelup": False, "levelup_text": "", "last_hit": False},
]


world_boss_cmd = on_command("世界BOSS", priority=5, block=True)


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
        settlement_lines, changed = _cleanup_stale_world_boss(group, today)
        if changed:
            _save_data(data)
        lines = [*settlement_lines, *_world_boss_status_lines(group, today)]

    await world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


force_world_boss_cmd = on_command("强制开启世界BOSS", priority=5, block=True)


@force_world_boss_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await force_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, _changed = _cleanup_stale_world_boss(group, today)
        current = _active_world_boss(group, today)
        if current:
            lines = [*settlement_lines, _line("world_boss_force_exists"), *_world_boss_status_lines(group, today)]
        else:
            spawned = _spawn_world_boss(
                group,
                today,
                event.get_user_id(),
                rng=random,
                snapshot=_force_world_boss_snapshot(group, today),
            )
            _save_data(data)
            lines = [*settlement_lines, _line("world_boss_force_opened"), *_world_boss_spawn_lines(spawned)]
    await force_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


test_world_rank_cmd = on_command("test世界排行", aliases={"测试世界排行"}, priority=5, block=True)


@test_world_rank_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if args and args.extract_plain_text().strip():
        return

    image_bytes = await _render_world_boss_settlement_image(
        {
            "monster": "赤鳞灾龙",
            "rows": [row.copy() for row in _TEST_WORLD_BOSS_ROWS],
            "last_hit_uid": "10003",
            "last_hit_name": "测试冒险者03",
            "last_hit_reward": {"exp": 8, "points": 2},
            "lines": [
                _line("world_boss_kill"),
                _line("world_boss_last_hit", name="测试冒险者03"),
            ],
        }
    )
    if image_bytes is None:
        lines = [
            "测试世界排行图渲染失败，已切回文字预览。",
            *_world_boss_reward_lines([row.copy() for row in _TEST_WORLD_BOSS_ROWS]),
        ]
        await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

    msg = _merge_lines_with_optional_image(
        [
            _line("world_boss_kill"),
            _line("world_boss_last_hit", name="测试冒险者03"),
        ],
        image_bytes,
    )
    await test_world_rank_cmd.finish(MessageSegment.reply(event.message_id) + msg)


attack_world_boss_cmd = on_command("攻击世界BOSS", priority=5, block=True)


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
    settlement = None
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, changed = _cleanup_stale_world_boss(group, today)
        boss = _active_world_boss(group, today)
        if not boss:
            if changed:
                _save_data(data)
            lines = [*settlement_lines, _error("boss_none")]
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

        user = _ensure_player(group, user_id, _display_name(event))
        if user.get("equip_date") != today:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))

        participant = _ensure_boss_participant(boss, user_id, user, today, rng=random)
        if participant is None:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if participant.get("equip_used") and not is_superuser:
            if changed:
                _save_data(data)
            await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_already_attacked"))

        dealt = _apply_world_boss_damage(
            boss,
            {user_id: _boss_damage(participant, user, today, rng=random)},
        ).get(str(user_id), 0)
        _consume_equip(participant)

        head_key = "world_boss_attack_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_attack"
        if int(boss.get("hp", 0)) <= 0:
            settlement = _world_boss_kill_settlement(group, boss, last_hit_uid=boss.get("last_hit"))
            lines = list(settlement["lines"])
        else:
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
        _save_data(data)

    settlement_image = await _render_world_boss_settlement_image(settlement) if settlement else None
    if settlement and settlement_image is None:
        lines.extend(_world_boss_reward_lines(settlement["rows"]))
    msg = _merge_lines_with_optional_image(lines, settlement_image)
    await attack_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + msg)


team_world_boss_cmd = on_command("组队世界BOSS", priority=5, block=True)


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
        return
    if target == initiator:
        return
    if target == str(getattr(bot, "self_id", "")):
        return

    today = _today_str()
    settlement = None
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, changed = _cleanup_stale_world_boss(group, today)
        boss = _active_world_boss(group, today)
        if not boss:
            if changed:
                _save_data(data)
            lines = [*settlement_lines, _error("boss_none")]
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))

        b = _ensure_player(group, initiator, _display_name(event))
        if b.get("equip_date") != today:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))

        a = _ensure_player(group, target)
        a_name = a.get("display_name") or f"群友{target}"
        if a.get("equip_date") != today:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))

        b_participant = _ensure_boss_participant(boss, initiator, b, today, rng=random)
        a_participant = _ensure_boss_participant(boss, target, a, today, rng=random)
        if b_participant is None:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_equip"))
        if a_participant is None:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_no_signin"))
        if b_participant.get("equip_used") and not is_superuser:
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("boss_already_attacked"))
        if a_participant.get("equip_used"):
            if changed:
                _save_data(data)
            await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + _error("team_target_broken"))

        bond_level = _bond_level(_get_intimacy(group, initiator, target))["level"]
        success = random.random() < _team_success_rate(bond_level)
        lines: list = []

        if success:
            raw_hits = {
                initiator: _boss_damage(b_participant, b, today, rng=random),
                target: _boss_damage(a_participant, a, today, rng=random),
            }
            team_hits, bonus_total = _apply_team_bonus(raw_hits)
            dealt = _apply_world_boss_damage(boss, team_hits)
            _consume_equip(b_participant)
            _consume_equip(a_participant)
            b_name = b.get("display_name") or f"群友{initiator}"
            last_hit_uid = str(boss.get("last_hit", ""))
            last_hit_name = b_name if last_hit_uid == str(initiator) else a_name
            head_key = "world_boss_team_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_team_attack"
            if int(boss.get("hp", 0)) <= 0:
                lines = []
            else:
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
                            "last_hit_name": last_hit_name,
                        },
                    )
                )
            if bonus_total > 0 and int(boss.get("hp", 0)) > 0:
                lines.append(_line("world_boss_team_bonus", bonus_total=bonus_total))
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
            dealt = _apply_world_boss_damage(
                boss,
                {initiator: _boss_damage(b_participant, b, today, rng=random)},
            )
            _consume_equip(b_participant)
            attack_line = _boss_at_line(
                "world_boss_attack_kill" if int(boss.get("hp", 0)) <= 0 else "world_boss_attack",
                {
                    "a": initiator,
                    "monster": boss.get("name", "世界BOSS"),
                    "damage": dealt.get(str(initiator), 0),
                    "hp": boss.get("hp", 0),
                    "max_hp": boss.get("max_hp", 0),
                },
            )
            if int(boss.get("hp", 0)) <= 0:
                lines = [attack_line]
            else:
                lines.append(attack_line)

        if int(boss.get("hp", 0)) <= 0:
            settlement = _world_boss_kill_settlement(group, boss, last_hit_uid=boss.get("last_hit"))
            lines = list(settlement["lines"])
        _save_data(data)

    settlement_image = await _render_world_boss_settlement_image(settlement) if settlement else None
    if settlement and settlement_image is None:
        lines.extend(_world_boss_reward_lines(settlement["rows"]))
    msg = _merge_lines_with_optional_image(lines, settlement_image)
    await team_world_boss_cmd.finish(MessageSegment.reply(event.message_id) + msg)
