"""Tests for M0 turn tracing and aggregate baseline metrics."""

from nonebot_plugin_akito.core.observability import (
    finish_turn_trace,
    record_context_sources,
    record_intent,
    record_memory_hit,
    record_model_call,
    record_parse_result,
    record_repeat_detection,
    record_retry,
    record_tool_call,
    reset_metrics,
    snapshot_metrics,
    start_turn_trace,
)


def test_turn_trace_collects_structured_fields_and_metrics():
    reset_metrics()
    trace = start_turn_trace("m0-test-001")
    record_intent(trace.request_id, "web_search")
    record_context_sources(trace.request_id, ["persona", "persona", "group_context"])
    record_model_call(trace.request_id, usage={"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14})
    record_parse_result(trace.request_id, success=True)
    record_memory_hit(trace.request_id, hit=True)
    record_repeat_detection(trace.request_id)
    record_retry(trace.request_id)
    record_tool_call(trace.request_id, name="search", status="success", latency_ms=12.5)

    payload = finish_turn_trace(trace.request_id, outcome="completed")
    metrics = snapshot_metrics()

    assert payload is not None
    assert payload["intent"] == "web_search"
    assert payload["context_sources"] == ["persona", "group_context"]
    assert payload["total_tokens"] == 14
    assert payload["tool_calls"][0]["name"] == "search"
    assert metrics["total_turns"] == 1
    assert metrics["completed_turns"] == 1
    assert metrics["parse_success_rate"] == 1.0
    assert metrics["search_success_rate"] == 1.0
    assert metrics["repeat_detections"] == 1
    assert metrics["retries"] == 1
    assert metrics["p50_latency_ms"] is not None


def test_trace_can_persist_sanitized_jsonl(monkeypatch, tmp_path):
    trace_path = tmp_path / "traces" / "conversation.jsonl"
    monkeypatch.setenv("AKITO_CONVERSATION_TRACE_PATH", str(trace_path))
    reset_metrics()
    trace = start_turn_trace("m0-test-002")
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
