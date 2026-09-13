"""World boss reward, settlement, and cross-day cleanup logic."""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from nonebot.log import logger

from .... import core as _core
from ....core import game_store as _game_store
from ....core.game_store import _add_intimacy, _pair_key
from ....core.types import GroupRecord
from ... import gift as _gift
from .. import config as _config
from .. import utils as _utils
from ..player import player as _player
from ..reporting import analytics as _analytics
from ..reporting.analytics import record_world_boss_settlement
from ..state import _rpg_state
from ..types import RpgUserRecord, WorldBossRecord
from . import logic as _logic

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    world_boss_cmd: Any
    force_world_boss_cmd: Any
    test_world_rank_cmd: Any
    attack_world_boss_cmd: Any
    team_world_boss_cmd: Any
    _render_world_boss_settlement_image: Callable[[dict], Awaitable[bytes | None]]
    _TEST_WORLD_BOSS_ROWS: list[dict[str, Any]]

LOCK = _game_store.LOCK
SUPERUSER_QQ = _core.SUPERUSER_QQ
is_sleeping = _core.is_sleeping
_load_data = _game_store._load_data
_save_data = _game_store._save_data
_get_group = _game_store._get_group
_display_name = _game_store._display_name
_first_at_qq = _game_store._first_at_qq
_render_with_ats = _game_store._render_with_ats
_today_str = _game_store._today_str
_get_intimacy = _game_store._get_intimacy
_cfg = _config._cfg
_copy = _config._copy
_error = _config._error
_line = _config._line
_bond_level = _gift._bond_level
_ensure_player = _player._ensure_player
_equip_power = _player._equip_power
_level_of = _player._level_of
_resolve_group = _player._resolve_group
_consume_equip = _player._consume_equip
record_world_boss_attack = _analytics.record_world_boss_attack
_fortune_combat_factor = _utils._fortune_combat_factor
_roll_fail_flavor = _utils._roll_fail_flavor
_team_power_bonus = _utils._team_power_bonus
_team_success_rate = _utils._team_success_rate
_logger = logger

_world_boss_cfg = _logic._world_boss_cfg
_soft_scale_count = _logic._soft_scale_count
_parse_iso_day = _logic._parse_iso_day
_active_world_boss = _logic._active_world_boss
_recent_active_user_ids = _logic._recent_active_user_ids
_expected_daily_power = _logic._expected_daily_power
_world_boss_snapshot = _logic._world_boss_snapshot
_snapshot_averages = _logic._snapshot_averages
_force_world_boss_snapshot = _logic._force_world_boss_snapshot
_spawn_world_boss = _logic._spawn_world_boss
_maybe_spawn_world_boss = _logic._maybe_spawn_world_boss
_maybe_spawn_world_boss_lines = _logic._maybe_spawn_world_boss_lines
_boss_participants = _logic._boss_participants
_ensure_boss_participant = _logic._ensure_boss_participant
_boss_damage = _logic._boss_damage
_allocate_exact = _logic._allocate_exact
_apply_team_bonus = _logic._apply_team_bonus
_apply_world_boss_damage = _logic._apply_world_boss_damage
_world_boss_spawn_lines = _logic._world_boss_spawn_lines


def _world_boss_reward_cfg() -> dict:
    rewards = _world_boss_cfg().get("rewards", {})
    return rewards if isinstance(rewards, dict) else {}


def _world_boss_team_bond_cfg() -> dict:
    cfg = _world_boss_cfg().get("team_bond", {})
    return cfg if isinstance(cfg, dict) else {}


def _world_boss_special_drop_cfg() -> dict:
    cfg = _world_boss_cfg().get("special_drop", {})
    return cfg if isinstance(cfg, dict) else {}


def _world_boss_contributors(boss: WorldBossRecord) -> dict[str, int]:
    return {str(uid): max(0, int(dmg)) for uid, dmg in boss.get("contributors", {}).items() if int(dmg) > 0}


def _world_boss_bond_gains(boss: WorldBossRecord) -> dict[str, int]:
    gains = boss.get("bond_gains")
    if not isinstance(gains, dict):
        gains = {}
        boss["bond_gains"] = gains
    return gains


def _world_boss_team_bond_daily_pairs(group: GroupRecord, today: str) -> dict[str, int]:
    rpg = _rpg_state(group)
    daily = rpg.get("world_boss_team_bond_daily")
    if not isinstance(daily, dict) or daily.get("date") != today:
        daily = {"date": today, "pairs": {}}
        rpg["world_boss_team_bond_daily"] = daily
    pairs = daily.get("pairs")
    if not isinstance(pairs, dict):
        pairs = {}
        daily["pairs"] = pairs
    return pairs


