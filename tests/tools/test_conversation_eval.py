"""Tests for the M0 conversation evaluation set and judge rubric."""

from pathlib import Path

from tools.conversation_eval import (
    ANALYSIS_JUDGE_DIMENSIONS,
    build_judge_prompt,
    load_eval_set,
    parse_judge_result,
    score_signal_diagnostics,
    summarize_responses,
    summarize_traces,
    validate_eval_set,
)

ROOT = Path(__file__).resolve().parents[2]


def test_m0_eval_set_has_required_coverage_and_valid_references():
    data = load_eval_set(ROOT / "tools/conversation_eval_set.json")

    assert validate_eval_set(data) == []
    assert len(data["cases"]) == 68
    assert sum(case["category"] == "plot_recall" for case in data["cases"]) == 12
    assert {case["surface"] for case in data["cases"]} == {
        "main_chat",
        "auto_chat",
        "impression_analysis",
        "impression_reply",
    }
    assert all(case["task"] for case in data["cases"])


def test_judge_prompt_treats_original_line_as_evidence_not_exact_answer():
    case = next(case for case in load_eval_set(ROOT / "tools/conversation_eval_set.json")["cases"] if case["id"] == "plot-001")

    prompt = build_judge_prompt(case, "记得，那次大家安排得太夸张了，不过冬弥也确实出了力。")

    assert "不是需要复述的标准答案" in prompt
    assert case["reference"]["evidence"] in prompt
    assert "factual_grounding" in prompt
    assert "不要求逐字复刻" in prompt


def test_impression_analysis_judge_uses_neutral_material_rubric():
    case = {
        "id": "analysis-001",
        "category": "impression",
        "surface": "impression_analysis",
        "task": "material_grounding",
        "user_message": "整理材料",
        "expected_signals": ["证据"],
        "forbidden_signals": ["彰人口吻"],
    }

    prompt = build_judge_prompt(case, '{"evidence": ["今天好累"]}')

    assert "中性材料分析阶段" in prompt
    assert "不要把彰人语气或角色扮演作为本阶段评分标准" in prompt
    assert "evidence_grounding" in prompt
    assert "persona_voice" not in prompt


def test_auto_chat_judge_allows_correct_silence_and_current_message_anchor():
    case = {
        "id": "auto-001",
        "category": "auto_chat",
        "surface": "auto_chat",
        "task": "interject_observer",
        "user_message": "大家继续聊天",
        "expected_signals": ["静默"],
        "forbidden_signals": ["旧话题"],
    }

    prompt = build_judge_prompt(case, "")

    assert "空回复/静默可以是正确结果" in prompt
    assert "只能锚定当前消息" in prompt


def test_judge_result_parser_requires_all_dimensions():
    valid = """{"factual_grounding":2,"relationship_consistency":2,"emotional_direction":1,"persona_voice":2,"scene_naturalness":2,"invention_control":2,"verdict":"pass","short_reason":"自然接住"}"""

    assert parse_judge_result(valid)["verdict"] == "pass"
    assert parse_judge_result("not json") is None
    assert parse_judge_result('{"verdict":"pass"}') is None
    analysis = '{"evidence_grounding":2,"observation_quality":2,"uncertainty_control":1,"attribution_accuracy":2,"verdict":"pass","short_reason":"边界清楚"}'
    assert parse_judge_result(analysis, ANALYSIS_JUDGE_DIMENSIONS)["verdict"] == "pass"


def test_signal_diagnostics_is_transparent_and_not_final_judge():
    case = {
        "expected_signals": ["冬弥", "无奈"],
        "forbidden_signals": ["不认识冬弥"],
    }

    result = score_signal_diagnostics(case, "我和冬弥都知道那件事，确实有点无奈。")

    assert result["expected_signal_coverage"] == 1.0
    assert result["forbidden_signal_triggered"] is False


def test_response_summary_tracks_duplicates_and_optional_judge():
    data = {
        "cases": [
            {"id": "a", "category": "casual", "expected_signals": ["好"], "forbidden_signals": []},
            {"id": "b", "category": "casual", "expected_signals": ["好"], "forbidden_signals": []},
        ]
    }
    summary = summarize_responses(
        data,
        [
            {"id": "a", "response": "好。"},
            {"id": "b", "response": "好。", "judge": {"persona_voice": 2}},
        ],
    )

    assert summary["response_cases"] == 2
    assert summary["exact_duplicate_rate"] == 0.5
    assert summary["judge_cases"] == 1


def test_trace_summary_calculates_runtime_rates_and_percentiles():
    summary = summarize_traces(
        [
            {
                "surface": "main_chat",
                "stage": "response",
                "outcome": "completed",
                "elapsed_ms": 100,
                "model_calls": 1,
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "total_tokens": 15,
                "parse_success": True,
                "memory_hit": True,
                "experiment_arm": "combined",
                "group_id": "1041487251",
                "event_retrieval_status": "hit",
                "fallback_reason": [],
                "repeat_detected": False,
                "retries": 0,
                "context_shadow": [
                    {"total_blocks": 3, "estimated_tokens": 30, "omitted_sources": ["old_history"]}
                ],
                "tool_calls": [{"name": "search", "status": "success"}],
            },
            {
                "surface": "auto_chat",
                "stage": "response",
                "outcome": "failed",
                "elapsed_ms": 200,
                "model_calls": 1,
                "parse_success": False,
                "memory_hit": False,
                "experiment_arm": "control",
                "group_id": "691188576",
                "event_retrieval_status": "disabled",
                "fallback_reason": ["legacy_prompt_fallback"],
                "repeat_detected": True,
                "retries": 1,
                "tool_calls": [{"name": "search", "status": "empty"}],
            },
        ]
    )

    assert summary["total_turns"] == 2
    assert summary["parse_success_rate"] == 0.5
    assert summary["memory_hit_rate"] == 0.5
    assert summary["event_hit_rate"] == 1.0
    assert summary["fallback_rate"] == 0.5
    assert summary["search_success_rate"] == 0.5
    assert summary["p50_latency_ms"] == 150.0
    assert summary["p95_latency_ms"] == 195.0
    assert summary["surface_counts"] == {"auto_chat": 1, "main_chat": 1}
    assert summary["surface_metrics"]["auto_chat"]["silent_turns"] == 0
    assert summary["context_shadow_reports"] == 1
    assert summary["context_shadow_omitted_sources"] == {"old_history": 1}
    assert summary["experiment_arm_counts"] == {"combined": 1, "control": 1}
    assert summary["group_counts"] == {"1041487251": 1, "691188576": 1}
    assert summary["experiment_arm_metrics"]["combined"]["avg_tokens"] == 15.0
    assert summary["experiment_arm_surface_metrics"]["control"]["auto_chat"]["failed_turns"] == 1
