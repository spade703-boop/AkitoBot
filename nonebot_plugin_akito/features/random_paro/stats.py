"""random_paro 的抽取统计与排行构建。"""

from __future__ import annotations

import json
import sys


def _pkg():
    return sys.modules[__package__]


def _today_str() -> str:
    pkg = _pkg()
    return pkg.datetime.now(pkg.TZ_CN).date().isoformat()


def _cooldown_store() -> dict[str, list[float]]:
    cooldowns = _pkg().PARO_STATS.setdefault("cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        _pkg().PARO_STATS["cooldowns"] = cooldowns
    return cooldowns


def _bump_counter(counter: dict[str, int], key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    counter[key] = counter.get(key, 0) + amount


_PAIR_KEY_SEPARATOR = "|||"


def _make_pair_key(akito_name: str, toya_name: str) -> str:
    return f"{akito_name}{_PAIR_KEY_SEPARATOR}{toya_name}"


def _split_pair_key(pair_key: str) -> tuple[str, str]:
    parts = pair_key.split(_PAIR_KEY_SEPARATOR, 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return pair_key, ""


def _record_period_hit(period_stats: dict, counter_key: str, order_key: str, name: str) -> None:
    period_stats["_seq"] += 1
    _bump_counter(period_stats[counter_key], name)
    period_stats[order_key][name] = period_stats["_seq"]


def _record_user_hit(user_stats: dict, counter_key: str, order_key: str, name: str) -> None:
    user_stats["_seq"] += 1
    _bump_counter(user_stats[counter_key], name)
    user_stats[order_key][name] = user_stats["_seq"]


def _record_user_draw_stats(
    user_stats: dict,
    *,
    results: list[tuple[str, str, bool, str | None]],
) -> None:
    user_stats["draw_count"] += len(results)
    for akito_name, toya_name, is_egg, fox_type in results:
        if fox_type is None:
            _record_user_hit(user_stats, "akito_hits", "akito_last_hit_seq", akito_name)
            _record_user_hit(user_stats, "toya_hits", "toya_last_hit_seq", toya_name)
            pair_key = _make_pair_key(akito_name, toya_name)
            _record_user_hit(user_stats, "pair_hits", "pair_last_hit_seq", pair_key)
        if is_egg:
            user_stats["egg_count"] += 1
        if fox_type == "foxbun":
            user_stats["foxbun_count"] += 1


def _get_fixed_side(fixed_a: str | None, fixed_b: str | None) -> str | None:
    if fixed_a and not fixed_b:
        return "akito"
    if fixed_b and not fixed_a:
        return "toya"
    return None


def _roll_daily_stats(group_stats: dict, today_str: str) -> bool:
    daily_stats = group_stats.get("daily")
    if isinstance(daily_stats, dict) and daily_stats.get("date") == today_str:
        from .store import _normalize_period_stats

        group_stats["daily"] = _normalize_period_stats(daily_stats, date=today_str)
        return False
    group_stats["daily"] = _pkg()._new_period_stats(date=today_str)
    return True


def _get_or_create_group_stats(group_id: str, today_str: str) -> tuple[dict, bool]:
    pkg = _pkg()
    groups = pkg.PARO_STATS.setdefault("groups", {})
    group_stats = groups.get(group_id)
    if not isinstance(group_stats, dict):
        group_stats = pkg._new_group_stats(today_str)
        groups[group_id] = group_stats
        return group_stats, True

    normalized = pkg._normalize_group_stats(group_stats, today_str)
    normalized_changed = normalized != group_stats
    groups[group_id] = normalized
    rolled = _roll_daily_stats(normalized, today_str)
    return normalized, normalized_changed or rolled


def _record_draw_stats_for_period(
    period_stats: dict,
    *,
    user_id: str,
    results: list[tuple[str, str, bool, str | None]],
    fixed_side: str | None,
) -> None:
    draw_count = len(results)
    period_stats["total_draws"] += draw_count
    _bump_counter(period_stats["user_draw_counts"], user_id, draw_count)

    for akito_name, toya_name, is_egg, fox_type in results:
        if fox_type is None:
            # Keep group rankings aligned with personal stats: directional draws
            # still count as seeing the fixed-side paro in the final result.
            _record_period_hit(period_stats, "akito_hits", "akito_last_hit_seq", akito_name)
            _record_period_hit(period_stats, "toya_hits", "toya_last_hit_seq", toya_name)

        if is_egg or fox_type == "foxbun":
            _bump_counter(period_stats["egg_user_counts"], user_id)

        if fox_type == "foxrabbit":
            period_stats["foxrabbit_total"] += 1
        elif fox_type == "foxbun":
            period_stats["foxbun_total"] += 1
        elif fox_type == "fox":
            period_stats["fox_total"] += 1
        elif fox_type == "rabbit":
            period_stats["rabbit_total"] += 1


def _record_group_draw_stats(
    *,
    group_id: int,
    user_id: str,
    display_name: str,
    results: list[tuple[str, str, bool, str | None]],
    fixed_side: str | None,
    fixed_name: str | None,
    requested_count: int,
    now_ts: float,
) -> None:
    pkg = _pkg()
    today_str = _today_str()
    group_stats, _rolled = _get_or_create_group_stats(str(group_id), today_str)
    group_stats["profiles"][user_id] = display_name
    user_stats = pkg._normalize_user_stats(group_stats["users"].get(user_id))
    group_stats["users"][user_id] = user_stats

    _record_draw_stats_for_period(
        group_stats["daily"],
        user_id=user_id,
        results=results,
        fixed_side=fixed_side,
    )
    _record_draw_stats_for_period(
        group_stats["history"],
        user_id=user_id,
        results=results,
        fixed_side=fixed_side,
    )
    _record_user_draw_stats(user_stats, results=results)

    for index, (akito_name, toya_name, is_egg, fox_type) in enumerate(results, 1):
        if not is_egg and fox_type != "foxbun":
            continue
        pkg._append_egg_log(
            {
                "ts": now_ts,
                "date": today_str,
                "group_id": str(group_id),
                "user_id": user_id,
                "display_name": display_name,
                "egg_type": "cooking" if is_egg else "foxbun",
                "akito": akito_name,
                "toya": toya_name,
                "draw_index": index,
                "requested_count": requested_count,
                "fixed_side": fixed_side,
                "fixed_name": fixed_name,
            }
        )

    pkg._save_stats()


def _sorted_counter_items(counter: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def _build_user_rows(counter: dict[str, int], profiles: dict[str, str], *, limit: int = 5) -> list[dict]:
    rows = []
    for index, (user_id, count) in enumerate(_sorted_counter_items(counter)[:limit], 1):
        display_name = profiles.get(user_id) or f"用户{user_id}"
        rows.append({"left": f"{index}. {display_name}", "right": f"{count}次"})
    if not rows:
        rows.append({"left": "暂无", "right": "0次"})
    return rows


def _sorted_ranked_items(counter: dict[str, int], last_hit_seq: dict[str, int] | None = None) -> list[tuple[str, int]]:
    if last_hit_seq is None:
        last_hit_seq = {}
    return sorted(counter.items(), key=lambda item: (-item[1], last_hit_seq.get(item[0], 10**9), item[0]))


def _build_character_rows(
    counter: dict[str, int],
    *,
    limit: int = 3,
    character: str,
    last_hit_seq: dict[str, int] | None = None,
) -> list[dict]:
    grouped: list[tuple[list[str], int]] = []
    for name, count in _sorted_ranked_items(counter, last_hit_seq):
        if grouped and grouped[-1][1] == count:
            grouped[-1][0].append(name)
        else:
            grouped.append(([name], count))

    rows = []
    for rank, (names, count) in enumerate(grouped[:limit], 1):
        visible_names = names[:3]
        display_text = " / ".join(visible_names)
        if len(names) > 3:
            display_text += " / ..."
        rows.append(
            {
                "left": f"TOP{rank} {display_text}",
                "right": f"{count}次",
                "suffix_avatar_names": visible_names,
                "suffix_character": character,
            }
        )
    if not rows:
        rows.append({"left": "暂无", "right": "0次"})
    return rows


def _build_fox_rows(period_stats: dict) -> list[dict]:
    entries = [
        ("foxrabbit", "狐兔", period_stats["foxrabbit_total"]),
        ("foxbun", "狐兔饭", period_stats["foxbun_total"]),
        ("fox", "狐狸", period_stats["fox_total"]),
        ("rabbit", "兔子", period_stats["rabbit_total"]),
    ]
    rows = []
    for _idx, (fox_type, label, count) in sorted(
        enumerate(entries),
        key=lambda item: (-item[1][2], item[0]),
    ):
        rows.append({"left": label, "right": f"{count}次", "icon_kind": "fox", "fox_type": fox_type})
    return rows


def _new_user_egg_history() -> dict:
    return {
        "cooking_count": 0,
        "foxbun_count": 0,
        "cooking_pair_hits": {},
        "cooking_pair_last_hit_seq": {},
        "_seq": 0,
    }


def _record_user_egg_history_entry(
    egg_history: dict,
    *,
    akito_name: str,
    toya_name: str,
    egg_type: str,
) -> None:
    if egg_type == "cooking":
        egg_history["cooking_count"] += 1
        pair_key = _make_pair_key(akito_name, toya_name)
        egg_history["_seq"] += 1
        _bump_counter(egg_history["cooking_pair_hits"], pair_key)
        egg_history["cooking_pair_last_hit_seq"][pair_key] = egg_history["_seq"]
        return
    if egg_type == "foxbun":
        egg_history["foxbun_count"] += 1


def _collect_user_egg_history(group_id: int, user_id: str) -> dict:
    pkg = _pkg()
    egg_history = _new_user_egg_history()
    path = pkg._egg_log_path()
    if not path.exists():
        return egg_history

    target_group_id = str(group_id)
    target_user_id = str(user_id)
    try:
        with open(path, encoding="utf-8") as file:
            for line in file:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if str(entry.get("group_id")) != target_group_id or str(entry.get("user_id")) != target_user_id:
                    continue
                egg_type = str(entry.get("egg_type") or "")
                if egg_type not in {"cooking", "foxbun"}:
                    continue
                _record_user_egg_history_entry(
                    egg_history,
                    akito_name=str(entry.get("akito") or ""),
                    toya_name=str(entry.get("toya") or ""),
                    egg_type=egg_type,
                )
    except Exception:
        pkg.logger.warning(f"读取 {pkg.EGG_LOG_FILE} 失败，无法构建个人做饭彩蛋历史")
    return egg_history


def _build_personal_cooking_pair_items(egg_history: dict) -> list[dict]:
    items = []
    for pair_key, count in _sorted_ranked_items(
        egg_history["cooking_pair_hits"],
        egg_history.get("cooking_pair_last_hit_seq"),
    ):
        akito_name, toya_name = _split_pair_key(pair_key)
        items.append(
            {
                "pair_key": pair_key,
                "akito_name": akito_name,
                "toya_name": toya_name,
                "count": count,
            }
        )
    return items


def _count_total_cooking_hits(egg_history: dict) -> int:
    return int(egg_history.get("cooking_count", 0)) + int(egg_history.get("foxbun_count", 0))
