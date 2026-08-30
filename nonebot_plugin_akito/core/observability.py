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
    auto_reply_shadow: dict[str, Any] | None = None
    experiment_arm: str = "default"
    m1_context_mode: str = ""
    m2_memory_mode: str = ""
    m3_tool_mode: str = "off"
    event_candidates: list[str] = field(default_factory=list)
    event_evidence_units: list[str] = field(default_factory=list)
    event_confidence: list[str] = field(default_factory=list)
    event_retrieval_status: str = ""
    event_retrieval_reason: str = ""
    event_top_score: float = 0.0
    event_score_margin: float = 0.0
    event_candidate_count: int = 0
    event_retrieval_strategy: str = ""
    event_candidate_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    fallback_reason: list[str] = field(default_factory=list)
    ambiguity_guard_triggered: bool = False
    ambiguity_guard_reason: str = ""
    ambiguity_guard_signals: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_route_mode: str = "off"
    tool_route_category: str = ""
    tool_route_decision: str = ""
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


@dataclass(frozen=True)
class AutoReplyShadowReport:
    """Privacy-safe rule evaluation for one optional random-chat turn."""

    should_interject: bool | None = None
    silence_reason: str = ""
    anchor_valid: bool | None = None
    current_message_only: bool | None = None
    cross_turn_breach: bool = False
    actual_interjected: bool = False
    relevance: str = "unknown"


_DETERMINISTIC_AUTO_SILENCE_REASONS = {
    "short_message",
    "blocked_prefix",
    "blocked_keyword",
    "sleeping",
    "safety_period",
    "cooldown",
}


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
        "guarded_turns": 0,
        "tool_calls": 0,
        "tool_successes": 0,
        "tool_route_modes": {},
        "auto_reply_turns": 0,
        "auto_reply_interjected": 0,
        "auto_reply_silent": 0,
        "auto_reply_anchor_failures": 0,
        "auto_reply_cross_turn_breaches": 0,
        "auto_reply_labeled": 0,
        "auto_reply_correct": 0,
        "auto_reply_silence_reasons": {},
        "auto_reply_relevance": {},
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


def record_auto_reply_shadow(
    request_id: str | None,
    report: AutoReplyShadowReport | dict[str, Any],
    *,
    actual_interjected: bool | None = None,
) -> None:
    """Attach a bounded auto-reply evaluation without retaining message text."""
    trace = _get_trace(request_id)
    if trace is None:
        return
    if isinstance(report, AutoReplyShadowReport):
        payload = asdict(report)
    else:
        payload = asdict(AutoReplyShadowReport(**{
            key: value
            for key, value in report.items()
            if key in AutoReplyShadowReport.__dataclass_fields__
        }))
    if actual_interjected is not None:
        payload["actual_interjected"] = bool(actual_interjected)
    trace.auto_reply_shadow = payload


def evaluate_auto_reply_shadow(
    message: str,
    *,
    addressed_to_bot: bool = False,
    silence_reason: str = "",
    reply: str = "",
    anchor: str = "",
    actual_interjected: bool = False,
) -> AutoReplyShadowReport:
    """Evaluate deterministic signals for random-chat shadow metrics.

    Passive group messages deliberately remain ``unknown`` so the report never
    pretends to know whether an optional interjection was socially necessary.
    """
    compact_message = "".join(str(message or "").split())
    compact_anchor = "".join(str(anchor or "").split())
    has_reply = bool(str(reply or "").strip())
    anchor_valid = True if not has_reply else bool(len(compact_anchor) >= 2 and compact_anchor in compact_message)
    cross_turn_breach = bool(has_reply and not anchor_valid)
    if addressed_to_bot:
        should_interject: bool | None = True
    elif silence_reason in _DETERMINISTIC_AUTO_SILENCE_REASONS:
        should_interject = False
    else:
        should_interject = None
    if cross_turn_breach:
        relevance = "irrelevant"
    elif has_reply and anchor_valid:
        relevance = "relevant"
    else:
        relevance = "unknown"
    return AutoReplyShadowReport(
        should_interject=should_interject,
        silence_reason=str(silence_reason or ""),
        anchor_valid=anchor_valid,
        current_message_only=anchor_valid,
        cross_turn_breach=cross_turn_breach,
        actual_interjected=bool(actual_interjected),
        relevance=relevance,
    )


