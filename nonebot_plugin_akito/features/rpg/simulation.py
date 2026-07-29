"""Deterministic long-term simulation for the solo RPG growth baseline."""

from __future__ import annotations

from datetime import date, timedelta
import random

from . import combat, fortune, rewards
from .config import _cfg
from .player import _grant_equip, _level_of

GROWTH_TARGETS = {
    30: (5, 6),
    90: (10, 13),
    180: (16, 20),
}


def _percentile(values: list[int], ratio: float) -> int:
    ordered = sorted(values)
    if not ordered:
        return 0
    index = round((len(ordered) - 1) * max(0.0, min(1.0, ratio)))
    return int(ordered[index])


def _simulate_signin(user: dict, today: str, streak: int, rng: random.Random) -> None:
    key = fortune._roll_fortune(user, rng)
    fortune_cfg = _cfg("fortune", {})
    lucky = set(fortune_cfg.get("lucky_keys", []))
    user["no_lucky_streak"] = 0 if key in lucky else int(user.get("no_lucky_streak", 0)) + 1
    user["last_fortune"] = key
    user["fortune"] = key
    user["fortune_date"] = today
    user["signin_streak"] = streak
    user["signin_last_date"] = today

    streak_cfg = _cfg("signin_streak", {})
    streak_bonus = min(
        max(streak - 1, 0) * int(streak_cfg.get("per_day", 0)),
        int(streak_cfg.get("cap", 0)),
    )
    user["exp"] = int(user.get("exp", 0)) + int(_cfg("signin", {}).get("exp", 0)) + streak_bonus
    _grant_equip(user, today, rng)


def simulate_solo_growth(
    *,
    days: int = 360,
    runs: int = 1000,
    seed: int = 20260727,
    checkpoints: tuple[int, ...] = (30, 90, 180, 270, 360),
) -> dict:
    """Simulate continuous daily sign-in plus one direct solo hunt per day."""
    days = max(1, int(days))
    runs = max(1, int(runs))
    selected_checkpoints = tuple(sorted({int(day) for day in checkpoints if 0 < int(day) <= days}))
    levels = {day: [] for day in selected_checkpoints}
    experiences = {day: [] for day in selected_checkpoints}
    total_battles = 0
    total_wins = 0
    level_30_days: list[int] = []
    monster_stats: dict[str, dict[str, int]] = {}
    start_day = date(2026, 1, 1)

    for run_index in range(runs):
        rng = random.Random(seed + run_index * 1009)
        user: dict = {"exp": 0, "points": 0, "inventory": {}}
        reached_level_30 = False
        for day_index in range(1, days + 1):
            today = (start_day + timedelta(days=day_index - 1)).isoformat()
            _simulate_signin(user, today, day_index, rng)
            outcome = rewards._settle_solo(
                user,
                today,
                direct=True,
                rng=rng,
                buff=combat._buff_for_date(today),
            )
            total_battles += 1
            total_wins += int(bool(outcome.get("win")))
            monster_name = str(outcome.get("monster", {}).get("name", "未知怪物"))
            monster = monster_stats.setdefault(monster_name, {"battles": 0, "wins": 0})
            monster["battles"] += 1
            monster["wins"] += int(bool(outcome.get("win")))
            current_level = _level_of(int(user.get("exp", 0)))
            if not reached_level_30 and current_level >= 30:
                level_30_days.append(day_index)
                reached_level_30 = True
            if day_index in levels:
                levels[day_index].append(current_level)
                experiences[day_index].append(int(user.get("exp", 0)))

    checkpoint_results = {}
    for checkpoint in selected_checkpoints:
        checkpoint_results[str(checkpoint)] = {
            "level_p10": _percentile(levels[checkpoint], 0.10),
            "level_median": _percentile(levels[checkpoint], 0.50),
            "level_p90": _percentile(levels[checkpoint], 0.90),
            "exp_median": _percentile(experiences[checkpoint], 0.50),
        }

    return {
        "runs": runs,
        "days": days,
        "seed": seed,
        "checkpoints": checkpoint_results,
        "level_30": {
            "reached_runs": len(level_30_days),
            "reach_rate": len(level_30_days) / runs,
            "day_p10": _percentile(level_30_days, 0.10) if level_30_days else None,
            "day_median": _percentile(level_30_days, 0.50) if level_30_days else None,
            "day_p90": _percentile(level_30_days, 0.90) if level_30_days else None,
        },
        "win_rate": total_wins / max(1, total_battles),
        "monsters": {
            name: {
                **stats,
                "win_rate": stats["wins"] / max(1, stats["battles"]),
            }
            for name, stats in monster_stats.items()
        },
    }


def growth_baseline_violations(result: dict) -> list[str]:
    violations: list[str] = []
    checkpoints = result.get("checkpoints", {})
    for day, (minimum, maximum) in GROWTH_TARGETS.items():
        summary = checkpoints.get(str(day))
        if not isinstance(summary, dict):
            continue
        median_level = int(summary.get("level_median", 0))
        if not minimum <= median_level <= maximum:
            violations.append(f"{day} 天中位等级 Lv{median_level}，目标为 Lv{minimum}-Lv{maximum}")
    return violations


def format_growth_simulation(result: dict) -> str:
    lines = [
        f"RPG 单人成长模拟：{int(result.get('runs', 0))} 条轨迹 / {int(result.get('days', 0))} 天",
        f"总体胜率：{float(result.get('win_rate', 0.0)):.1%}",
    ]
    for day, summary in result.get("checkpoints", {}).items():
        lines.append(
            f"{day} 天：Lv{summary['level_median']} "
            f"（P10 Lv{summary['level_p10']} / P90 Lv{summary['level_p90']}，中位经验 {summary['exp_median']}）"
        )
    level_30 = result.get("level_30", {})
    if level_30.get("reached_runs"):
        lines.append(
            f"Lv30 到达：中位第 {level_30['day_median']} 天 "
            f"（P10 第 {level_30['day_p10']} 天 / P90 第 {level_30['day_p90']} 天，"
            f"覆盖 {float(level_30.get('reach_rate', 0.0)):.1%} 轨迹）"
        )
    else:
        lines.append("Lv30 到达：当前模拟时长内尚无轨迹到达")
    violations = growth_baseline_violations(result)
    lines.append("成长基线：通过" if not violations else "成长基线：" + "；".join(violations))
    return "\n".join(lines)
