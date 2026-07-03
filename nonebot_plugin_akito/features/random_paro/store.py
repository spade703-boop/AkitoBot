"""random_paro 的数据存取与状态归一化。"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys


def _pkg():
    return sys.modules[__package__]


def _save():
    pkg = _pkg()
    path = pkg.find_data_path(pkg.DATA_FILE)
    if not path:
        path = pkg.get_data_dir() / pkg.DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(pkg.PARO_DATA, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _stats_path() -> Path:
    pkg = _pkg()
    path = pkg.find_data_path(pkg.STATS_FILE)
    if not path:
        path = pkg.get_data_dir() / pkg.STATS_FILE
    return path


def _egg_log_path() -> Path:
    pkg = _pkg()
    path = pkg.find_data_path(pkg.EGG_LOG_FILE)
    if not path:
        path = pkg.get_data_dir() / pkg.EGG_LOG_FILE
    return path


def _new_period_stats(*, date: str | None = None) -> dict:
    stats = {
        "total_draws": 0,
        "user_draw_counts": {},
        "akito_hits": {},
        "toya_hits": {},
        "akito_last_hit_seq": {},
        "toya_last_hit_seq": {},
        "egg_user_counts": {},
        "foxrabbit_total": 0,
        "foxbun_total": 0,
        "fox_total": 0,
        "rabbit_total": 0,
        "_seq": 0,
    }
    if date is not None:
        stats["date"] = date
    return stats


def _new_user_stats() -> dict:
    return {
        "draw_count": 0,
        "egg_count": 0,
        "foxbun_count": 0,
        "akito_hits": {},
        "toya_hits": {},
        "pair_hits": {},
        "akito_last_hit_seq": {},
        "toya_last_hit_seq": {},
        "pair_last_hit_seq": {},
        "_seq": 0,
    }


def _new_group_stats(today_str: str) -> dict:
    return {
        "profiles": {},
        "users": {},
        "daily": _new_period_stats(date=today_str),
        "history": _new_period_stats(),
    }


def _new_stats_state() -> dict:
    return {
        "schema_version": 2,
        "cooldowns": {},
        "groups": {},
    }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_counter(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        count = _safe_int(value)
        if count > 0:
            normalized[str(key)] = count
    return normalized


def _normalize_period_stats(raw: object, *, date: str | None = None) -> dict:
    stats = _new_period_stats(date=date)
    if not isinstance(raw, dict):
        return stats

    if date is not None and isinstance(raw.get("date"), str):
        stats["date"] = raw["date"]

    stats["total_draws"] = max(0, _safe_int(raw.get("total_draws")))
    stats["_seq"] = max(0, _safe_int(raw.get("_seq")))
    for key in (
        "user_draw_counts",
        "akito_hits",
        "toya_hits",
        "egg_user_counts",
        "akito_last_hit_seq",
        "toya_last_hit_seq",
    ):
        stats[key] = _normalize_counter(raw.get(key))
    for key in ("foxrabbit_total", "foxbun_total", "fox_total", "rabbit_total"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    return stats


def _normalize_user_stats(raw: object) -> dict:
    stats = _new_user_stats()
    if not isinstance(raw, dict):
        return stats

    for key in ("draw_count", "egg_count", "foxbun_count", "_seq"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    for key in (
        "akito_hits",
        "toya_hits",
        "pair_hits",
        "akito_last_hit_seq",
        "toya_last_hit_seq",
        "pair_last_hit_seq",
    ):
        stats[key] = _normalize_counter(raw.get(key))
    return stats


def _normalize_group_stats(raw: object, today_str: str) -> dict:
    stats = _new_group_stats(today_str)
    if not isinstance(raw, dict):
        return stats

    if isinstance(raw.get("profiles"), dict):
        stats["profiles"] = {
            str(user_id): str(display_name)
            for user_id, display_name in raw["profiles"].items()
            if str(display_name).strip()
        }
    if isinstance(raw.get("users"), dict):
        stats["users"] = {
            str(user_id): _normalize_user_stats(user_stats)
            for user_id, user_stats in raw["users"].items()
        }
    stats["daily"] = _normalize_period_stats(raw.get("daily"), date=today_str)
    stats["history"] = _normalize_period_stats(raw.get("history"))
    return stats


def _load_stats() -> dict:
    pkg = _pkg()
    path = _stats_path()
    if not path.exists():
        return _new_stats_state()

    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except Exception:
        pkg.logger.warning(f"读取 {pkg.STATS_FILE} 失败，已重置派生统计数据")
        return _new_stats_state()

    today_str = pkg.datetime.now(pkg.TZ_CN).date().isoformat()
    stats = _new_stats_state()
    if isinstance(raw, dict):
        stats["schema_version"] = max(2, _safe_int(raw.get("schema_version"), 2) or 2)

        raw_cooldowns = raw.get("cooldowns")
        if isinstance(raw_cooldowns, dict):
            cooldowns: dict[str, list[float]] = {}
            for user_id, history in raw_cooldowns.items():
                if not isinstance(history, list):
                    continue
                valid_history = []
                for timestamp in history:
                    try:
                        valid_history.append(float(timestamp))
                    except (TypeError, ValueError):
                        continue
                cooldowns[str(user_id)] = valid_history
            stats["cooldowns"] = cooldowns

        raw_groups = raw.get("groups")
        if isinstance(raw_groups, dict):
            stats["groups"] = {
                str(group_id): _normalize_group_stats(group_stats, today_str)
                for group_id, group_stats in raw_groups.items()
            }

    return stats


def _save_stats() -> None:
    pkg = _pkg()
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(pkg.PARO_STATS, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_egg_log(entry: dict) -> None:
    path = _egg_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def reload_paro_data() -> None:
    pkg = _pkg()
    pkg.PARO_DATA.clear()
    pkg.PARO_DATA.update(pkg.load_json_file(pkg.DATA_FILE, pkg.DEFAULT_DATA))
    pkg.PARO_STATS.clear()
    pkg.PARO_STATS.update(_load_stats())
    pkg.logger.info("🔄 派生池与排行榜数据已热重载")
