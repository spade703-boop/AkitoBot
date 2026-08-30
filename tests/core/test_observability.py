"""Tests for M0 turn tracing and aggregate baseline metrics."""

from nonebot_plugin_akito.core.observability import (
    finish_turn_trace,
    record_ambiguity_guard,
    record_context_shadow,
    record_context_sources,
    record_event_memory,
    record_intent,
    record_memory_hit,
    record_model_call,
    record_parse_result,
    record_repeat_detection,
    record_retry,
    record_rollout,
    record_tool_call,
    reset_metrics,
    set_trace_stage,
    snapshot_metrics,
    start_turn_trace,
)


def test_turn_trace_collects_structured_fields_and_metrics():
    reset_metrics()
    trace = start_turn_trace("m0-test-001", group_id="1041487251", surface="auto_chat", stage="response")
    set_trace_stage(trace.request_id, "reply")
    record_context_shadow(
        trace.request_id,
        {
            "stage": "reply",
            "budget_tokens": 100,
            "total_blocks": 2,
            "estimated_tokens": 20,
            "selected_sources": ["current_turn"],
            "omitted_sources": ["old_history"],
        },
    )
    record_intent(trace.request_id, "web_search")
    record_context_sources(trace.request_id, ["persona", "persona", "group_context"])
    record_rollout(
        trace.request_id,
        experiment_arm="combined",
        m1_context_mode="canary",
        m2_memory_mode="shadow",
    )
    record_event_memory(
        trace.request_id,
        candidates=["event-1", "event-1"],
        evidence_units=["event-1:31", "event-1:31", "event-1:83"],
        confidences=["high"],
        status="hit",
        reason="",
        top_score=8.5,
        score_margin=2.0,
        candidate_count=3,
        retrieval_strategy="hybrid",
        candidate_diagnostics=[
            {
                "event_id": "event-1",
                "source_kind": "curated_story",
                "lexical_score": 6.5,
                "cosine_score": 0.42,
                "rerank_score": 0.91,
                "kept": True,
                "drop_reason": "",
            }
        ],
    )
    record_model_call(trace.request_id, usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})
    record_parse_result(trace.request_id, success=True)
    record_memory_hit(trace.request_id, hit=True)
    record_repeat_detection(trace.request_id)
    record_retry(trace.request_id)
    record_tool_call(trace.request_id, name="search", status="success", latency_ms=12.5)

    payload = finish_turn_trace(trace.request_id, outcome="completed")
    metrics = snapshot_metrics()

    assert payload is not None
    assert payload["surface"] == "auto_chat"
    assert payload["trace_schema_version"] == 1
    assert payload["recorded_at"].endswith("+00:00")
    assert payload["group_id"] == "1041487251"
    assert payload["stage"] == "reply"
    assert payload["context_shadow"][0]["selected_sources"] == ["current_turn"]
    assert payload["intent"] == "web_search"
    assert payload["context_sources"] == ["persona", "group_context"]
    assert payload["experiment_arm"] == "combined"
    assert payload["m1_context_mode"] == "canary"
    assert payload["event_candidates"] == ["event-1"]
    assert payload["event_evidence_units"] == ["event-1:31", "event-1:83"]
    assert payload["event_retrieval_status"] == "hit"
    assert payload["event_retrieval_reason"] == ""
    assert payload["event_top_score"] == 8.5
    assert payload["event_candidate_count"] == 3
    assert payload["event_retrieval_strategy"] == "hybrid"
    assert payload["event_candidate_diagnostics"][0]["rerank_score"] == 0.91
    assert payload["total_tokens"] == 14
    assert payload["tool_calls"][0]["name"] == "search"
    assert metrics["total_turns"] == 1
    assert metrics["completed_turns"] == 1
    assert metrics["parse_success_rate"] == 1.0
    assert metrics["search_success_rate"] == 1.0
    assert metrics["repeat_detections"] == 1
    assert metrics["retries"] == 1
    assert metrics["p50_latency_ms"] is not None
    assert metrics["by_experiment_arm"]["combined"]["turns"] == 1
    assert metrics["by_experiment_arm"]["combined"]["event_hit_rate"] == 1.0
    assert metrics["by_experiment_arm"]["combined"]["avg_tokens"] == 14.0


def test_ambiguity_guard_is_not_a_fallback_or_event_retrieval_hit():
    reset_metrics()
    trace = start_turn_trace("m0-guard-001", surface="main_chat", stage="response")
    record_ambiguity_guard(
        trace.request_id,
        triggered=True,
        reason="ambiguous_event_reference",
        signals=["event_reference", "follow_up"],
    )
    record_event_memory(trace.request_id, status="skipped", reason="ambiguity_guard")

    payload = finish_turn_trace(trace.request_id, outcome="completed")
    metrics = snapshot_metrics()

    assert payload is not None
    assert payload["ambiguity_guard_triggered"] is True
    assert payload["ambiguity_guard_reason"] == "ambiguous_event_reference"
    assert payload["ambiguity_guard_signals"] == ["event_reference", "follow_up"]
    assert payload["fallback_reason"] == []
    assert metrics["guarded_turns"] == 1
    assert metrics["guard_rate"] == 1.0
    assert metrics["event_retrieval_observed"] == 0
    assert metrics["event_hits"] == 0


def test_trace_can_persist_sanitized_jsonl(monkeypatch, tmp_path):
    trace_path = tmp_path / "traces" / "conversation.jsonl"
    monkeypatch.setenv("AKITO_CONVERSATION_TRACE_PATH", str(trace_path))
    reset_metrics()
    trace = start_turn_trace("m0-test-002", surface="impression", stage="analysis")
    record_intent(trace.request_id, "mention")

    finish_turn_trace(trace.request_id, outcome="completed")

    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert '"request_id":"m0-test-002"' in lines[0]
    assert "content" not in lines[0]


def test_finish_unknown_trace_is_safe(monkeypatch):
    monkeypatch.delenv("AKITO_CONVERSATION_TRACE_PATH", raising=False)
    reset_metrics()
    assert finish_turn_trace("missing", outcome="failed") is None
    assert snapshot_metrics()["total_turns"] == 0