def _grant_world_boss_team_bond(
    group: GroupRecord,
    boss: WorldBossRecord,
    uid1: str,
    uid2: str,
    today: str,
    *,
    kill: bool,
    negative: bool,
) -> int:
    cfg = _world_boss_team_bond_cfg()
    limit = max(0, int(cfg.get("daily_limit", 1)))
    if limit <= 0:
        return 0

    pairs = _world_boss_team_bond_daily_pairs(group, today)
    key = _pair_key(uid1, uid2)
    if int(pairs.get(key, 0)) >= limit:
        return 0

    gain = max(0, int(cfg.get("base", 1)))
    if kill:
        gain += max(0, int(cfg.get("kill_bonus", 0)))
    if negative:
        gain += max(0, int(cfg.get("negative_bonus", 0)))
    if gain <= 0:
        return 0

    _add_intimacy(group, uid1, uid2, gain)
    pairs[key] = int(pairs.get(key, 0)) + 1

    bond_gains = _world_boss_bond_gains(boss)
    bond_gains[str(uid1)] = int(bond_gains.get(str(uid1), 0)) + gain
    bond_gains[str(uid2)] = int(bond_gains.get(str(uid2), 0)) + gain
    return gain


def _world_boss_reward_values(boss: WorldBossRecord, *, reward_ratio: float = 1.0) -> dict[str, int]:
    rewards = _world_boss_reward_cfg()
    ratio = max(0.0, float(reward_ratio))
    reward_scale_count = max(1, int(boss.get("reward_scale_count", boss.get("scale_count", 1))))
    return {
        "exp_pool": max(0, round(int(rewards.get("exp_pool_per_scale", 60)) * reward_scale_count * ratio)),
        "points_pool": max(0, round(int(rewards.get("points_pool_per_scale", 8)) * reward_scale_count * ratio)),
        "exp_fixed": max(0, round(int(rewards.get("exp_fixed", 12)) * ratio)),
        "points_fixed": max(0, round(int(rewards.get("points_fixed", 2)) * ratio)),
    }


def _world_boss_special_drop_name(boss: WorldBossRecord) -> str:
    cfg = _world_boss_special_drop_cfg()
    items = cfg.get("items", {})
    if not isinstance(items, dict):
        return ""
    return str(items.get(str(boss.get("name", "")), ""))


def _roll_world_boss_special_drop(user: RpgUserRecord, boss: WorldBossRecord, *, rng=random) -> str:
    item_name = _world_boss_special_drop_name(boss)
    if not item_name:
        return ""

    trophies = user.get("world_boss_trophies")
    if not isinstance(trophies, list):
        trophies = []
        user["world_boss_trophies"] = trophies
    if item_name in trophies:
        return ""

    chance = float(_world_boss_special_drop_cfg().get("chance", 0.03))
    if chance <= 0 or rng.random() >= chance:
        return ""

    trophies.append(item_name)
    return item_name


def _world_boss_reward_results(
    group: GroupRecord,
    contributors: dict[str, int],
    *,
    boss: WorldBossRecord | None = None,
    exp_pool: int,
    points_pool: int,
    exp_fixed: int,
    points_fixed: int,
    last_hit_uids: list[str] | tuple[str, ...] | set[str] | None = None,
    bond_gains: dict[str, int] | None = None,
    allow_special_drop: bool = False,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    exp_alloc = _allocate_exact(exp_pool, contributors)
    points_alloc = _allocate_exact(points_pool, contributors)
    reward_cfg = _world_boss_reward_cfg()
    total_damage = max(1, sum(int(dmg) for dmg in contributors.values()))
    last_hit_uid_set = {str(uid) for uid in (last_hit_uids or []) if str(uid)}
    cleaned_bonds = {str(uid): max(0, int(amount)) for uid, amount in (bond_gains or {}).items() if int(amount) > 0}

    ranked = sorted(contributors.items(), key=lambda item: (-item[1], item[0]))
    for rank, (uid, damage) in enumerate(ranked, 1):
        user = _ensure_player(group, uid)
        old_level = _level_of(int(user.get("exp", 0)))
        is_last_hit = uid in last_hit_uid_set
        exp_bonus = int(reward_cfg.get("last_hit_exp_bonus", 0)) if is_last_hit else 0
        points_bonus = int(reward_cfg.get("last_hit_points_bonus", 0)) if is_last_hit else 0
        bond_gain = int(cleaned_bonds.get(uid, 0))
        special_drop = (
            _roll_world_boss_special_drop(user, boss) if allow_special_drop and isinstance(boss, dict) else ""
        )
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
                "bond": bond_gain,
                "special_drop": special_drop,
                "old_level": old_level,
                "new_level": new_level,
                "levelup": new_level > old_level,
                "levelup_text": f"Lv{old_level}→Lv{new_level}" if new_level > old_level else "",
                "last_hit": is_last_hit,
            }
        )
    return rows


