"""Build a deterministic, evidence-preserving Akito/Toya event inventory."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

TOYA_ALIASES = ("冬弥", "青柳", "Toya", "トウヤ")
AKITO_ALIASES = ("彰人", "东云", "東雲", "Akito", "アキト")
TARGET_CATEGORY = "冬弥·彰冬"


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"\s+", " ", text)


def contains_alias(text: str, aliases: tuple[str, ...]) -> bool:
    return any(alias.lower() in text for alias in aliases)


def stable_event_id(title: str, category: str) -> str:
    key = f"{normalize_text(title)}\n{normalize_text(category)}".encode()
    return f"akito-toya-{hashlib.sha1(key).hexdigest()[:12]}"


def confidence_for(rows: list[dict[str, Any]], category: str) -> str:
    combined = " ".join(
        normalize_text(row.get(field, ""))
        for row in rows
        for field in ("context", "cn_key", "dialogue")
    )
    has_both = contains_alias(combined, TOYA_ALIASES) and contains_alias(combined, AKITO_ALIASES)
    if category == TARGET_CATEGORY and any(row.get("type") == "story" for row in rows) and has_both:
        return "high"
    if category == TARGET_CATEGORY or has_both:
        return "medium"
    return "low"


def build_inventory(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    candidate_records = 0
    for index, row in enumerate(records):
        if not isinstance(row, dict):
            continue
        searchable = " ".join(normalize_text(row.get(field, "")) for field in ("context", "cn_key", "dialogue"))
        category = str(row.get("category") or "").strip()
        if not contains_alias(searchable, TOYA_ALIASES) and category != TARGET_CATEGORY:
            continue
        candidate_records += 1
        title = str(row.get("cn_key") or row.get("context") or f"未命名事件 {index}").strip()
        key = (normalize_text(title), normalize_text(category))
        evidence = dict(row)
        evidence["record_index"] = index
        grouped[key].append(evidence)

    events: list[dict[str, Any]] = []
    for (normalized_title, normalized_category), rows in grouped.items():
        del normalized_title, normalized_category
        first = rows[0]
        title = str(first.get("cn_key") or first.get("context") or "未命名事件").strip()
        category = str(first.get("category") or "未分类").strip()
        context_values = [str(row.get("context") or "").strip() for row in rows if str(row.get("context") or "").strip()]
        topics: list[str] = []
        for row in rows:
            value = row.get("topics")
            if isinstance(value, list):
                topics.extend(str(item).strip() for item in value if str(item).strip())
        unique_topics = list(dict.fromkeys(topics))
        searchable_summary = normalize_text(" ".join([title, *context_values, *unique_topics]))
        relationship_tags = [
            tag
            for marker, tag in (
                ("练习", "练习"),
                ("演出", "演出"),
                ("生日", "庆祝"),
                ("庆生", "庆祝"),
                ("承诺", "承诺"),
                ("提醒", "照护"),
                ("聚餐", "日常"),
                ("唱歌", "共同音乐"),
            )
            if marker in searchable_summary
        ]
        record_indices = [int(row.get("record_index", -1)) for row in rows]
        events.append(
            {
                "event_id": stable_event_id(title, category),
                "source": {"path": "data/content/akito_scripts.json", "record_indices": record_indices},
                "title": title,
                "summary": context_values[0] if context_values else title,
                "category": category,
                "topics": unique_topics,
                "confidence": confidence_for(rows, category),
                "entities": ["akito", "toya"] if category == TARGET_CATEGORY else ["toya"],
                "participants": ["彰人", "冬弥"] if category == TARGET_CATEGORY else ["冬弥"],
                "relationship_tags": list(dict.fromkeys(relationship_tags)),
                "timeline": [],
                "locations": [],
                "evidence": [
                    {
                        "record_index": int(row.get("record_index", -1)),
                        "type": str(row.get("type") or ""),
                        "context": str(row.get("context") or ""),
                        "dialogue": str(row.get("dialogue") or ""),
                    }
                    for row in rows
                ],
                "keywords": list(
                    dict.fromkeys(
                        [title, *context_values[:2], category, *unique_topics, "冬弥", "彰人"]
                    )
                ),
            }
        )

    events.sort(key=lambda event: event["event_id"])
    confidence_counts = Counter(event["confidence"] for event in events)
    return {
        "schema_version": 1,
        "source": {"path": "data/content/akito_scripts.json", "record_count": len(records)},
        "selection": {
            "candidate_rule": "冬弥/青柳/Toya 相关记录或分类为冬弥·彰冬；按 cn_key+category 聚类",
            "candidate_records": candidate_records,
            "event_count": len(events),
            "confidence_counts": dict(sorted(confidence_counts.items())),
        },
        "events": events,
    }


def validate_inventory(inventory: dict[str, Any]) -> list[str]:
    """Validate evidence and attribution constraints before an asset is published."""
    errors: list[str] = []
    events = inventory.get("events")
    if not isinstance(events, list) or not events:
        return ["events 必须是非空数组"]
    ids: set[str] = set()
    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        if not isinstance(event, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in ids:
            errors.append(f"{prefix}.event_id 缺失或重复")
        ids.add(event_id)
        evidence = event.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{prefix}.evidence 必须保留至少一条原始证据")
            continue
        if event.get("confidence") == "high":
            if not str(event.get("title") or "").strip() or not str(event.get("summary") or "").strip():
                errors.append(f"{prefix} 高置信度事件缺少明确概括")
            if not any(str(row.get("context") or "").strip() and str(row.get("dialogue") or "").strip() for row in evidence if isinstance(row, dict)):
                errors.append(f"{prefix} 高置信度事件缺少情境与台词证据")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="生成彰人/冬弥事件记忆资产")
    parser.add_argument("--source", default="data/content/akito_scripts.json")
    parser.add_argument("--output", default="data/content/akito_event_memories.json")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source_path = root / args.source
    output_path = root / args.output
    records = json.loads(source_path.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("source must be a JSON array")
    inventory = build_inventory(records)
    errors = validate_inventory(inventory)
    if errors:
        raise ValueError("事件资产校验失败: " + "; ".join(errors[:5]))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    selection = inventory["selection"]
    print(
        f"事件资产已生成: {selection['event_count']} events / "
        f"{selection['candidate_records']} records / {selection['confidence_counts']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
