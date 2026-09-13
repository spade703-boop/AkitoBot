"""World boss state, generation, and damage logic."""

from __future__ import annotations

from datetime import date, timedelta
import math
import random
from typing import Any, cast

from ....core import game_store as _game_store
from ....core.types import GroupRecord
from ... import gift as _gift
from .. import config as _config
from .. import utils as _utils
from ..player import player as _player
from ..reporting import analytics as _analytics
from ..state import _rpg_state
from ..types import BossParticipantRecord, RpgUserRecord, WorldBossRecord

_cfg = _config._cfg
_line = _config._line
_add_intimacy = _game_store._add_intimacy
_pair_key = _game_store._pair_key
_bond_level = _gift._bond_level
_ensure_player = _player._ensure_player
_equip_power = _player._equip_power
_level_of = _player._level_of
record_world_boss_spawn = _analytics.record_world_boss_spawn
_fortune_combat_factor = _utils._fortune_combat_factor
_team_power_bonus = _utils._team_power_bonus


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


def _parse_iso_day(text: object) -> date | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _active_world_boss(group: GroupRecord, today: str) -> WorldBossRecord | None:
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


def _recent_active_user_ids(group: GroupRecord, today: str) -> list[str]:
    cfg = _world_boss_cfg()
    window = max(1, int(cfg.get("activity_window_days", 7)))
    today_day = _parse_iso_day(today)
    if today_day is None:
        return []
    cutoff = today_day - timedelta(days=window - 1)
    user_ids: list[str] = []
    users = cast(dict[str, RpgUserRecord], group.get("users", {}))
    for uid, rec in users.items():
        if not isinstance(rec, dict):
            continue
        last_days = [_parse_iso_day(rec.get("last_sign_in")), _parse_iso_day(rec.get("signin_last_date"))]
        if any(day is not None and cutoff <= day <= today_day for day in last_days):
            user_ids.append(str(uid))
    return user_ids


def _expected_daily_power(user: RpgUserRecord) -> int:
    ecfg = _cfg("equip", {})
    level = _level_of(int(user.get("exp", 0)))
    base = int(ecfg.get("base", 10))
    per_level = int(ecfg.get("per_level", 5))
    expected_roll = int(ecfg.get("var", 6)) // 2
    return max(1, base + level * per_level + expected_roll)


def _world_boss_snapshot(group: GroupRecord, today: str) -> dict[str, Any]:
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


def _snapshot_averages(group: GroupRecord, user_ids: list[str]) -> tuple[int, int]:
    levels: list[int] = []
    powers: list[int] = []
    for uid in user_ids:
        rec = cast(RpgUserRecord, group.get("users", {}).get(uid, {}))
        if not isinstance(rec, dict):
            continue
        levels.append(_level_of(int(rec.get("exp", 0))))
        powers.append(_expected_daily_power(rec))
    avg_level = max(1, round(sum(levels) / len(levels))) if levels else 1
    avg_power = max(1, round(sum(powers) / len(powers))) if powers else _expected_daily_power({})
    return avg_level, avg_power


def _force_world_boss_snapshot(group: GroupRecord, today: str) -> dict[str, Any]:
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


def _spawn_world_boss(
    group: GroupRecord,
    today: str,
    user_id: str,
    rng=random,
    snapshot: dict[str, Any] | None = None,
    *,
    forced: bool = False,
) -> WorldBossRecord | None:
    snap = snapshot or _world_boss_snapshot(group, today)
    if not snap.get("spawnable"):
        return None
    boss_names = _world_boss_cfg().get("boss_names", [])
    if not isinstance(boss_names, list) or not boss_names:
        boss_names = ["世界BOSS"]
    boss: WorldBossRecord = {
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
    boss["metric_id"] = record_world_boss_spawn(group, today, forced=forced, boss=boss)
    return boss


def _maybe_spawn_world_boss(
    group: GroupRecord,
    today: str,
    user_id: str,
    rng=random,
) -> WorldBossRecord | None:
    if _active_world_boss(group, today):
        return None
    snapshot = _world_boss_snapshot(group, today)
    if not snapshot.get("spawnable"):
        return None
    if float(rng.random()) >= float(_world_boss_cfg().get("spawn_chance", 0.001)):
        return None
    return _spawn_world_boss(group, today, user_id, rng=rng, snapshot=snapshot)


def _maybe_spawn_world_boss_lines(group: GroupRecord, today: str, user_id: str, rng=random) -> list[str]:
    boss = _maybe_spawn_world_boss(group, today, user_id, rng=rng)
    return _world_boss_spawn_lines(boss) if boss else []


def _world_boss_spawn_lines(boss: WorldBossRecord | None) -> list[str]:
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


def _boss_participants(boss: WorldBossRecord) -> dict[str, BossParticipantRecord]:
    participants = boss.get("participants")
    if not isinstance(participants, dict):
        participants = {}
        boss["participants"] = participants
    return participants


def _ensure_boss_participant(
    boss: WorldBossRecord,
    user_id: str,
    user: RpgUserRecord,
    today: str,
    *,
    rng=random,
) -> BossParticipantRecord | None:
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


def _boss_damage(
    equip_rec: BossParticipantRecord,
    fortune_user: RpgUserRecord,
    today: str,
    *,
    rng=random,
) -> int:
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


def _apply_world_boss_damage(boss: WorldBossRecord, hits: dict[str, int]) -> dict[str, int]:
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
        boss.pop("last_hit_uids", None)
    else:
        boss.pop("last_hit", None)
        boss.pop("last_hit_uids", None)

    result = {str(uid): 0 for uid in hits}
    result.update(actual)
    return result


__all__ = [
    "_active_world_boss",
    "_apply_team_bonus",
    "_apply_world_boss_damage",
    "_boss_damage",
    "_boss_participants",
    "_ensure_boss_participant",
    "_expected_daily_power",
    "_force_world_boss_snapshot",
    "_maybe_spawn_world_boss",
    "_maybe_spawn_world_boss_lines",
    "_recent_active_user_ids",
    "_spawn_world_boss",
    "_world_boss_cfg",
    "_world_boss_snapshot",
    "_world_boss_spawn_lines",
]