def record_rollout(
    request_id: str | None,
    *,
    experiment_arm: str,
    m1_context_mode: str,
    m2_memory_mode: str,
    m3_tool_mode: str = "off",
) -> None:
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.experiment_arm = str(experiment_arm or "default")
    trace.m1_context_mode = str(m1_context_mode or "")
    trace.m2_memory_mode = str(m2_memory_mode or "")
    trace.m3_tool_mode = str(m3_tool_mode or "off")


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
    retrieval_strategy: str = "",
    candidate_diagnostics: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
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
    trace.event_retrieval_strategy = str(retrieval_strategy or "")
    trace.event_candidate_diagnostics = [
        {
            "event_id": str(item.get("event_id") or ""),
            "source_kind": str(item.get("source_kind") or ""),
            "lexical_score": round(float(item.get("lexical_score") or 0.0), 3),
            "cosine_score": round(float(item["cosine_score"]), 6) if item.get("cosine_score") is not None else None,
            "rerank_score": round(float(item["rerank_score"]), 6) if item.get("rerank_score") is not None else None,
            "kept": bool(item.get("kept")),
            "drop_reason": str(item.get("drop_reason") or ""),
        }
        for item in candidate_diagnostics[:10]
        if isinstance(item, dict) and item.get("event_id")
    ]
    if fallback_reason:
        trace.fallback_reason.append(str(fallback_reason))


def record_fallback_reason(request_id: str | None, reason: str) -> None:
    trace = _get_trace(request_id)
    if trace is not None and reason:
        trace.fallback_reason.append(str(reason))


def record_ambiguity_guard(
    request_id: str | None,
    *,
    triggered: bool,
    reason: str = "",
    signals: list[str] | tuple[str, ...] = (),
) -> None:
    """Record deterministic ambiguity preflight separately from model fallbacks."""
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.ambiguity_guard_triggered = bool(triggered)
    trace.ambiguity_guard_reason = str(reason or "")
    trace.ambiguity_guard_signals = list(dict.fromkeys(str(item) for item in signals if str(item)))


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
    error_code: str = "",
) -> None:
    trace = _get_trace(request_id)
    if trace is not None:
        payload = {"name": name, "status": status, "latency_ms": round(latency_ms, 2)}
        if error_code:
            payload["error_code"] = str(error_code)
        trace.tool_calls.append(payload)


