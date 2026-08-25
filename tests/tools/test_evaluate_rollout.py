import json

import pytest

from tools.evaluate_rollout import build_rollout_report, load_traces, render_rollout_report


def _trace(arm: str, *, failed: bool = False, elapsed_ms: float = 100, tokens: int = 20) -> dict:
    return {
        "experiment_arm": arm,
        "group_id": "1041487251" if arm == "combined" else "691188576",
        "surface": "main_chat",
        "stage": "response",
        "outcome": "failed" if failed else "completed",
        "elapsed_ms": elapsed_ms,
        "total_tokens": tokens,
        "parse_success": not failed,
        "event_retrieval_status": "hit" if not failed else "no_hit",
        "fallback_reason": [],
        "repeat_detected": False,
        "memory_hit": False,
        "retries": 0,
        "tool_calls": [],
    }


def test_rollout_report_passes_with_enough_comparable_samples():
    traces = [_trace("default") for _ in range(30)] + [_trace("combined", tokens=22) for _ in range(30)]

    report = build_rollout_report(traces, min_turns=30)

    assert report["verdict"] == "pass"
    assert report["arm_counts"] == {"combined": 30, "default": 30}
    assert report["observations"]["treatment"]["avg_tokens"] == 22.0
    assert report["checks"][0]["status"] == "pass"


def test_rollout_report_waits_for_both_arms():
    report = build_rollout_report([_trace("combined")], min_turns=2)

    assert report["verdict"] == "insufficient_data"
    assert report["insufficient_arms"] == ["default", "combined"]


def test_rollout_report_supports_single_active_group():
    report = build_rollout_report(
        [_trace("combined") for _ in range(30)],
        treatment_arm="combined",
        min_turns=30,
        single_arm=True,
    )

    assert report["verdict"] == "single_arm_observation"
    assert report["mode"] == "single_arm"
    assert report["missing_arms"] == []
    assert report["insufficient_arms"] == []
    markdown = render_rollout_report(report)
    assert "不提供因果比较" in markdown
    assert "单臂绝对指标" in markdown


def test_rollout_report_flags_regression_and_renders_review_boundary():
    traces = [_trace("default") for _ in range(30)] + [_trace("combined", failed=True) for _ in range(30)]
    report = build_rollout_report(traces, min_turns=30)

    assert report["verdict"] == "review"
    assert report["checks"][0]["status"] == "review"
    markdown = render_rollout_report(report, trace_path="traces.jsonl")
    assert "人工复核清单" in markdown
    assert "用户原文" in markdown


def test_load_traces_rejects_malformed_lines(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.write_text(json.dumps({"experiment_arm": "control"}) + "\nnot-json\n", encoding="utf-8")

    with pytest.raises(ValueError, match="第 2 行不是 JSON"):
        load_traces(path)
