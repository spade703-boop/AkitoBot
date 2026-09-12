"""random_paro 的数据存取与状态归一化。"""

from __future__ import annotations

from datetime import datetime
import json
import math
import os
from pathlib import Path

from nonebot.log import logger

from ...core import TZ_CN, find_data_path, get_data_dir, load_json_file

DATA_FILE = "paro_pools.json"
CONFIG_FILE = "paro_config.json"
STATS_FILE = "paro_stats.json"
EGG_LOG_FILE = "paro_egg_log.jsonl"
DEFAULT_DATA = {"akito_pool": [], "toya_pool": []}
DEFAULT_PARO_CONFIG = {
    "cooking_rate": 0.03,
    "special_rate": 0.08,
    "special_outcomes": [
        {
            "id": "fox",
            "label": "狐狸",
            "weight": 1,
            "tags": ["fox"],
            "assets": ["狐"],
            "message": "一只得意的狐狸赶走了这里的派生。",
            "counts_as_cooking": False,
            "legacy_stat": "fox_total",
        },
        {
            "id": "rabbit",
            "label": "兔子",
            "weight": 1,
            "tags": ["rabbit"],
            "assets": ["兔"],
            "message": "一只圆圆的兔子挡住了这里的派生。",
            "counts_as_cooking": False,
            "legacy_stat": "rabbit_total",
        },
        {
            "id": "foxrabbit",
            "label": "狐兔",
            "weight": 1,
            "tags": ["fox", "rabbit"],
            "assets": ["狐", "兔"],
            "message": "一对眼熟的狐兔出现在了这里……",
            "counts_as_cooking": False,
            "legacy_stat": "foxrabbit_total",
        },
        {
            "id": "foxbun",
            "label": "狐兔饭",
            "weight": 1,
            "tags": ["fox", "rabbit"],
            "assets": ["狐&兔"],
            "message": "发现了一对正在贴贴的狐兔！",
            "counts_as_cooking": True,
            "legacy_stat": "foxbun_total",
        },
    ],
}

PARO_DATA: dict = load_json_file(DATA_FILE, DEFAULT_DATA)
PARO_CONFIG: dict = {}
PARO_STATS: dict = {}


