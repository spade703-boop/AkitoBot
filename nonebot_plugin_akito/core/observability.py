"""Low-overhead turn tracing and baseline metrics for the conversation pipeline."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import threading
import time
from typing import Any
import uuid

from nonebot.log import logger


@dataclass
class TurnTrace:
    """In-memory trace for one turn; user text is intentionally not retained."""

    request_id: str
    trace_schema_version: int = 1
    recorded_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    )
    group_id: str = ""
    surface: str = "main_chat"
    stage: str = "response"
    started_at: float = field(default_factory=time.perf_counter)
    intent: str = ""
    context_sources: list[str] = field(default_factory=list)
    context_shadow: list[dict[str, Any]] = field(default_factory=list)
    experiment_arm: str = "default"
    m1_context_mode: str = ""
    m2_memory_mode: str = ""
    event_candidates: list[str] = field(default_factory=list)
    event_evidence_units: list[str] = field(default_factory=list)
    event_confidence: list[str] = field(default_factory=list)
    event_retrieval_status: str = ""
    event_retrieval_reason: str = ""
    event_top_score: float = 0.0
    event_score_margin: float = 0.0
    event_candidate_count: int = 0
    fallback_reason: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    parse_success: bool | None = None
    repeat_detected: bool = False
    memory_hit: bool = False
    retries: int = 0
    outcome: str = "running"
    elapsed_ms: float = 0.0


_LOCK = threading.Lock()
_CURRENT_REQUEST_ID: ContextVar[str | None] = ContextVar("conversation_request_id", default=None)
_ACTIVE: dict[str, TurnTrace] = {}
def _new_metric_bucket() -> dict[str, Any]:
    return {
        "total_turns": 0,
        "completed_turns": 0,
        "failed_turns": 0,
        "silent_turns": 0,
        "model_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "parse_successes": 0,
        "parse_failures": 0,
        "repeat_detections": 0,
        "search_requests": 0,
        "search_successes": 0,
        "memory_hits": 0,
        "event_retrieval_observed": 0,
        "event_hits": 0,
        "fallbacks": 0,
        "tool_calls": 0,
        "tool_successes": 0,
        "retries": 0,
        "latencies_ms": [],
    }


_METRICS: dict[str, Any] = _new_metric_bucket()
_ARM_METRICS: dict[str, dict[str, Any]] = {}


def new_request_id() -> str:
    """Return a short opaque identifier suitable for logs and test fixtures."""
    return uuid.uuid4().hex[:12]


def start_turn_trace(
    request_id: str | None = None,
    *,
    group_id: str | int | None = None,
    surface: str = "main_chat",
    stage: str = "response",
) -> TurnTrace:
    """Start and register a turn trace."""
    trace = TurnTrace(
        request_id=request_id or new_request_id(),
        group_id=str(group_id) if group_id is not None else "",
        surface=surface or "main_chat",
        stage=stage or "response",
    )
    _CURRENT_REQUEST_ID.set(trace.request_id)
    with _LOCK:
        _ACTIVE[trace.request_id] = trace
        _METRICS["total_turns"] += 1
    return trace


def get_turn_trace(request_id: str) -> TurnTrace | None:
    """Return the active trace for a request, if it still exists."""
    with _LOCK:
        return _ACTIVE.get(request_id)


def current_request_id() -> str | None:
    """Return the request id bound to the current async task."""
    return _CURRENT_REQUEST_ID.get()


def _get_trace(request_id: str | None) -> TurnTrace | None:
    return get_turn_trace(request_id) if request_id else None


def record_intent(request_id: str, intent: str) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.intent = intent


def set_trace_stage(request_id: str | None, stage: str) -> None:
    """Update the current processing stage without changing the request id."""
    trace = _get_trace(request_id)
    if trace is not None and stage:
        trace.stage = stage


def record_context_sources(request_id: str, sources: list[str]) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.context_sources = list(dict.fromkeys(source for source in sources if source))


def record_context_shadow(request_id: str | None, report: dict[str, Any]) -> None:
    """Append privacy-safe hypothetical context selection metadata to a trace."""
    trace = _get_trace(request_id)
    if trace is not None and report:
        trace.context_shadow.append(dict(report))


def record_rollout(
    request_id: str | None,
    *,
    experiment_arm: str,
    m1_context_mode: str,
    m2_memory_mode: str,
) -> None:
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.experiment_arm = str(experiment_arm or "default")
    trace.m1_context_mode = str(m1_context_mode or "")
    trace.m2_memory_mode = str(m2_memory_mode or "")


def record_event_memory(
    request_id: str | None,
    *,
    candidates: list[str] | tuple[str, ...] = (),
    evidence_units: list[str] | tuple[str, ...] = (),
    confidences: list[str] | tuple[str, ...] = (),
    status: str = "",
    reason: str = "",
    top_score: float = 0.0,
    score_margin: float = 0.0,
    candidate_count: int = 0,
    fallback_reason: str = "",
) -> None:
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.event_candidates = list(dict.fromkeys(str(item) for item in candidates if str(item)))
    trace.event_evidence_units = list(dict.fromkeys(str(item) for item in evidence_units if str(item)))
    trace.event_confidence = list(dict.fromkeys(str(item) for item in confidences if str(item)))
    trace.event_retrieval_status = str(status or "")
    trace.event_retrieval_reason = str(reason or "")
    trace.event_top_score = round(float(top_score or 0.0), 3)
    trace.event_score_margin = round(float(score_margin or 0.0), 3)
    trace.event_candidate_count = max(0, int(candidate_count or 0))
    if fallback_reason:
        trace.fallback_reason.append(str(fallback_reason))


def record_fallback_reason(request_id: str | None, reason: str) -> None:
    trace = _get_trace(request_id)
    if trace is not None and reason:
        trace.fallback_reason.append(str(reason))


def record_model_call(request_id: str | None, *, usage: Any = None) -> None:
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.model_calls += 1
    prompt_tokens, completion_tokens, total_tokens = _extract_usage(usage)
    trace.prompt_tokens += prompt_tokens
    trace.completion_tokens += completion_tokens
    trace.total_tokens += total_tokens


def _extract_usage(usage: Any) -> tuple[int, int, int]:
    if usage is None:
        return 0, 0, 0
    if isinstance(usage, dict):
        def get_value(key: str, default: int = 0) -> Any:
            return usage.get(key, default)
    else:
        def get_value(key: str, default: int = 0) -> Any:
            return getattr(usage, key, default)
    try:
        prompt = int(get_value("prompt_tokens", 0) or 0)
        completion = int(get_value("completion_tokens", 0) or 0)
        total = int(get_value("total_tokens", prompt + completion) or prompt + completion)
    except (TypeError, ValueError):
        return 0, 0, 0
    return prompt, completion, total


def record_parse_result(request_id: str | None, *, success: bool) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.parse_success = success


def record_repeat_detection(request_id: str | None) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.repeat_detected = True


def record_memory_hit(request_id: str | None, *, hit: bool) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.memory_hit = hit


def record_retry(request_id: str | None) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.retries += 1


def record_tool_call(
    request_id: str | None,
    *,
    name: str,
    status: str,
    latency_ms: float = 0.0,
) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        trace.tool_calls.append({"name": name, "status": status, "latency_ms": round(latency_ms, 2)})


def finish_turn_trace(request_id: str, *, outcome: str) -> dict[str, Any] | None:
    """Finalize a trace, update aggregate counters, and emit a structured log."""
    with _LOCK:
        trace = _ACTIVE.pop(request_id, None)
        if trace is None:
            return None
        trace.outcome = outcome
        trace.elapsed_ms = round((time.perf_counter() - trace.started_at) * 1000, 2)
        payload = asdict(trace)
        _accumulate_metrics(_METRICS, trace, count_turn=False)
        arm = trace.experiment_arm or "default"
        _accumulate_metrics(_ARM_METRICS.setdefault(arm, _new_metric_bucket()), trace)
        _persist_trace(payload)
    logger.info("conversation_trace={}", json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    if _CURRENT_REQUEST_ID.get() == request_id:
        _CURRENT_REQUEST_ID.set(None)
    return payload


def _persist_trace(payload: dict[str, Any]) -> None:
    path_text = os.environ.get("AKITO_CONVERSATION_TRACE_PATH", "").strip()
    if not path_text:
        return
    try:
        path = Path(path_text)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    except Exception as exc:
        logger.warning("conversation trace 写入失败: %s", exc)


def _accumulate_metrics(
    metrics: dict[str, Any],
    trace: TurnTrace,
    *,
    count_turn: bool = True,
) -> None:
    metrics["total_turns"] += count_turn
    metrics["completed_turns"] += trace.outcome == "completed"
    metrics["failed_turns"] += trace.outcome == "failed"
    metrics["silent_turns"] += trace.outcome == "silent"
    metrics["model_calls"] += trace.model_calls
    metrics["prompt_tokens"] += trace.prompt_tokens
    metrics["completion_tokens"] += trace.completion_tokens
    metrics["total_tokens"] += trace.total_tokens
    metrics["parse_successes"] += trace.parse_success is True
    metrics["parse_failures"] += trace.parse_success is False
    metrics["repeat_detections"] += trace.repeat_detected
    metrics["memory_hits"] += trace.memory_hit
    event_observed = trace.event_retrieval_status not in {"", "disabled"}
    metrics["event_retrieval_observed"] += event_observed
    metrics["event_hits"] += event_observed and trace.event_retrieval_status == "hit"
    metrics["fallbacks"] += bool(trace.fallback_reason)
    metrics["tool_calls"] += len(trace.tool_calls)
    metrics["tool_successes"] += sum(item["status"] == "success" for item in trace.tool_calls)
    metrics["search_requests"] += sum(item["name"] == "search" for item in trace.tool_calls)
    metrics["search_successes"] += sum(
        item["name"] == "search" and item["status"] == "success" for item in trace.tool_calls
    )
    metrics["retries"] += trace.retries
    metrics["latencies_ms"].append(trace.elapsed_ms)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=100, method="inclusive")[int(percentile) - 1], 2)


def snapshot_metrics() -> dict[str, Any]:
    """Return JSON-safe aggregate metrics for a baseline report."""
    with _LOCK:
        snapshot = {key: value for key, value in _METRICS.items() if key != "latencies_ms"}
        latencies = list(_METRICS["latencies_ms"])
        arm_metrics = {
            arm: {key: list(value) if key == "latencies_ms" else value for key, value in metrics.items()}
            for arm, metrics in _ARM_METRICS.items()
        }
    total = snapshot["total_turns"] or 1
    snapshot.update(
        {
            "parse_success_rate": round(snapshot["parse_successes"] / total, 4),
            "repeat_rate": round(snapshot["repeat_detections"] / total, 4),
            "memory_hit_rate": round(snapshot["memory_hits"] / total, 4),
            "search_success_rate": round(
                snapshot["search_successes"] / snapshot["search_requests"], 4
            )
            if snapshot["search_requests"]
            else None,
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "by_experiment_arm": {
                arm: _summarize_metric_bucket(metrics)
                for arm, metrics in sorted(arm_metrics.items())
            },
        }
    )
    return snapshot


def reset_metrics() -> None:
    """Reset in-memory counters; intended for tests and local baseline runs."""
    with _LOCK:
        _ACTIVE.clear()
        _ARM_METRICS.clear()
        _METRICS.clear()
        _METRICS.update(_new_metric_bucket())


def _summarize_metric_bucket(metrics: dict[str, Any]) -> dict[str, Any]:
    total_turns = int(metrics["total_turns"])
    parse_observed = int(metrics["parse_successes"]) + int(metrics["parse_failures"])
    event_observed = int(metrics["event_retrieval_observed"])
    return {
        "turns": total_turns,
        "completed": metrics["completed_turns"],
        "failed": metrics["failed_turns"],
        "silent": metrics["silent_turns"],
        "parse_success_rate": round(
            int(metrics["parse_successes"]) / parse_observed, 4
        ) if parse_observed else None,
        "avg_tokens": round(int(metrics["total_tokens"]) / total_turns, 2) if total_turns else 0.0,
        "p50_latency_ms": _percentile(metrics["latencies_ms"], 50),
        "p95_latency_ms": _percentile(metrics["latencies_ms"], 95),
        "event_hit_rate": round(
            int(metrics["event_hits"]) / event_observed, 4
        ) if event_observed else None,
        "fallback_rate": round(
            int(metrics["fallbacks"]) / total_turns, 4
        ) if total_turns else None,
    }