def record_tool_route(
    request_id: str | None,
    *,
    mode: str,
    category: str = "",
    decision: str = "",
) -> None:
    trace = _get_trace(request_id)
    if trace is None:
        return
    trace.tool_route_mode = str(mode or "off")
    trace.tool_route_category = str(category or "")
    trace.tool_route_decision = str(decision or "")


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
    event_observed = trace.event_retrieval_status not in {"", "disabled", "skipped"}
    metrics["event_retrieval_observed"] += event_observed
    metrics["event_hits"] += event_observed and trace.event_retrieval_status == "hit"
    metrics["fallbacks"] += bool(trace.fallback_reason)
    metrics["guarded_turns"] += trace.ambiguity_guard_triggered
    metrics["tool_calls"] += len(trace.tool_calls)
    metrics["tool_successes"] += sum(item["status"] == "success" for item in trace.tool_calls)
    route_mode = str(trace.tool_route_mode or "off")
    route_modes = metrics["tool_route_modes"]
    route_modes[route_mode] = int(route_modes.get(route_mode, 0)) + 1
    metrics["search_requests"] += sum(item["name"] == "search" for item in trace.tool_calls)
    metrics["search_successes"] += sum(
        item["name"] == "search" and item["status"] == "success" for item in trace.tool_calls
    )
    metrics["retries"] += trace.retries
    metrics["latencies_ms"].append(trace.elapsed_ms)
    report = trace.auto_reply_shadow
    if trace.surface == "auto_chat" and report:
        metrics["auto_reply_turns"] += 1
        metrics["auto_reply_interjected"] += bool(report.get("actual_interjected"))
        metrics["auto_reply_silent"] += not bool(report.get("actual_interjected"))
        metrics["auto_reply_anchor_failures"] += not bool(report.get("anchor_valid", True))
        metrics["auto_reply_cross_turn_breaches"] += bool(report.get("cross_turn_breach"))
        reason = str(report.get("silence_reason") or "")
        if reason:
            reasons = metrics["auto_reply_silence_reasons"]
            reasons[reason] = int(reasons.get(reason, 0)) + 1
        relevance = str(report.get("relevance") or "unknown")
        relevance_counts = metrics["auto_reply_relevance"]
        relevance_counts[relevance] = int(relevance_counts.get(relevance, 0)) + 1
        expected = report.get("should_interject")
        if expected is not None:
            metrics["auto_reply_labeled"] += 1
            metrics["auto_reply_correct"] += bool(expected) == bool(report.get("actual_interjected"))


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
    auto_reply_summary = _summarize_auto_reply_metrics(snapshot)
    snapshot.update(
        {
            "parse_success_rate": round(snapshot["parse_successes"] / total, 4),
            "repeat_rate": round(snapshot["repeat_detections"] / total, 4),
            "memory_hit_rate": round(snapshot["memory_hits"] / total, 4),
            "guard_rate": round(snapshot["guarded_turns"] / total, 4),
            "search_success_rate": round(
                snapshot["search_successes"] / snapshot["search_requests"], 4
            )
            if snapshot["search_requests"]
            else None,
            "p50_latency_ms": _percentile(latencies, 50),
            "p95_latency_ms": _percentile(latencies, 95),
            "auto_reply_shadow": auto_reply_summary,
            "auto_reply_accuracy": auto_reply_summary["accuracy"],
            "auto_reply_silence_reasons": auto_reply_summary["silence_reasons"],
            "tool_route_modes": dict(snapshot.get("tool_route_modes", {})),
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
        "guarded_turns": metrics["guarded_turns"],
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
        "guard_rate": round(
            int(metrics["guarded_turns"]) / total_turns, 4
        ) if total_turns else None,
        "tool_route_modes": dict(metrics.get("tool_route_modes", {})),
        "auto_reply": _summarize_auto_reply_metrics(metrics),
    }


def _summarize_auto_reply_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    labeled = int(metrics.get("auto_reply_labeled", 0))
    turns = int(metrics.get("auto_reply_turns", 0))
    anchor_failures = int(metrics.get("auto_reply_anchor_failures", 0))
    cross_turn_breaches = int(metrics.get("auto_reply_cross_turn_breaches", 0))
    return {
        "turns": turns,
        "interjected": int(metrics.get("auto_reply_interjected", 0)),
        "silent": int(metrics.get("auto_reply_silent", 0)),
        "interjection_rate": round(int(metrics.get("auto_reply_interjected", 0)) / turns, 4) if turns else None,
        "anchor_failures": anchor_failures,
        "anchor_failure_rate": round(anchor_failures / turns, 4) if turns else None,
        "cross_turn_breaches": cross_turn_breaches,
        "cross_turn_breach_rate": round(cross_turn_breaches / turns, 4) if turns else None,
        "accuracy": round(int(metrics.get("auto_reply_correct", 0)) / labeled, 4) if labeled else None,
        "silence_reasons": dict(metrics.get("auto_reply_silence_reasons", {})),
        "relevance": dict(metrics.get("auto_reply_relevance", {})),
    }