def _save():
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(PARO_DATA, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _stats_path() -> Path:
    path = find_data_path(STATS_FILE)
    if not path:
        path = get_data_dir() / STATS_FILE
    return path


def _config_path() -> Path:
    path = find_data_path(CONFIG_FILE)
    if not path:
        path = get_data_dir() / CONFIG_FILE
    return path


def _egg_log_path() -> Path:
    path = find_data_path(EGG_LOG_FILE)
    if not path:
        path = get_data_dir() / EGG_LOG_FILE
    return path


def _safe_rate(value: object, default: float) -> float:
    try:
        rate = float(value)
    except (TypeError, ValueError):
        return default
    return rate if 0 <= rate <= 1 else default


def _normalize_paro_config(raw: object) -> dict:
    config = {
        "cooking_rate": DEFAULT_PARO_CONFIG["cooking_rate"],
        "special_rate": DEFAULT_PARO_CONFIG["special_rate"],
        "special_outcomes": [],
    }
    if isinstance(raw, dict):
        config["cooking_rate"] = _safe_rate(raw.get("cooking_rate"), config["cooking_rate"])
        config["special_rate"] = _safe_rate(raw.get("special_rate"), config["special_rate"])
        raw_outcomes = raw.get("special_outcomes")
    else:
        raw_outcomes = None

    if not isinstance(raw_outcomes, list):
        raw_outcomes = DEFAULT_PARO_CONFIG["special_outcomes"]

    seen_ids: set[str] = set()
    for raw_outcome in raw_outcomes:
        if not isinstance(raw_outcome, dict):
            continue
        outcome_id = str(raw_outcome.get("id") or "").strip()
        if not outcome_id or outcome_id in seen_ids:
            continue
        try:
            weight = float(raw_outcome.get("weight", 0))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(weight) or weight <= 0:
            continue
        tags = raw_outcome.get("tags")
        assets = raw_outcome.get("assets")
        tags = [str(tag).strip() for tag in tags if str(tag).strip()] if isinstance(tags, list) else []
        assets = [str(asset).strip() for asset in assets if str(asset).strip()] if isinstance(assets, list) else []
        if not tags:
            continue
        outcome = {
            "id": outcome_id,
            "label": str(raw_outcome.get("label") or outcome_id),
            "weight": weight,
            "tags": tags,
            "assets": assets,
            "message": str(raw_outcome.get("message") or raw_outcome.get("label") or outcome_id),
            "counts_as_cooking": bool(raw_outcome.get("counts_as_cooking", False)),
        }
        legacy_stat = raw_outcome.get("legacy_stat")
        if isinstance(legacy_stat, str) and legacy_stat.strip():
            outcome["legacy_stat"] = legacy_stat.strip()
        config["special_outcomes"].append(outcome)
        seen_ids.add(outcome_id)

    if not config["special_outcomes"]:
        config["special_outcomes"] = [dict(item) for item in DEFAULT_PARO_CONFIG["special_outcomes"]]
    return config


def _special_outcome_map() -> dict[str, dict]:
    return {item["id"]: item for item in PARO_CONFIG.get("special_outcomes", []) if isinstance(item, dict)}


def _special_outcome(special_type: str | None) -> dict | None:
    if not special_type:
        return None
    return _special_outcome_map().get(special_type)


def _new_period_stats(*, date: str | None = None) -> dict:
    stats = {
        "total_draws": 0,
        "user_draw_counts": {},
        "akito_hits": {},
        "toya_hits": {},
        "akito_last_hit_seq": {},
        "toya_last_hit_seq": {},
        "egg_user_counts": {},
        "special_outcomes": {},
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
        "special_outcomes": {},
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
        "special_outcomes",
        "akito_last_hit_seq",
        "toya_last_hit_seq",
    ):
        stats[key] = _normalize_counter(raw.get(key))
    for key in ("foxrabbit_total", "foxbun_total", "fox_total", "rabbit_total"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    legacy_map = {
        "foxrabbit": "foxrabbit_total",
        "foxbun": "foxbun_total",
        "fox": "fox_total",
        "rabbit": "rabbit_total",
    }
    for special_type, legacy_key in legacy_map.items():
        legacy_count = stats[legacy_key]
        if legacy_count > stats["special_outcomes"].get(special_type, 0):
            stats["special_outcomes"][special_type] = legacy_count
    return stats


def _normalize_user_stats(raw: object) -> dict:
    stats = _new_user_stats()
    if not isinstance(raw, dict):
        return stats

    for key in ("draw_count", "egg_count", "foxbun_count", "_seq"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    stats["special_outcomes"] = _normalize_counter(raw.get("special_outcomes"))
    if stats["foxbun_count"] > stats["special_outcomes"].get("foxbun", 0):
        stats["special_outcomes"]["foxbun"] = stats["foxbun_count"]
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


def _bump_counter(counter: dict[str, int], key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    counter[key] = counter.get(key, 0) + amount


def _rebuild_history_counters_from_users(history: dict, users: dict[str, dict]) -> None:
    akito_hits: dict[str, int] = {}
    toya_hits: dict[str, int] = {}
    user_draw_counts: dict[str, int] = {}
    egg_user_counts: dict[str, int] = {}
    foxbun_total = 0
    existing_special_outcomes = _normalize_counter(history.get("special_outcomes"))
    user_special_outcomes: dict[str, int] = {}

    for user_id, user_stats in users.items():
        draw_count = max(0, _safe_int(user_stats.get("draw_count")))
        if draw_count > 0:
            user_draw_counts[user_id] = draw_count

        egg_count = max(0, _safe_int(user_stats.get("egg_count")))
        foxbun_count = max(0, _safe_int(user_stats.get("foxbun_count")))
        cooking_special_count = 0
        for special_type, count in user_stats.get("special_outcomes", {}).items():
            outcome = _special_outcome(str(special_type))
            if outcome and outcome.get("counts_as_cooking") and str(special_type) != "foxbun":
                cooking_special_count += max(0, _safe_int(count))
        if egg_count or foxbun_count or cooking_special_count:
            egg_user_counts[user_id] = egg_count + foxbun_count + cooking_special_count
        foxbun_total += foxbun_count
        for special_type, count in user_stats.get("special_outcomes", {}).items():
            special_type = str(special_type)
            _bump_counter(user_special_outcomes, special_type, max(0, _safe_int(count)))

        for key, count in user_stats.get("akito_hits", {}).items():
            _bump_counter(akito_hits, key, max(0, _safe_int(count)))
        for key, count in user_stats.get("toya_hits", {}).items():
            _bump_counter(toya_hits, key, max(0, _safe_int(count)))

    old_akito_order = _normalize_counter(history.get("akito_last_hit_seq"))
    old_toya_order = _normalize_counter(history.get("toya_last_hit_seq"))
    akito_names = sorted(akito_hits, key=lambda name: (old_akito_order.get(name, 10**9), name))
    toya_names = sorted(toya_hits, key=lambda name: (old_toya_order.get(name, 10**9), name))

    history["akito_hits"] = {key: akito_hits[key] for key in sorted(akito_hits)}
    history["toya_hits"] = {key: toya_hits[key] for key in sorted(toya_hits)}
    history["user_draw_counts"] = {key: user_draw_counts[key] for key in sorted(user_draw_counts)}
    history["egg_user_counts"] = {key: egg_user_counts[key] for key in sorted(egg_user_counts)}
    history["total_draws"] = sum(user_draw_counts.values())
    history["foxbun_total"] = foxbun_total
    special_outcomes = dict(user_special_outcomes)
    for special_type, count in existing_special_outcomes.items():
        if special_type not in user_special_outcomes:
            special_outcomes[special_type] = count
    for special_type, legacy_key in {
        "foxrabbit": "foxrabbit_total",
        "foxbun": "foxbun_total",
        "fox": "fox_total",
        "rabbit": "rabbit_total",
    }.items():
        if special_type not in special_outcomes:
            special_outcomes[special_type] = max(0, _safe_int(history.get(legacy_key)))
    history["special_outcomes"] = {key: value for key, value in special_outcomes.items() if value > 0}
    history["foxrabbit_total"] = special_outcomes.get("foxrabbit", 0)
    history["fox_total"] = special_outcomes.get("fox", 0)
    history["rabbit_total"] = special_outcomes.get("rabbit", 0)
    history["foxbun_total"] = special_outcomes.get("foxbun", foxbun_total)
    history["akito_last_hit_seq"] = {name: index for index, name in enumerate(akito_names, 1)}
    history["toya_last_hit_seq"] = {name: index for index, name in enumerate(toya_names, 1)}


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
            str(user_id): _normalize_user_stats(user_stats) for user_id, user_stats in raw["users"].items()
        }
    stats["daily"] = _normalize_period_stats(raw.get("daily"), date=today_str)
    stats["history"] = _normalize_period_stats(raw.get("history"))
    if stats["users"]:
        _rebuild_history_counters_from_users(stats["history"], stats["users"])
    return stats


def _load_stats() -> dict:
    path = _stats_path()
    if not path.exists():
        return _new_stats_state()

    try:
        with open(path, encoding="utf-8") as file:
            raw = json.load(file)
    except Exception:
        logger.warning(f"读取 {STATS_FILE} 失败，已重置派生统计数据")
        return _new_stats_state()

    today_str = datetime.now(TZ_CN).date().isoformat()
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
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as file:
        json.dump(PARO_STATS, file, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_egg_log(entry: dict) -> None:
    path = _egg_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as file:
        file.write(json.dumps(entry, ensure_ascii=False) + "\n")


def reload_paro_data() -> None:
    PARO_DATA.clear()
    PARO_DATA.update(load_json_file(DATA_FILE, DEFAULT_DATA))
    PARO_CONFIG.clear()
    PARO_CONFIG.update(_normalize_paro_config(load_json_file(CONFIG_FILE, DEFAULT_PARO_CONFIG)))
    PARO_STATS.clear()
    PARO_STATS.update(_load_stats())
    logger.info("🔄 派生池与排行榜数据已热重载")


PARO_STATS.update(_load_stats())
PARO_CONFIG.update(_normalize_paro_config(load_json_file(CONFIG_FILE, DEFAULT_PARO_CONFIG)))
