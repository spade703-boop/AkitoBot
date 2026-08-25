"""Offline safety audit for the generated event-memory inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any
import unicodedata

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
MIN_STRONG_SCORE = 4.5
MIN_ISOLATED_SCORE = 3.0
MIN_SCORE_MARGIN = 1.0
GENERIC_TERMS = {"彰人", "冬弥", "青柳", "东云", "東雲", "toya", "akito"}
QUERY_STOP_PHRASES = (
    "你还记得", "还记得", "你们以前", "以前", "那一次", "那次", "那天", "上次",
    "那个事情", "那个事", "后来", "当时", "发生过什么", "发生了什么", "发生过",
    "怎么样了", "怎么样", "怎么说的", "怎么说", "说说", "是不是", "有没有", "什么",
    "青柳冬弥", "东云彰人", "東雲彰人", "冬弥", "彰人", "青柳", "东云", "東雲",
    "你们", "你自己", "自己", "你", "他", "的", "了", "吗", "吧", "呢", "挺", "有点",
)
QUERY_ALIASES = (
    ("庆生", "生日"), ("庆祝", "生日"), ("生日歌", "生日唱歌"), ("吃饭", "聚餐"),
    ("一起吃饭", "聚餐"), ("闹过头", "张扬"), ("别闹", "张扬"), ("努力学习", "学习"),
)
SIGNAL_TERMS = (
    "生日", "惊喜", "甜食", "唱歌", "胜负", "切蛋糕", "聚餐", "学校", "热闹", "规则",
    "雪仗", "提醒", "学习", "感谢", "配合", "开心", "祝福", "讨论", "努力", "聚会",
    "清晨", "很早", "早上", "sekai", "rad blast",
)


def normalize(value: object) -> str:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).lower())
    for source, target in QUERY_ALIASES:
        text = text.replace(source, target)
    return text


def ngrams(value: object) -> set[str]:
    text = normalize(value)
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))
    for match in re.findall(r"[\u4e00-\u9fff]+", text):
        for size in (2, 3, 4):
            terms.update(match[index : index + size] for index in range(max(0, len(match) - size + 1)))
    return {term for term in terms if term and term not in GENERIC_TERMS}


def has_specific_event_cue(query: str) -> bool:
    cue_text = normalize(query)
    for phrase in QUERY_STOP_PHRASES:
        cue_text = cue_text.replace(phrase, "")
    latin_terms = re.findall(r"[a-z0-9]{2,}", cue_text)
    chinese_text = "".join(re.findall(r"[\u4e00-\u9fff]+", cue_text))
    return bool(latin_terms or len(chinese_text) >= 2)


def signal_cue_count(query: str) -> int:
    query_text = normalize(query)
    return sum(term in query_text for term in SIGNAL_TERMS)


def score(query: str, event: dict[str, Any]) -> float:
    query_text = normalize(query)
    query_terms = ngrams(query)
    title = normalize(event.get("title"))
    values = [title, normalize(event.get("summary")), normalize(event.get("category"))]
    values.extend(normalize(item) for item in event.get("topics", []) if str(item).strip())
    values.extend(normalize(item) for item in event.get("keywords", []) if str(item).strip())
    result = 0.0
    for value in dict.fromkeys(item for item in values if item):
        if len(value) >= 2 and value in query_text:
            result += 3.0 if value == title else 1.5
        result += min(3.0, len(ngrams(value) & query_terms) * 0.5)
        result += 2.0 * sum(term in query_text and term in value for term in SIGNAL_TERMS)
    return round(result, 3)


def retrieve(
    query: str,
    events: list[dict[str, Any]],
    *,
    top_k: int = 3,
    min_confidence: str = "high",
) -> dict[str, Any]:
    if not str(query or "").strip():
        return {"status": "no_hit", "reason": "empty_query", "hits": [], "scored": []}
    if not has_specific_event_cue(query):
        return {"status": "no_hit", "reason": "insufficient_event_cues", "hits": [], "scored": []}
    minimum = CONFIDENCE_ORDER.get(min_confidence, CONFIDENCE_ORDER["high"])
    scored = [
        (score(query, event), event)
        for event in events
        if CONFIDENCE_ORDER.get(str(event.get("confidence") or "low"), 0) >= minimum
    ]
    scored = [(value, event) for value, event in scored if value > 0]
    scored.sort(key=lambda item: (-item[0], str(item[1].get("event_id"))))
    if not scored:
        return {"status": "no_hit", "reason": "no_relevant_event", "hits": [], "scored": []}
    if scored[0][0] < MIN_ISOLATED_SCORE:
        return {"status": "no_hit", "reason": "low_score", "hits": [], "scored": scored}
    score_margin = scored[0][0] - scored[1][0] if len(scored) > 1 else scored[0][0]
    if (
        scored[0][0] < MIN_STRONG_SCORE
        and score_margin < MIN_SCORE_MARGIN
        and signal_cue_count(query) < 2
    ):
        return {"status": "no_hit", "reason": "ambiguous_candidates", "hits": [], "scored": scored}
    return {"status": "hit", "reason": "", "hits": scored[:top_k], "scored": scored}


def evaluate(eval_set: dict[str, Any], asset: dict[str, Any], top_k: int = 3) -> dict[str, Any]:
    events = [event for event in asset.get("events", []) if isinstance(event, dict)]
    cases = [case for case in eval_set.get("cases", []) if isinstance(case, dict)]
    rows: list[dict[str, Any]] = []
    positive_ranks: list[int | None] = []
    negative_hits = 0
    ambiguous_abstentions = 0
    for case in cases:
        result = retrieve(str(case.get("query", "")), events, top_k=max(3, top_k))
        expected = {str(item) for item in case.get("expected_event_ids", [])}
        event_ids = [str(event.get("event_id")) for _, event in result["hits"]]
        rank = next((index for index, event_id in enumerate(event_ids, 1) if event_id in expected), None)
        kind = str(case.get("kind") or "positive")
        if kind == "positive":
            positive_ranks.append(rank)
        elif kind == "negative":
            negative_hits += result["status"] == "hit"
        elif kind == "ambiguous":
            ambiguous_abstentions += result["status"] == "no_hit"
        scored = result["scored"]
        rows.append(
            {
                "id": case.get("id"), "kind": kind, "status": result["status"], "reason": result["reason"],
                "matched": rank is not None if kind == "positive" else result["status"] == "no_hit",
                "rank": rank, "event_ids": event_ids[:top_k], "expected_event_ids": sorted(expected),
                "top_score": scored[0][0] if scored else 0.0,
                "second_score": scored[1][0] if len(scored) > 1 else 0.0,
            }
        )
    positive_total = len(positive_ranks)
    negative_total = sum(case.get("kind") == "negative" for case in cases)
    ambiguous_total = sum(case.get("kind") == "ambiguous" for case in cases)
    negative_false_positive_rate = negative_hits / negative_total if negative_total else 0.0
    return {
        "dataset_cases": len(cases), "event_count": len(events),
        "high_confidence_events": sum(event.get("confidence") == "high" for event in events),
        "positive_cases": positive_total, "negative_cases": negative_total, "ambiguous_cases": ambiguous_total,
        "recall_at_1": round(sum(rank == 1 for rank in positive_ranks) / positive_total, 4) if positive_total else 0.0,
        "recall_at_3": round(sum(rank is not None and rank <= 3 for rank in positive_ranks) / positive_total, 4) if positive_total else 0.0,
        "mrr": round(sum(1 / rank for rank in positive_ranks if rank) / positive_total, 4) if positive_total else 0.0,
        "false_positive_rate": round(negative_false_positive_rate, 4),
        "specificity": round(1 - negative_false_positive_rate, 4) if negative_total else 0.0,
        "ambiguous_abstention_rate": round(ambiguous_abstentions / ambiguous_total, 4) if ambiguous_total else 0.0,
        "cases": rows,
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# M2 事件记忆离线召回报告", "",
        "> 该报告评估事件候选召回和安全拒绝，不代表线上模型回复质量；原作事件是证据锚点，不是逐字答案。", "",
        f"- 事件总数：{report['event_count']}", f"- 高置信度事件：{report['high_confidence_events']}",
        f"- 评测样例：{report['dataset_cases']}（正例 {report['positive_cases']} / 负例 {report['negative_cases']} / 模糊例 {report['ambiguous_cases']}）",
        f"- Recall@1：{report['recall_at_1']}", f"- Recall@3：{report['recall_at_3']}", f"- MRR：{report['mrr']}",
        f"- 负例误认率：{report['false_positive_rate']}", f"- 负例特异度：{report['specificity']}",
        f"- 模糊问法拒绝率：{report['ambiguous_abstention_rate']}", "",
        "| Case | Kind | Result | Reason | Top / second | Retrieved event ids |", "| --- | --- | --- | --- | --- | --- |",
    ]
    for case in report["cases"]:
        result = "pass" if case["matched"] else "fail"
        event_ids = ", ".join(case["event_ids"]) or "（无）"
        lines.append(
            f"| {case['id']} | {case['kind']} | {result} | {case['reason'] or '-'} | "
            f"{case['top_score']} / {case['second_score']} | {event_ids} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="评估 M2 事件记忆召回与安全拒绝")
    parser.add_argument("--eval-set", default="tools/event_memory_eval_set.json")
    parser.add_argument("--asset", default="data/content/akito_event_memories.json")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", default="docs/M2_EVENT_RECALL.md")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    eval_set = json.loads((root / args.eval_set).read_text(encoding="utf-8"))
    asset = json.loads((root / args.asset).read_text(encoding="utf-8"))
    report = evaluate(eval_set, asset, top_k=max(1, args.top_k))
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_report(report), encoding="utf-8")
    summary_keys = (
        "dataset_cases", "recall_at_1", "recall_at_3", "mrr",
        "false_positive_rate", "ambiguous_abstention_rate",
    )
    print(json.dumps({key: report[key] for key in summary_keys}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
