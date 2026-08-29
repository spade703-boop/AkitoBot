"""Offline safety audit for the generated event-memory inventory."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
_SCORING_PATH = ROOT / "nonebot_plugin_akito" / "core" / "event_memory_scoring.py"
_SCORING_SPEC = importlib.util.spec_from_file_location("akito_event_memory_scoring", _SCORING_PATH)
if _SCORING_SPEC is None or _SCORING_SPEC.loader is None:
    raise RuntimeError(f"无法加载共享事件记忆评分模块: {_SCORING_PATH}")
_SCORING = importlib.util.module_from_spec(_SCORING_SPEC)
_SCORING_SPEC.loader.exec_module(_SCORING)
has_specific_event_cue = _SCORING.has_specific_event_cue
event_signal_cue_count = _SCORING.event_signal_cue_count
normalize = _SCORING.normalize
qualified_events = _SCORING.qualified_events
rank_events = _SCORING.rank_events
score_event = _SCORING.score_event
signal_cue_count = _SCORING.signal_cue_count
source_kind = _SCORING.source_kind

CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
MIN_STRONG_SCORE = 4.5
MIN_ISOLATED_SCORE = 3.0
MIN_SCORE_MARGIN = 1.0
LEXICAL_MAX_SCORE_GAP = 2.0
score = score_event


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
    eligible = [event for event in events if CONFIDENCE_ORDER.get(str(event.get("confidence") or "low"), 0) >= minimum]
    scored = rank_events(query, eligible)
    if not scored:
        return {"status": "no_hit", "reason": "no_relevant_event", "hits": [], "scored": []}
    if scored[0][0] < MIN_ISOLATED_SCORE:
        return {"status": "no_hit", "reason": "low_score", "hits": [], "scored": scored}
    score_margin = scored[0][0] - scored[1][0] if len(scored) > 1 else scored[0][0]
    top_event_signal_count = event_signal_cue_count(query, scored[0][1])
    if scored[0][0] < MIN_STRONG_SCORE and score_margin < MIN_SCORE_MARGIN and top_event_signal_count < 2:
        return {"status": "no_hit", "reason": "ambiguous_candidates", "hits": [], "scored": scored}
    selected, _ = qualified_events(
        scored,
        top_k=top_k,
        minimum_score=MIN_ISOLATED_SCORE,
        max_score_gap=LEXICAL_MAX_SCORE_GAP,
    )
    if not selected:
        return {"status": "no_hit", "reason": "no_relevant_event", "hits": [], "scored": scored}
    return {"status": "hit", "reason": "", "hits": selected, "scored": scored}


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
        forbidden = {str(item) for item in case.get("forbidden_event_ids", [])}
        event_ids = [str(event.get("event_id")) for _, event in result["hits"]]
        rank = next((index for index, event_id in enumerate(event_ids, 1) if event_id in expected), None)
        forbidden_hits = sorted(forbidden & set(event_ids))
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
                "matched": (rank is not None and not forbidden_hits) if kind == "positive" else result["status"] == "no_hit",
                "rank": rank, "event_ids": event_ids[:top_k], "expected_event_ids": sorted(expected),
                "forbidden_event_ids": sorted(forbidden), "forbidden_hits": forbidden_hits,
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
    parser.add_argument("--eval-set", default="tools/event_memory/retrieval/eval_set.json")
    parser.add_argument("--asset", default="data/content/akito_event_memories.json")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--output", default="docs/conversation_ai/event_memory/M2_EVENT_RECALL.md")
    args = parser.parse_args()
    eval_set = json.loads((ROOT / args.eval_set).read_text(encoding="utf-8"))
    asset = json.loads((ROOT / args.asset).read_text(encoding="utf-8"))
    report = evaluate(eval_set, asset, top_k=max(1, args.top_k))
    output = ROOT / args.output
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
