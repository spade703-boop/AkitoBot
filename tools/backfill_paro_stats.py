from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import shutil


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _normalize_counter(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        count = _safe_int(value)
        if count > 0:
            normalized[str(key)] = count
    return normalized


def _counter_to_sorted_dict(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _aggregate_user_counter(users: dict[str, object], field: str) -> Counter[str]:
    counter: Counter[str] = Counter()
    for user_stats in users.values():
        if not isinstance(user_stats, dict):
            continue
        counter.update(_normalize_counter(user_stats.get(field)))
    return counter


def _aggregate_user_draw_counts(users: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for user_id, user_stats in users.items():
        if not isinstance(user_stats, dict):
            continue
        draw_count = _safe_int(user_stats.get("draw_count"))
        if draw_count > 0:
            counts[str(user_id)] = draw_count
    return {user_id: counts[user_id] for user_id in sorted(counts)}


def _aggregate_egg_user_counts(users: dict[str, object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for user_id, user_stats in users.items():
        if not isinstance(user_stats, dict):
            continue
        total = _safe_int(user_stats.get("egg_count")) + _safe_int(user_stats.get("foxbun_count"))
        if total > 0:
            counts[str(user_id)] = total
    return {user_id: counts[user_id] for user_id in sorted(counts)}


def _aggregate_foxbun_total(users: dict[str, object]) -> int:
    total = 0
    for user_stats in users.values():
        if not isinstance(user_stats, dict):
            continue
        total += _safe_int(user_stats.get("foxbun_count"))
    return total


def _build_stable_order(counter: Counter[str], previous_order: object) -> dict[str, int]:
    old_order = _normalize_counter(previous_order)
    ordered_names = sorted(counter, key=lambda name: (old_order.get(name, 10**9), name))
    return {name: index for index, name in enumerate(ordered_names, 1)}


def _top_item(counter: dict[str, int]) -> tuple[str, int] | None:
    if not counter:
        return None
    return min(counter.items(), key=lambda item: (-item[1], item[0]))


def backfill_history_group(group_id: str, group_stats: object) -> dict[str, object]:
    if not isinstance(group_stats, dict):
        return {"group_id": group_id, "changed": False}

    users = group_stats.get("users")
    if not isinstance(users, dict):
        users = {}

    history = group_stats.get("history")
    if not isinstance(history, dict):
        history = {}
        group_stats["history"] = history

    old_akito_hits = _normalize_counter(history.get("akito_hits"))
    old_toya_hits = _normalize_counter(history.get("toya_hits"))
    old_user_draw_counts = _normalize_counter(history.get("user_draw_counts"))
    old_egg_user_counts = _normalize_counter(history.get("egg_user_counts"))
    old_total_draws = _safe_int(history.get("total_draws"))
    old_foxbun_total = _safe_int(history.get("foxbun_total"))

    new_akito_hits = _aggregate_user_counter(users, "akito_hits")
    new_toya_hits = _aggregate_user_counter(users, "toya_hits")
    new_user_draw_counts = _aggregate_user_draw_counts(users)
    new_egg_user_counts = _aggregate_egg_user_counts(users)
    new_total_draws = sum(new_user_draw_counts.values())
    new_foxbun_total = _aggregate_foxbun_total(users)

    new_akito_hits_dict = _counter_to_sorted_dict(new_akito_hits)
    new_toya_hits_dict = _counter_to_sorted_dict(new_toya_hits)
    new_akito_order = _build_stable_order(new_akito_hits, history.get("akito_last_hit_seq"))
    new_toya_order = _build_stable_order(new_toya_hits, history.get("toya_last_hit_seq"))

    changed = any(
        [
            old_akito_hits != new_akito_hits_dict,
            old_toya_hits != new_toya_hits_dict,
            old_user_draw_counts != new_user_draw_counts,
            old_egg_user_counts != new_egg_user_counts,
            old_total_draws != new_total_draws,
            old_foxbun_total != new_foxbun_total,
            _normalize_counter(history.get("akito_last_hit_seq")) != new_akito_order,
            _normalize_counter(history.get("toya_last_hit_seq")) != new_toya_order,
        ]
    )

    history["akito_hits"] = new_akito_hits_dict
    history["toya_hits"] = new_toya_hits_dict
    history["user_draw_counts"] = new_user_draw_counts
    history["egg_user_counts"] = new_egg_user_counts
    history["total_draws"] = new_total_draws
    history["foxbun_total"] = new_foxbun_total
    history["akito_last_hit_seq"] = new_akito_order
    history["toya_last_hit_seq"] = new_toya_order

    return {
        "group_id": group_id,
        "changed": changed,
        "old_top_akito": _top_item(old_akito_hits),
        "new_top_akito": _top_item(new_akito_hits_dict),
        "old_top_toya": _top_item(old_toya_hits),
        "new_top_toya": _top_item(new_toya_hits_dict),
        "old_total_draws": old_total_draws,
        "new_total_draws": new_total_draws,
    }


def backfill_paro_stats_file(path: Path, *, dry_run: bool = False, backup_suffix: str = ".bak") -> dict[str, object]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    groups = raw.get("groups")
    if not isinstance(groups, dict):
        raise ValueError("Invalid paro_stats.json: missing top-level groups object")

    summaries = []
    changed_groups = []
    for group_id, group_stats in groups.items():
        summary = backfill_history_group(str(group_id), group_stats)
        summaries.append(summary)
        if summary["changed"]:
            changed_groups.append(summary)

    backup_path: Path | None = None
    if changed_groups and not dry_run:
        backup_path = path.with_name(path.name + backup_suffix)
        shutil.copy2(path, backup_path)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(path)

    return {
        "path": path,
        "backup_path": backup_path,
        "groups_scanned": len(groups),
        "groups_changed": len(changed_groups),
        "changed_groups": changed_groups,
        "all_groups": summaries,
        "dry_run": dry_run,
    }


def _format_top(item: tuple[str, int] | None) -> str:
    if item is None:
        return "none"
    return f"{item[0]} {item[1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill random_paro history stats from per-user totals.")
    parser.add_argument(
        "path",
        nargs="?",
        default="data/paro_stats.json",
        help="Path to paro_stats.json (default: data/paro_stats.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Calculate changes without writing the file")
    args = parser.parse_args(argv)

    result = backfill_paro_stats_file(Path(args.path), dry_run=args.dry_run)
    print(f"Scanned {result['groups_scanned']} groups in {result['path']}")

    if not result["groups_changed"]:
        print("No groups needed history backfill.")
        return 0

    for summary in result["changed_groups"]:
        print(
            f"group {summary['group_id']}: "
            f"akito { _format_top(summary['old_top_akito']) } -> { _format_top(summary['new_top_akito']) }, "
            f"toya { _format_top(summary['old_top_toya']) } -> { _format_top(summary['new_top_toya']) }, "
            f"draws {summary['old_total_draws']} -> {summary['new_total_draws']}"
        )

    if result["dry_run"]:
        print("Dry run only; file was not changed.")
        return 0

    print(f"Updated {result['path']}")
    if result["backup_path"] is not None:
        print(f"Backup written to {result['backup_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
