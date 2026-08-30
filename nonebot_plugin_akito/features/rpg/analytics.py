"""Rolling group-level RPG metrics and the superuser-only summary command."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any

from nonebot import on_command
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from ...core import SUPERUSER_QQ
from ...core.game_store import LOCK, _get_group, _load_data, _today_str
from ...core.types import GroupRecord
from ..gift.render import render_bond_page
from .player import _level_of, _resolve_group
from .state import _rpg_state
from .types import (
    MetricMemberField,
    MetricScalarField,
    RpgMetricDay,
    WorldBossMetricRecord,
    WorldBossRecord,
)

METRICS_RETENTION_DAYS = 30

_SCALAR_FIELDS: tuple[MetricScalarField, ...] = (
    "signins",
    "signin_exp_gained",
    "signin_streak_bonus",
    "battles",
    "wins",
    "solo_battles",
    "team_battles",
    "fallback_battles",
    "team_attempts",
    "team_formed",
    "exp_gained",
    "points_gained",
    "supply_opens",
    "supply_points_spent",
    "supply_exp_gained",
    "world_boss_spawns",
    "world_boss_forced_spawns",
    "world_boss_attacks",
    "world_boss_damage",
    "world_boss_kills",
    "world_boss_expired",
    "world_boss_exp_gained",
    "world_boss_points_gained",
    "forge_uses",
    "forge_points_spent",
    "world_boss_forge_uses",
    "world_boss_forge_points_spent",
    "rebuy_uses",
    "rebuy_points_spent",
    "drop_attempts",
    "drop_hits",
)
_MODE_FIELDS: dict[str, MetricScalarField] = {
    "solo": "solo_battles",
    "team": "team_battles",
    "fallback": "fallback_battles",
}
_DETAIL_METRIC_FIELDS = {
    "signin": ("signins", "signin_players"),
    "spending": ("forge_uses", "forge_points_spent", "rebuy_uses", "rebuy_points_spent"),
    "events": ("events",),
    "drops": ("drops", "drop_attempts", "drop_hits"),
    "supply": ("supply_players", "supply_items"),
    "world_boss_instances": ("world_boss_instances",),
}


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _metrics_days(group: GroupRecord) -> dict[str, RpgMetricDay]:
    rpg = _rpg_state(group)
    metrics = rpg.get("metrics")
    if not isinstance(metrics, dict):
        metrics = {}
        rpg["metrics"] = metrics
    days = metrics.get("days")
    if not isinstance(days, dict):
        days = {}
        metrics["days"] = days
    return days


def _prune_metrics(group: GroupRecord, today: str) -> None:
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=METRICS_RETENTION_DAYS - 1)).isoformat()
    except ValueError:
        return
    days = _metrics_days(group)
    for day in list(days):
        if str(day) < cutoff:
            days.pop(day, None)


def _metric_day(group: GroupRecord, today: str) -> RpgMetricDay:
    _prune_metrics(group, today)
    days = _metrics_days(group)
    entry = days.get(today)
    if not isinstance(entry, dict):
        entry = {}
        days[today] = entry
    for field in _SCALAR_FIELDS:
        if not isinstance(entry.get(field), int):
            entry[field] = 0
    for member_field in ("players", "signin_players", "supply_players", "world_boss_players"):
        if not isinstance(entry.get(member_field), list):
            entry[member_field] = []
    for map_field in ("monsters", "events", "drops", "supply_items"):
        if not isinstance(entry.get(map_field), dict):
            entry[map_field] = {}
    if not isinstance(entry.get("world_boss_instances"), list):
        entry["world_boss_instances"] = []
    return entry


def _add_unique(entry: RpgMetricDay, key: MetricMemberField, user_ids: Iterable[object]) -> None:
    values = entry.get(key)
    if not isinstance(values, list):
        values = []
        entry[key] = values
    for user_id in user_ids:
        normalized = str(user_id)
        if normalized and normalized not in values:
            values.append(normalized)


def _member_ids(value: object) -> Iterable[object]:
    if isinstance(value, (list, tuple, set)):
        return value
    return ()


def _add_counter(entry: RpgMetricDay, key: str, name: object, amount: int = 1) -> None:
    label = str(name).strip()
    numeric_amount = _safe_int(amount)
    if not label or numeric_amount <= 0:
        return
    values = entry.get(key)
    if not isinstance(values, dict):
        values = {}
        entry[key] = values
    values[label] = _safe_int(values.get(label, 0)) + numeric_amount


def _mark_rpg_first_seen(group: GroupRecord, user_ids: Iterable[object], today: str) -> None:
    users = group.get("users", {})
    if not isinstance(users, dict):
        return
    for user_id in user_ids:
        user = users.get(str(user_id))
        if isinstance(user, dict) and not str(user.get("rpg_first_seen", "")):
            user["rpg_first_seen"] = today


def _reward_records(outcome: dict[str, Any]) -> list[dict[str, Any]]:
    records = []
    if isinstance(outcome.get("drops"), list):
        records.append(outcome)
    for key in ("b", "a"):
        reward = outcome.get(key)
        if isinstance(reward, dict):
            records.append(reward)
    return records


def _record_battle_details(entry: RpgMetricDay, outcome: dict[str, Any]) -> None:
    event_fields = (
        ("battle", outcome.get("event")),
        ("support", outcome.get("support_scene")),
        ("team", outcome.get("team_event")),
        ("team_negative", outcome.get("negative_event")),
        ("minor", outcome.get("minor_event")),
        ("team_minor", outcome.get("team_minor_event")),
        ("team_support", outcome.get("team_support_variant")),
    )
    for category, value in event_fields:
        if value:
            _add_counter(entry, "events", f"{category}:{value}")
    if outcome.get("battle_guard_triggered") or outcome.get("battle_guard_owner"):
        _add_counter(entry, "events", "battle_guard")
    buff = outcome.get("buff")
    if isinstance(buff, dict) and str(buff.get("key", "plain")) != "plain":
        _add_counter(entry, "events", f"daily_buff:{buff.get('key', 'unknown')}")

    for reward in _reward_records(outcome):
        entry["drop_attempts"] += 1
        drops = reward.get("drops")
        if not isinstance(drops, list):
            continue
        if drops:
            entry["drop_hits"] += 1
        for item_name in drops:
            _add_counter(entry, "drops", item_name)
        if reward.get("exp_buffed"):
            _add_counter(entry, "events", "exp_buff")


def record_signin(
    group: GroupRecord,
    today: str,
    *,
    user_id: object,
    exp_gained: int,
    streak_bonus: int = 0,
    fortune: str = "",
) -> None:
    entry = _metric_day(group, today)
    entry["signins"] += 1
    entry["signin_exp_gained"] += max(0, _safe_int(exp_gained))
    entry["signin_streak_bonus"] += max(0, _safe_int(streak_bonus))
    _add_unique(entry, "signin_players", [user_id])
    _mark_rpg_first_seen(group, [user_id], today)
    _add_counter(entry, "events", "signin")
    if fortune:
        _add_counter(entry, "events", f"fortune:{fortune}")


def record_battle(
    group: GroupRecord,
    today: str,
    *,
    mode: str,
    user_ids: Iterable[object],
    outcome: dict[str, Any],
    exp_gained: int,
    points_gained: int,
) -> None:
    entry = _metric_day(group, today)
    participant_ids = list(user_ids)
    entry["battles"] += 1
    entry["wins"] += int(bool(outcome.get("win")))
    mode_field = _MODE_FIELDS.get(mode)
    if mode_field:
        entry[mode_field] += 1
    entry["exp_gained"] += max(0, _safe_int(exp_gained))
    entry["points_gained"] += max(0, _safe_int(points_gained))
    _add_unique(entry, "players", participant_ids)
    _mark_rpg_first_seen(group, participant_ids, today)
    _record_battle_details(entry, outcome)

    monster = outcome.get("monster", {})
    monster_name = str(monster.get("name", "未知怪物")) if isinstance(monster, dict) else "未知怪物"
    monsters = entry["monsters"]
    monster_entry = monsters.get(monster_name)
    if not isinstance(monster_entry, dict):
        monster_entry = {"battles": 0, "wins": 0, "elite": 0}
        monsters[monster_name] = monster_entry
    monster_entry["battles"] = int(monster_entry.get("battles", 0)) + 1
    monster_entry["wins"] = int(monster_entry.get("wins", 0)) + int(bool(outcome.get("win")))
    monster_entry["elite"] = int(monster_entry.get("elite", 0)) + int(bool(outcome.get("elite")))


def record_forge(
    group: GroupRecord,
    today: str,
    *,
    points_spent: int,
    world_boss: bool = False,
    refund: int = 0,
) -> None:
    net_spent = max(0, _safe_int(points_spent) - max(0, _safe_int(refund)))
    entry = _metric_day(group, today)
    entry["forge_uses"] += 1
    entry["forge_points_spent"] += net_spent
    if world_boss:
        entry["world_boss_forge_uses"] += 1
        entry["world_boss_forge_points_spent"] += net_spent
        _add_counter(entry, "events", "forge:world_boss")
    else:
        _add_counter(entry, "events", "forge:regular")


def record_rebuy(
    group: GroupRecord,
    today: str,
    *,
    points_spent: int,
    refund: int = 0,
) -> None:
    net_spent = max(0, _safe_int(points_spent) - max(0, _safe_int(refund)))
    entry = _metric_day(group, today)
    entry["rebuy_uses"] += 1
    entry["rebuy_points_spent"] += net_spent
    _add_counter(entry, "events", "equip_rebuy")


def record_team_attempt(group: GroupRecord, today: str, *, formed: bool) -> None:
    entry = _metric_day(group, today)
    entry["team_attempts"] += 1
    entry["team_formed"] += int(bool(formed))


def record_supply_open(
    group: GroupRecord,
    today: str,
    *,
    points_spent: int,
    exp_gained: int,
    count: int = 1,
    user_id: object | None = None,
    items: dict[str, int] | None = None,
) -> None:
    entry = _metric_day(group, today)
    entry["supply_opens"] += max(0, _safe_int(count))
    entry["supply_points_spent"] += max(0, _safe_int(points_spent))
    entry["supply_exp_gained"] += max(0, _safe_int(exp_gained))
    if user_id is not None:
        _add_unique(entry, "supply_players", [user_id])
        _mark_rpg_first_seen(group, [user_id], today)
    _add_counter(entry, "events", "supply_open", max(0, _safe_int(count)))
    if isinstance(items, dict):
        for item_name, amount in items.items():
            _add_counter(entry, "supply_items", item_name, max(0, _safe_int(amount)))


def _find_boss_instance(
    entry: RpgMetricDay,
    boss: WorldBossRecord | None = None,
) -> WorldBossMetricRecord | None:
    instances = entry.get("world_boss_instances")
    if not isinstance(instances, list):
        instances = []
        entry["world_boss_instances"] = instances
    metric_id = str(boss.get("metric_id", "")) if isinstance(boss, dict) else ""
    for instance in reversed(instances):
        if not isinstance(instance, dict):
            continue
        if metric_id and str(instance.get("id", "")) == metric_id:
            return instance
        if (
            not metric_id
            and isinstance(boss, dict)
            and str(instance.get("date", "")) == str(boss.get("date", ""))
            and not bool(instance.get("killed"))
            and not bool(instance.get("expired"))
        ):
            return instance
    return None


def record_world_boss_spawn(
    group: GroupRecord,
    today: str,
    *,
    forced: bool = False,
    boss: WorldBossRecord | None = None,
) -> str:
    entry = _metric_day(group, today)
    if forced:
        entry["world_boss_forced_spawns"] += 1
        _add_counter(entry, "events", "boss:forced_spawn")
    else:
        entry["world_boss_spawns"] += 1
        _add_counter(entry, "events", "boss:spawn")
    instances = entry["world_boss_instances"]
    metric_id = f"{today}#{len(instances) + 1}"
    instances.append(
        {
            "id": metric_id,
            "date": today,
            "name": str(boss.get("name", "世界BOSS")) if isinstance(boss, dict) else "世界BOSS",
            "max_hp": max(0, _safe_int(boss.get("max_hp", 0))) if isinstance(boss, dict) else 0,
            "participants": 0,
            "attacks": 0,
            "damage": 0,
            "killed": False,
            "expired": False,
            "reward_players": 0,
            "reward_exp": 0,
            "reward_points": 0,
        }
    )
    return metric_id


def record_world_boss_attack(
    group: GroupRecord,
    today: str,
    *,
    user_ids: Iterable[object],
    damage: int,
    boss: WorldBossRecord | None = None,
    event: str = "",
) -> None:
    entry = _metric_day(group, today)
    entry["world_boss_attacks"] += 1
    entry["world_boss_damage"] += max(0, _safe_int(damage))
    participant_ids = list(user_ids)
    _add_unique(entry, "world_boss_players", participant_ids)
    _mark_rpg_first_seen(group, participant_ids, today)
    _add_counter(entry, "events", "boss:attack")
    if event:
        _add_counter(entry, "events", f"boss:{event}")
    instance = _find_boss_instance(entry, boss)
    if instance is not None:
        instance["attacks"] = _safe_int(instance.get("attacks", 0)) + 1
        instance["damage"] = _safe_int(instance.get("damage", 0)) + max(0, _safe_int(damage))
        contributors = boss.get("contributors", {}) if isinstance(boss, dict) else {}
        contributor_count = len(contributors) if isinstance(contributors, dict) and contributors else len(
            {str(user_id) for user_id in participant_ids if str(user_id)}
        )
        instance["participants"] = max(_safe_int(instance.get("participants", 0)), contributor_count)


def record_world_boss_settlement(
    group: GroupRecord,
    boss: WorldBossRecord,
    rows: list[dict[str, Any]],
    *,
    killed: bool,
) -> None:
    boss_day = str(boss.get("date") or _today_str())
    entry = _metric_day(group, boss_day)
    entry["world_boss_kills" if killed else "world_boss_expired"] += 1
    exp_gained = sum(max(0, _safe_int(row.get("exp", 0))) for row in rows)
    points_gained = sum(max(0, _safe_int(row.get("points", 0))) for row in rows)
    entry["world_boss_exp_gained"] += exp_gained
    entry["world_boss_points_gained"] += points_gained
    _add_counter(entry, "events", "boss:killed" if killed else "boss:expired")
    instance = _find_boss_instance(entry, boss)
    if instance is not None:
        instance["killed"] = bool(killed)
        instance["expired"] = not killed
        instance["reward_players"] = len(rows)
        instance["reward_exp"] = exp_gained
        instance["reward_points"] = points_gained


def aggregate_metrics(group: GroupRecord, today: str, period_days: int) -> dict[str, Any]:
    period_days = max(1, int(period_days))
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=period_days - 1)).isoformat()
    except ValueError:
        cutoff = ""
    aggregate: dict[str, int] = {field: 0 for field in _SCALAR_FIELDS}
    players: set[str] = set()
    signin_players: set[str] = set()
    supply_players: set[str] = set()
    boss_players: set[str] = set()
    monsters: dict[str, dict[str, int]] = {}
    events: dict[str, int] = {}
    drops: dict[str, int] = {}
    supply_items: dict[str, int] = {}
    world_boss_instances: list[WorldBossMetricRecord] = []
    detail_available = {name: False for name in _DETAIL_METRIC_FIELDS}
    tracked_days = 0
    for day, raw_entry in _metrics_days(group).items():
        if str(day) < cutoff or str(day) > today or not isinstance(raw_entry, dict):
            continue
        tracked_days += 1
        for name, fields in _DETAIL_METRIC_FIELDS.items():
            detail_available[name] = detail_available[name] or any(field in raw_entry for field in fields)
        for field in _SCALAR_FIELDS:
            aggregate[field] += max(0, _safe_int(raw_entry.get(field, 0)))
        players.update(str(uid) for uid in _member_ids(raw_entry.get("players")) if str(uid))
        signin_players.update(str(uid) for uid in _member_ids(raw_entry.get("signin_players")) if str(uid))
        supply_players.update(str(uid) for uid in _member_ids(raw_entry.get("supply_players")) if str(uid))
        boss_players.update(str(uid) for uid in _member_ids(raw_entry.get("world_boss_players")) if str(uid))
        raw_events = raw_entry.get("events", {})
        if isinstance(raw_events, dict):
            for key, value in raw_events.items():
                if isinstance(value, (int, float)):
                    events[str(key)] = events.get(str(key), 0) + max(0, _safe_int(value))
        raw_drops = raw_entry.get("drops", {})
        if isinstance(raw_drops, dict):
            for key, value in raw_drops.items():
                if isinstance(value, (int, float)):
                    drops[str(key)] = drops.get(str(key), 0) + max(0, _safe_int(value))
        raw_supply_items = raw_entry.get("supply_items", {})
        if isinstance(raw_supply_items, dict):
            for key, value in raw_supply_items.items():
                if isinstance(value, (int, float)):
                    supply_items[str(key)] = supply_items.get(str(key), 0) + max(0, _safe_int(value))
        raw_instances = raw_entry.get("world_boss_instances", [])
        if isinstance(raw_instances, list):
            for raw_instance in raw_instances:
                if isinstance(raw_instance, dict):
                    world_boss_instances.append(dict(raw_instance))
        raw_monsters = raw_entry.get("monsters", {})
        if not isinstance(raw_monsters, dict):
            continue
        for name, raw_monster in raw_monsters.items():
            if not isinstance(raw_monster, dict):
                continue
            target = monsters.setdefault(str(name), {"battles": 0, "wins": 0, "elite": 0})
            target["battles"] += max(0, _safe_int(raw_monster.get("battles", 0)))
            target["wins"] += max(0, _safe_int(raw_monster.get("wins", 0)))
            target["elite"] += max(0, _safe_int(raw_monster.get("elite", 0)))
    return {
        **aggregate,
        "period_days": period_days,
        "tracked_days": tracked_days,
        "active_players": len(players),
        "signin_players": len(signin_players),
        "supply_players": len(supply_players),
        "world_boss_players": len(boss_players),
        "monsters": monsters,
        "events": events,
        "drops": drops,
        "supply_items": supply_items,
        "world_boss_instances": world_boss_instances,
        "signin_metrics_available": detail_available["signin"],
        "spending_metrics_available": detail_available["spending"],
        "event_metrics_available": detail_available["events"],
        "drop_metrics_available": detail_available["drops"],
        "supply_metrics_available": detail_available["supply"],
        "world_boss_instance_metrics_available": detail_available["world_boss_instances"],
    }


def _rate(numerator: int, denominator: int) -> str:
    return f"{numerator / denominator:.1%}" if denominator > 0 else "暂无"


def _summary_lines(summary: dict) -> list[str]:
    battles = int(summary["battles"])
    team_attempts = int(summary["team_attempts"])
    boss_spawns = int(summary["world_boss_spawns"]) + int(summary["world_boss_forced_spawns"])
    return [
        f"【近{summary['period_days']}天】记录 {summary['tracked_days']} 天，活跃 {summary['active_players']} 人",
        (
            f"· 普通战斗 {battles} 场：单人 {summary['solo_battles']} / "
            f"组队 {summary['team_battles']} / 失败改单刷 {summary['fallback_battles']}，"
            f"胜率 {_rate(int(summary['wins']), battles)}"
        ),
        f"· 组队邀请 {team_attempts} 次，成立 {summary['team_formed']} 次（{_rate(int(summary['team_formed']), team_attempts)}）",
        f"· 普通投放：经验 +{summary['exp_gained']}，积分 +{summary['points_gained']}",
        (
            f"· 冒险补给：开启 {summary['supply_opens']} 次，"
            f"消耗 {summary['supply_points_spent']} 积分，固定经验 +{summary['supply_exp_gained']}"
        ),
        (
            f"· 世界BOSS：刷新 {boss_spawns} 次（强制 {summary['world_boss_forced_spawns']}），"
            f"击杀 {summary['world_boss_kills']} / 离场 {summary['world_boss_expired']}，"
            f"挑战 {summary['world_boss_attacks']} 次、参与 {summary['world_boss_players']} 人"
        ),
        f"· BOSS投放：经验 +{summary['world_boss_exp_gained']}，积分 +{summary['world_boss_points_gained']}",
    ]


def build_metrics_report(group: GroupRecord, today: str) -> str:
    week = aggregate_metrics(group, today, 7)
    month = aggregate_metrics(group, today, 30)
    monster_rows = [
        (name, int(stats["battles"]), int(stats["wins"]))
        for name, stats in month["monsters"].items()
        if int(stats["battles"]) >= 3
    ]
    monster_rows.sort(key=lambda row: (row[2] / row[1], -row[1], row[0]))
    lines = ["📊 RPG运营数据", "· 仅统计本功能上线后的滚动记录。", *_summary_lines(week), *_summary_lines(month)]
    if monster_rows:
        lowest = "、".join(f"{name} {wins}/{battles}" for name, battles, wins in monster_rows[:3])
        lines.append(f"· 近30天低胜率怪物：{lowest}")
    return "\n".join(lines)


def _period_page_data(summary: dict) -> dict:
    battles = int(summary["battles"])
    wins = int(summary["wins"])
    team_attempts = int(summary["team_attempts"])
    team_formed = int(summary["team_formed"])
    boss_spawns = int(summary["world_boss_spawns"]) + int(summary["world_boss_forced_spawns"])
    active_players = int(summary["active_players"])
    boss_players = int(summary["world_boss_players"])
    return {
        **summary,
        "label": f"近{summary['period_days']}天",
        "win_rate": _rate(wins, battles),
        "win_rate_value": round(wins / battles * 100) if battles else 0,
        "team_rate": _rate(team_formed, team_attempts),
        "team_rate_value": round(team_formed / team_attempts * 100) if team_attempts else 0,
        "boss_spawns": boss_spawns,
        "boss_completion_rate": _rate(int(summary["world_boss_kills"]), boss_spawns),
        "boss_completion_rate_value": round(int(summary["world_boss_kills"]) / boss_spawns * 100) if boss_spawns else 0,
        "boss_participation_rate": _rate(boss_players, active_players),
        "boss_participation_rate_value": round(boss_players / active_players * 100) if active_players else 0,
        "avg_exp_per_battle": round(int(summary["exp_gained"]) / battles) if battles else 0,
        "avg_points_per_battle": round(int(summary["points_gained"]) / battles, 1) if battles else 0,
        "signin_avg_per_player": round(int(summary["signins"]) / int(summary["signin_players"]), 1)
        if int(summary["signin_players"])
        else 0,
        "signin_base_exp": max(0, int(summary["signin_exp_gained"]) - int(summary["signin_streak_bonus"])),
        "signin_streak_percent": round(
            int(summary["signin_streak_bonus"]) / int(summary["signin_exp_gained"]) * 100, 1
        )
        if int(summary["signin_exp_gained"])
        else 0,
        "drop_hit_rate": _rate(int(summary["drop_hits"]), int(summary["drop_attempts"])),
        "forge_avg_cost": round(int(summary["forge_points_spent"]) / int(summary["forge_uses"]))
        if int(summary["forge_uses"])
        else 0,
        "rebuy_avg_cost": round(int(summary["rebuy_points_spent"]) / int(summary["rebuy_uses"]))
        if int(summary["rebuy_uses"])
        else 0,
        "forge_rebuy_points_spent": int(summary["forge_points_spent"]) + int(summary["rebuy_points_spent"]),
        "battle_mix": [
            {
                "label": "单人",
                "value": int(summary["solo_battles"]),
                "percent": round(int(summary["solo_battles"]) / battles * 100) if battles else 0,
                "tone": "warm",
            },
            {
                "label": "组队",
                "value": int(summary["team_battles"]),
                "percent": round(int(summary["team_battles"]) / battles * 100) if battles else 0,
                "tone": "cool",
            },
            {
                "label": "回退",
                "value": int(summary["fallback_battles"]),
                "percent": round(int(summary["fallback_battles"]) / battles * 100) if battles else 0,
                "tone": "muted",
            },
        ],
    }


def _period_days(days: dict, today: str, period_days: int) -> list[tuple[str, dict]]:
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=max(1, int(period_days)) - 1)).isoformat()
    except ValueError:
        cutoff = ""
    return [
        (str(day), entry)
        for day, entry in sorted(days.items())
        if cutoff <= str(day) <= today and isinstance(entry, dict)
    ]


def _activity_trend(group: GroupRecord, today: str, period_days: int = 7) -> list[dict[str, Any]]:
    trend = []
    for day, entry in _period_days(_metrics_days(group), today, period_days):
        battles = max(0, int(entry.get("battles", 0)))
        wins = max(0, int(entry.get("wins", 0)))
        trend.append(
            {
                "label": day[5:],
                "battles": battles,
                "players": len({str(uid) for uid in _member_ids(entry.get("players")) if str(uid)}),
                "win_rate": _rate(wins, battles),
                "win_rate_value": round(wins / battles * 100) if battles else 0,
            }
        )
    return trend


def _active_level_distribution(group: GroupRecord, today: str, period_days: int = 30) -> list[dict[str, Any]]:
    active_ids: set[str] = set()
    for _day, entry in _period_days(_metrics_days(group), today, period_days):
        active_ids.update(str(uid) for uid in _member_ids(entry.get("players")) if str(uid))
    users = group.get("users", {})
    levels: dict[int, int] = {}
    for uid in active_ids:
        user = users.get(uid) if isinstance(users, dict) else None
        if not isinstance(user, dict):
            continue
        level = _level_of(int(user.get("exp", 0)))
        levels[level] = levels.get(level, 0) + 1
    peak = max(levels.values(), default=0)
    return [
        {
            "level": level,
            "players": players,
            "percent": round(players / len(active_ids) * 100) if active_ids else 0,
            "height": round(players / peak * 100) if peak else 0,
        }
        for level, players in sorted(levels.items())
    ]


_EVENT_CATEGORY_LABELS = {
    "signin": "签到",
    "fortune": "签到运势",
    "battle": "战斗特判",
    "support": "战斗支援",
    "team": "组队事件",
    "team_negative": "组队负面事件",
    "team_minor": "组队小奇遇",
    "team_support": "组队援护",
    "minor": "个人小奇遇",
    "battle_guard": "护符救场",
    "exp_buff": "双倍经验",
    "daily_buff": "每日增益",
    "supply_open": "冒险补给",
    "forge": "装备强化",
    "equip_rebuy": "购买替换装",
    "boss": "世界BOSS",
}
_EVENT_NAME_LABELS = {
    "insight": "看破",
    "desperate": "背水一战",
    "slip": "行动受阻",
    "akito_success": "彰人追击（胜利）",
    "akito_fail": "彰人追击（失败）",
    "toya_rescue": "冬弥援护",
    "duo_combo": "彰冬联携",
    "focus_fire": "集中火力",
    "cover_route": "交替掩护",
    "follow_up": "追加攻击",
    "missed_beat": "配合失误",
    "friction": "配合摩擦",
    "misread": "判断偏差",
    "loose_guard": "防守松动",
    "break_ice": "关系缓和",
    "supply_cache": "补给袋",
    "campfire": "营火",
    "worn_chest": "旧宝箱",
    "lost_pouch": "遗失钱袋",
    "regular": "普通装备",
    "world_boss": "世界BOSS装备",
    "daji": "大吉",
    "ji": "吉",
    "ping": "中平",
    "xiaoxiong": "小凶",
    "daxiong": "大凶",
    "drop": "掉落翻倍日",
    "exp": "经验涌动日",
    "spawn": "普通刷新",
    "forced_spawn": "强制刷新",
    "attack": "普通攻击",
    "team_attack": "组队攻击",
    "team_fail": "组队失败后攻击",
    "killed": "击杀结算",
    "expired": "离场结算",
}


def _event_label(key: object) -> str:
    raw_key = str(key)
    category, separator, name = raw_key.partition(":")
    if not separator:
        return _EVENT_NAME_LABELS.get(raw_key, raw_key)
    category_label = _EVENT_CATEGORY_LABELS.get(category, category)
    nested_category, nested_separator, nested_name = name.partition(":")
    if nested_separator:
        name_label = " · ".join(
            (
                _EVENT_NAME_LABELS.get(nested_category, nested_category),
                _EVENT_NAME_LABELS.get(nested_name, nested_name),
            )
        )
    else:
        name_label = _EVENT_NAME_LABELS.get(name, name)
    return f"{category_label} · {name_label}"


def _counter_rows(values: object, labeler, *, limit: int = 10) -> list[dict[str, Any]]:
    if not isinstance(values, dict):
        return []
    rows = [
        {"label": labeler(key), "value": max(0, _safe_int(value))}
        for key, value in values.items()
        if isinstance(value, (int, float)) and _safe_int(value) > 0
    ]
    rows.sort(key=lambda row: (-row["value"], row["label"]))
    peak = max((row["value"] for row in rows), default=0)
    for row in rows:
        row["percent"] = round(row["value"] / peak * 100) if peak else 0
    return rows[:limit]


def _boss_instance_rows(summary: dict) -> list[dict[str, Any]]:
    raw_instances = summary.get("world_boss_instances", [])
    if not isinstance(raw_instances, list):
        return []
    rows = []
    for instance in raw_instances:
        if not isinstance(instance, dict):
            continue
        if instance.get("killed"):
            status = "已击杀"
        elif instance.get("expired"):
            status = "已离场"
        else:
            status = "未结算"
        rows.append(
            {
                "date": str(instance.get("date", "")),
                "name": str(instance.get("name", "世界BOSS")),
                "participants": max(0, _safe_int(instance.get("participants", 0))),
                "attacks": max(0, _safe_int(instance.get("attacks", 0))),
                "damage": max(0, _safe_int(instance.get("damage", 0))),
                "reward_players": max(0, _safe_int(instance.get("reward_players", 0))),
                "reward_exp": max(0, _safe_int(instance.get("reward_exp", 0))),
                "reward_points": max(0, _safe_int(instance.get("reward_points", 0))),
                "status": status,
            }
        )
    rows.sort(key=lambda row: row["date"])
    return rows[-10:]


def build_metrics_page_data(group: GroupRecord, today: str) -> dict:
    week = aggregate_metrics(group, today, 7)
    month = aggregate_metrics(group, today, 30)
    monster_rows = [
        {
            "name": name,
            "battles": int(stats["battles"]),
            "wins": int(stats["wins"]),
            "elite": int(stats.get("elite", 0)),
            "win_rate": _rate(int(stats["wins"]), int(stats["battles"])),
        }
        for name, stats in month["monsters"].items()
        if int(stats["battles"]) >= 3
    ]
    monster_rows.sort(key=lambda row: (row["wins"] / row["battles"], -row["battles"], row["name"]))
    periods = [_period_page_data(week), _period_page_data(month)]
    periods[0]["comparison_label"] = "近期表现"
    periods[1]["comparison_label"] = "30天基线"
    return {
        "title": "RPG 运营看板",
        "today": today,
        "page_width": 820,
        "periods": periods,
        "low_win_monsters": monster_rows[:3],
        "activity_trend": _activity_trend(group, today),
        "level_distribution": _active_level_distribution(group, today),
        "event_rows": _counter_rows(month.get("events", {}), _event_label),
        "drop_rows": _counter_rows(month.get("drops", {}), str),
        "supply_rows": _counter_rows(month.get("supply_items", {}), str),
        "boss_instance_rows": _boss_instance_rows(month),
    }


def _build_style_test_group(today: str) -> GroupRecord:
    user_ids = [f"style-{index}" for index in range(1, 9)]
    users = {
        user_id: {
            "exp": 135 * (index + 1) * index // 2 + 60 * index,
            "points": 300 + index * 47,
            "display_name": f"看板测试冒险者{index}",
        }
        for index, user_id in enumerate(user_ids, start=1)
    }
    days: dict[str, RpgMetricDay] = {}
    monster_names = ("史莱姆", "泥怪", "哥布林", "野狼")
    for offset in range(30):
        day = (date.fromisoformat(today) - timedelta(days=29 - offset)).isoformat()
        battles = 18 + offset % 9
        wins = max(1, battles - 3 - offset % 4)
        players = user_ids[: 4 + offset % 5]
        monsters = {
            name: {
                "battles": battles // 4 + (index == offset % 4),
                "wins": max(0, battles // 4 - (index == (offset + 1) % 4)),
                "elite": 1 if index == offset % 4 else 0,
            }
            for index, name in enumerate(monster_names)
        }
        days[day] = {
            **{field: 0 for field in _SCALAR_FIELDS},
            "signins": len(players),
            "signin_exp_gained": len(players) * (10 + min(offset % 8, 5)),
            "signin_streak_bonus": len(players) * min(offset % 8, 5),
            "battles": battles,
            "wins": wins,
            "solo_battles": battles // 2,
            "team_battles": battles - battles // 2 - 1,
            "fallback_battles": 1,
            "team_attempts": battles - battles // 2,
            "team_formed": battles - battles // 2 - 1,
            "exp_gained": wins * 68,
            "points_gained": wins * 5,
            "supply_opens": 1 + offset % 3,
            "supply_points_spent": (1 + offset % 3) * 140,
            "supply_exp_gained": (1 + offset % 3) * 30,
            "forge_uses": offset % 4,
            "forge_points_spent": (offset % 4) * 60,
            "world_boss_forge_uses": 1 if offset % 4 == 0 else 0,
            "world_boss_forge_points_spent": 30 if offset % 4 == 0 else 0,
            "rebuy_uses": 1 if offset % 7 == 0 else 0,
            "rebuy_points_spent": 100 if offset % 7 == 0 else 0,
            "drop_attempts": battles + battles - battles // 2 - 1,
            "drop_hits": battles,
            "world_boss_spawns": 1 if offset % 4 == 0 else 0,
            "world_boss_attacks": 4 if offset % 4 == 0 else 0,
            "world_boss_damage": 620 if offset % 4 == 0 else 0,
            "world_boss_kills": 1 if offset % 4 == 0 else 0,
            "world_boss_exp_gained": 460 if offset % 4 == 0 else 0,
            "world_boss_points_gained": 72 if offset % 4 == 0 else 0,
            "players": players,
            "signin_players": players,
            "supply_players": players[: min(3, len(players))],
            "world_boss_players": players[: min(5, len(players))] if offset % 4 == 0 else [],
            "monsters": monsters,
            "events": {
                "signin": len(players),
                "fortune:ji": max(1, len(players) // 3),
                "battle:insight": 2 + offset % 3,
                "support:akito_success": 1 if offset % 3 == 0 else 0,
                "minor:worn_chest": 1 if offset % 5 == 0 else 0,
                "forge:regular": offset % 4,
                "supply_open": 1 + offset % 3,
            },
            "drops": {"经验书": 2 + offset % 4, "史莱姆黏液": 1 + offset % 3},
            "supply_items": {"旅人的行囊": 1 + offset % 2, "龙骑士的地图": offset % 2},
            "world_boss_instances": [
                {
                    "id": f"{day}#1",
                    "date": day,
                    "name": "赤鳞灾龙",
                    "max_hp": 1200,
                    "participants": min(5, len(players)),
                    "attacks": 4,
                    "damage": 620,
                    "killed": True,
                    "expired": False,
                    "reward_players": min(5, len(players)),
                    "reward_exp": 460,
                    "reward_points": 72,
                }
            ] if offset % 4 == 0 else [],
        }
    return {"user_ids": user_ids, "users": users, "rpg": {"metrics": {"days": days}}}


async def _render_metrics_image(page_data: dict) -> bytes | None:
    try:
        return await render_bond_page("rpg_metrics.html", page_data, viewport_width=860)
    except Exception as exc:
        logger.warning(f"rpg metrics render failed ({exc}), falling back to text")
        return None


rpg_metrics_cmd = on_command("RPG数据", priority=5, block=True)
style_test_cmd = on_command("看板样式测试", priority=5, block=True)


@rpg_metrics_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        page_data = build_metrics_page_data(group, today)
        report = build_metrics_report(group, today)
    image = await _render_metrics_image(page_data)
    if image is not None:
        await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(image))
    await rpg_metrics_cmd.finish(MessageSegment.reply(event.message_id) + report)


@style_test_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group(event)
    if rejection:
        await style_test_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None or (args and args.extract_plain_text().strip()):
        return
    today = _today_str()
    group = _build_style_test_group(today)
    page_data = build_metrics_page_data(group, today)
    image = await _render_metrics_image(page_data)
    reply_prefix = MessageSegment.reply(event.message_id)
    if image is not None:
        await style_test_cmd.finish(reply_prefix + MessageSegment.image(image))
    await style_test_cmd.finish(reply_prefix + "看板样式测试渲染失败，已切回文字预览。\n" + build_metrics_report(group, today))
