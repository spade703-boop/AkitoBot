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
from .player import _resolve_group
from .state import _rpg_state
from .types import MetricMemberField, MetricScalarField, RpgMetricDay, WorldBossRecord

METRICS_RETENTION_DAYS = 30

_SCALAR_FIELDS: tuple[MetricScalarField, ...] = (
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
)
_MODE_FIELDS: dict[str, MetricScalarField] = {
    "solo": "solo_battles",
    "team": "team_battles",
    "fallback": "fallback_battles",
}


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
    for member_field in ("players", "world_boss_players"):
        if not isinstance(entry.get(member_field), list):
            entry[member_field] = []
    if not isinstance(entry.get("monsters"), dict):
        entry["monsters"] = {}
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
    entry["battles"] += 1
    entry["wins"] += int(bool(outcome.get("win")))
    mode_field = _MODE_FIELDS.get(mode)
    if mode_field:
        entry[mode_field] += 1
    entry["exp_gained"] += max(0, int(exp_gained))
    entry["points_gained"] += max(0, int(points_gained))
    _add_unique(entry, "players", user_ids)

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


def record_team_attempt(group: GroupRecord, today: str, *, formed: bool) -> None:
    entry = _metric_day(group, today)
    entry["team_attempts"] += 1
    entry["team_formed"] += int(bool(formed))


def record_supply_open(group: GroupRecord, today: str, *, points_spent: int, exp_gained: int) -> None:
    entry = _metric_day(group, today)
    entry["supply_opens"] += 1
    entry["supply_points_spent"] += max(0, int(points_spent))
    entry["supply_exp_gained"] += max(0, int(exp_gained))


def record_world_boss_spawn(group: GroupRecord, today: str, *, forced: bool = False) -> None:
    entry = _metric_day(group, today)
    if forced:
        entry["world_boss_forced_spawns"] += 1
    else:
        entry["world_boss_spawns"] += 1


def record_world_boss_attack(
    group: GroupRecord,
    today: str,
    *,
    user_ids: Iterable[object],
    damage: int,
) -> None:
    entry = _metric_day(group, today)
    entry["world_boss_attacks"] += 1
    entry["world_boss_damage"] += max(0, int(damage))
    _add_unique(entry, "world_boss_players", user_ids)


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
    entry["world_boss_exp_gained"] += sum(max(0, int(row.get("exp", 0))) for row in rows)
    entry["world_boss_points_gained"] += sum(max(0, int(row.get("points", 0))) for row in rows)


def aggregate_metrics(group: GroupRecord, today: str, period_days: int) -> dict[str, Any]:
    period_days = max(1, int(period_days))
    try:
        cutoff = (date.fromisoformat(today) - timedelta(days=period_days - 1)).isoformat()
    except ValueError:
        cutoff = ""
    aggregate: dict[str, int] = {field: 0 for field in _SCALAR_FIELDS}
    players: set[str] = set()
    boss_players: set[str] = set()
    monsters: dict[str, dict[str, int]] = {}
    tracked_days = 0
    for day, raw_entry in _metrics_days(group).items():
        if str(day) < cutoff or str(day) > today or not isinstance(raw_entry, dict):
            continue
        tracked_days += 1
        for field in _SCALAR_FIELDS:
            aggregate[field] += max(0, int(raw_entry.get(field, 0)))
        players.update(str(uid) for uid in raw_entry.get("players", []) if str(uid))
        boss_players.update(str(uid) for uid in raw_entry.get("world_boss_players", []) if str(uid))
        raw_monsters = raw_entry.get("monsters", {})
        if not isinstance(raw_monsters, dict):
            continue
        for name, raw_monster in raw_monsters.items():
            if not isinstance(raw_monster, dict):
                continue
            target = monsters.setdefault(str(name), {"battles": 0, "wins": 0, "elite": 0})
            target["battles"] += max(0, int(raw_monster.get("battles", 0)))
            target["wins"] += max(0, int(raw_monster.get("wins", 0)))
            target["elite"] += max(0, int(raw_monster.get("elite", 0)))
    return {
        **aggregate,
        "period_days": period_days,
        "tracked_days": tracked_days,
        "active_players": len(players),
        "world_boss_players": len(boss_players),
        "monsters": monsters,
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
    return {
        **summary,
        "label": f"近{summary['period_days']}天",
        "win_rate": _rate(wins, battles),
        "win_rate_value": round(wins / battles * 100) if battles else 0,
        "team_rate": _rate(team_formed, team_attempts),
        "team_rate_value": round(team_formed / team_attempts * 100) if team_attempts else 0,
        "boss_spawns": boss_spawns,
    }


def build_metrics_page_data(group: GroupRecord, today: str) -> dict:
    week = aggregate_metrics(group, today, 7)
    month = aggregate_metrics(group, today, 30)
    monster_rows = [
        {
            "name": name,
            "battles": int(stats["battles"]),
            "wins": int(stats["wins"]),
            "win_rate": _rate(int(stats["wins"]), int(stats["battles"])),
        }
        for name, stats in month["monsters"].items()
        if int(stats["battles"]) >= 3
    ]
    monster_rows.sort(key=lambda row: (row["wins"] / row["battles"], -row["battles"], row["name"]))
    return {
        "title": "RPG 运营看板",
        "today": today,
        "page_width": 860,
        "periods": [_period_page_data(week), _period_page_data(month)],
        "low_win_monsters": monster_rows[:3],
    }


async def _render_metrics_image(page_data: dict) -> bytes | None:
    try:
        return await render_bond_page("rpg_metrics.html", page_data, viewport_width=860)
    except Exception as exc:
        logger.warning(f"rpg metrics render failed ({exc}), falling back to text")
        return None


rpg_metrics_cmd = on_command("RPG数据", priority=5, block=True)


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