def _world_boss_reward_lines(rows: list[dict[str, Any]]) -> list[str]:
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
                bond_part=(f"、羁绊 +{int(row.get('bond', 0))}" if int(row.get("bond", 0)) > 0 else ""),
                drop_part=(f"、获得「{row.get('special_drop', '')}」" if row.get("special_drop") else ""),
                levelup=(f"，升级 {row['levelup_text']}" if row.get("levelup_text") else ""),
            )
            + extra
        )
    return lines


def _normalize_last_hit_uids(last_hit_ref) -> list[str]:
    if isinstance(last_hit_ref, (list, tuple, set)):
        return [str(uid) for uid in last_hit_ref if str(uid)]
    if last_hit_ref is None:
        return []
    text = str(last_hit_ref).strip()
    return [text] if text else []


def _world_boss_kill_settlement(
    group: GroupRecord,
    boss: WorldBossRecord,
    *,
    last_hit_uids: str | list[str] | tuple[str, ...] | set[str] | None = None,
) -> dict[str, Any]:
    contributors = _world_boss_contributors(boss)
    result: dict[str, Any] = {
        "monster": str(boss.get("name", "世界BOSS")),
        "rows": [],
        "last_hit_uids": [],
        "last_hit_name": "",
        "last_hit_reward": {"exp": 0, "points": 0},
        "total_bond": 0,
        "lines": [],
    }
    if not contributors:
        result["lines"] = [_line("world_boss_kill")]
        record_world_boss_settlement(group, boss, [], killed=True)
        _rpg_state(group).pop("world_boss", None)
        return result

    normalized_last_hit = [uid for uid in _normalize_last_hit_uids(last_hit_uids) if uid in contributors]

    reward_values = _world_boss_reward_values(boss)
    rows = _world_boss_reward_results(
        group,
        contributors,
        boss=boss,
        exp_pool=reward_values["exp_pool"],
        points_pool=reward_values["points_pool"],
        exp_fixed=reward_values["exp_fixed"],
        points_fixed=reward_values["points_fixed"],
        last_hit_uids=normalized_last_hit,
        bond_gains=_world_boss_bond_gains(boss),
        allow_special_drop=True,
    )
    result["rows"] = rows
    result["total_bond"] = sum(int(row.get("bond", 0)) for row in rows)
    result["last_hit_uids"] = normalized_last_hit
    last_hit_names: list[str] = []
    last_hit_reward = {"exp": 0, "points": 0}
    for row in rows:
        if row.get("uid") not in normalized_last_hit:
            continue
        last_hit_names.append(str(row.get("name", "")))
        last_hit_reward = {
            "exp": int(row.get("exp_bonus", 0)),
            "points": int(row.get("points_bonus", 0)),
        }
    result["last_hit_name"] = " / ".join(name for name in last_hit_names if name)
    result["last_hit_reward"] = last_hit_reward
    result["lines"] = [_line("world_boss_kill")]
    if result["last_hit_name"]:
        result["lines"].append(_line("world_boss_last_hit", name=result["last_hit_name"]))

    record_world_boss_settlement(group, boss, rows, killed=True)
    _rpg_state(group).pop("world_boss", None)
    return result


def _world_boss_kill_lines(group: GroupRecord, boss: WorldBossRecord) -> list[str]:
    settlement = _world_boss_kill_settlement(
        group, boss, last_hit_uids=boss.get("last_hit_uids") or boss.get("last_hit")
    )
    if not settlement["rows"]:
        return settlement["lines"]
    return [*settlement["lines"], *_world_boss_reward_lines(settlement["rows"])]


def _world_boss_unfinished_lines(group: GroupRecord, boss: WorldBossRecord) -> list[str]:
    contributors = _world_boss_contributors(boss)
    if not contributors:
        record_world_boss_settlement(group, boss, [], killed=False)
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
        boss=boss,
        exp_pool=reward_values["exp_pool"],
        points_pool=reward_values["points_pool"],
        exp_fixed=reward_values["exp_fixed"],
        points_fixed=reward_values["points_fixed"],
        bond_gains=_world_boss_bond_gains(boss),
    )
    record_world_boss_settlement(group, boss, rows, killed=False)
    lines.extend(_world_boss_reward_lines(rows))
    return lines


def _cleanup_stale_world_boss(group: GroupRecord, today: str) -> tuple[list[str], bool]:
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


def _world_boss_status_lines(group: GroupRecord, today: str) -> list[str]:
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

    contributors = {str(uid): max(0, int(dmg)) for uid, dmg in boss.get("contributors", {}).items() if int(dmg) > 0}
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


__all__ = [
    "_cleanup_stale_world_boss",
    "_world_boss_kill_settlement",
    "_world_boss_unfinished_lines",
]
