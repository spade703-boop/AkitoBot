"""Read-only retrieval of evidence-backed Akito/Toya episodic memories."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Any

from .data import EVENT_MEMORY_DB
from .event_memory_scoring import (
    event_signal_cue_count,
    qualified_events,
    rank_events,
    score_event,
    should_prioritize_curated,
)
from .event_memory_scoring import (
    has_specific_event_cue as _shared_has_specific_event_cue,
)
from .event_memory_scoring import (
    ngrams as _shared_ngrams,
)
from .event_memory_scoring import (
    normalize as _shared_normalize,
)
from .event_memory_scoring import (
    signal_cue_count as _shared_signal_cue_count,
)
from .retrieval import RetrievalContext, build_retrieval_context, retrieve_result
from .retrieval_assets import event_memory_retrieval_text
from .rollout import mode_is_shadowing

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_MIN_STRONG_SCORE = 4.5
_MIN_ISOLATED_SCORE = 3.0
_MIN_SCORE_MARGIN = 1.0
_LEXICAL_MAX_SCORE_GAP = 2.0
_HYBRID_RECALL_K = 20
_HYBRID_CURATED_MIN_SCORE = 0.10
_HYBRID_LEGACY_MIN_SCORE = 0.15
_HYBRID_RELATIVE_SCORE = 0.65
_HYBRID_CURATED_PRIORITY_MARGIN = 0.05
_SIGNAL_TERMS = (
    "生日",
    "惊喜",
    "甜食",
    "唱歌",
    "胜负",
    "切蛋糕",
    "聚餐",
    "学校",
    "热闹",
    "规则",
    "雪仗",
    "提醒",
    "学习",
    "感谢",
    "配合",
    "开心",
    "祝福",
    "讨论",
    "努力",
    "聚会",
    "清晨",
    "很早",
    "早上",
    "sekai",
    "rad blast",
)


def event_source_kind(event: dict[str, Any]) -> str:
    """Resolve provenance for new and legacy event assets."""
    explicit = str(event.get("source_kind") or "").strip().lower()
    if explicit in {"curated_story", "legacy_script"}:
        return explicit
    source = event.get("source")
    if isinstance(source, dict) and (source.get("draft_id") or source.get("url")):
        return "curated_story"
    return "legacy_script"


def event_review_status(event: dict[str, Any]) -> str:
    """Resolve review status without breaking schema-v1 assets."""
    explicit = str(event.get("review_status") or "").strip().lower()
    if explicit in {"reviewed", "generated"}:
        return explicit
    return "reviewed" if event_source_kind(event) == "curated_story" else "generated"


@dataclass(frozen=True)
class EventMemoryHit:
    event_id: str
    title: str
    summary: str
    category: str
    confidence: str
    score: float
    evidence: tuple[dict[str, Any], ...]
    topics: tuple[str, ...] = ()
    style_examples: tuple[dict[str, Any], ...] = ()
    source_kind: str = "legacy_script"
    review_status: str = "generated"
    lexical_score: float = 0.0
    cosine_score: float | None = None
    rerank_score: float | None = None


@dataclass(frozen=True)
class EventMemoryCandidate:
    event_id: str
    source_kind: str
    lexical_score: float = 0.0
    cosine_score: float | None = None
    rerank_score: float | None = None
    kept: bool = False
    drop_reason: str = ""

    def as_trace_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "source_kind": self.source_kind,
            "lexical_score": round(self.lexical_score, 3),
            "cosine_score": round(self.cosine_score, 6) if self.cosine_score is not None else None,
            "rerank_score": round(self.rerank_score, 6) if self.rerank_score is not None else None,
            "kept": self.kept,
            "drop_reason": self.drop_reason,
        }


@dataclass(frozen=True)
class EventMemoryResult:
    status: str
    hits: tuple[EventMemoryHit, ...] = ()
    reason: str = ""
    top_score: float = 0.0
    score_margin: float = 0.0
    candidate_count: int = 0
    retrieval_strategy: str = "lexical"
    diagnostics: tuple[EventMemoryCandidate, ...] = ()
    fallback_reason: str = ""

    @property
    def candidates(self) -> list[str]:
        return [hit.event_id for hit in self.hits]

    @property
    def confidences(self) -> list[str]:
        return [hit.confidence for hit in self.hits]

    @property
    def evidence_units(self) -> list[str]:
        units: list[str] = []
        for hit in self.hits:
            for evidence in hit.evidence[:3]:
                record_index = evidence.get("record_index")
                if record_index is None:
                    continue
                units.append(f"{hit.event_id}:{record_index}")
        return units

    @property
    def candidate_diagnostics(self) -> list[dict[str, Any]]:
        return [candidate.as_trace_dict() for candidate in self.diagnostics[:10]]


def validate_event_inventory(payload: object) -> list[str]:
    """Return schema/grounding errors for an event asset without mutating it."""
    if not isinstance(payload, dict):
        return ["event asset root must be an object"]
    events = payload.get("events")
    if not isinstance(events, list) or not events:
        return ["events must be a non-empty array"]
    errors: list[str] = []
    ids: set[str] = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            errors.append(f"events[{index}] must be an object")
            continue
        event_id = str(event.get("event_id") or "")
        if not event_id or event_id in ids:
            errors.append(f"events[{index}] event_id missing or duplicated")
        ids.add(event_id)
        evidence = event.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"events[{index}] evidence missing")
            continue
        if event.get("confidence") == "high" and not any(
            isinstance(row, dict) and str(row.get("context") or "").strip() and str(row.get("dialogue") or "").strip()
            for row in evidence
        ):
            errors.append(f"events[{index}] high-confidence evidence incomplete")
    return errors


def _normalize(value: object) -> str:
    return _shared_normalize(value)


def _ngrams(text: str) -> set[str]:
    return _shared_ngrams(text)


def _has_specific_event_cue(query: str) -> bool:
    return _shared_has_specific_event_cue(query)


def _signal_cue_count(query: str) -> int:
    return _shared_signal_cue_count(query)


def _event_rows() -> list[dict[str, Any]]:
    if not isinstance(EVENT_MEMORY_DB, dict):
        return []
    events = EVENT_MEMORY_DB.get("events", [])
    return [event for event in events if isinstance(event, dict) and event.get("event_id")]


def _score_event(query: str, event: dict[str, Any]) -> float:
    return score_event(query, event)


def _score_evidence(query: str, evidence: dict[str, Any]) -> float:
    query_text = _normalize(query)
    query_terms = _ngrams(query)
    values = [
        _normalize(evidence.get(field))
        for field in ("context", "context_zh", "dialogue", "dialogue_zh")
        if evidence.get(field)
    ]
    score = 0.0
    for value in dict.fromkeys(item for item in values if item):
        overlap = _ngrams(value) & query_terms
        score += min(5.0, 0.5 * len(overlap))
        score += 2.0 * sum(term in query_text and term in value for term in _SIGNAL_TERMS)
    return score


def _rank_evidence(query: str, evidence: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    ranked = sorted(
        enumerate(evidence),
        key=lambda item: (-_score_evidence(query, item[1]), item[0]),
    )
    return tuple(item[1] for item in ranked)


def _build_hit(
    query: str,
    event: dict[str, Any],
    *,
    lexical_score: float,
    cosine_score: float | None = None,
    rerank_score: float | None = None,
) -> EventMemoryHit:
    evidence = event.get("evidence", [])
    evidence_rows = tuple(item for item in evidence if isinstance(item, dict)) if isinstance(evidence, list) else ()
    evidence_rows = _rank_evidence(query, evidence_rows)
    topics = event.get("topics", [])
    style_examples = event.get("style_examples", [])
    style_rows = tuple(item for item in style_examples if isinstance(item, dict)) if isinstance(style_examples, list) else ()
    score = rerank_score if rerank_score is not None else lexical_score
    return EventMemoryHit(
        event_id=str(event.get("event_id")),
        title=str(event.get("title") or "未命名事件"),
        summary=str(event.get("summary") or ""),
        category=str(event.get("category") or ""),
        confidence=str(event.get("confidence") or "low").lower(),
        score=round(float(score), 6),
        evidence=evidence_rows,
        topics=tuple(str(item) for item in topics if str(item).strip()) if isinstance(topics, list) else (),
        style_examples=style_rows,
        source_kind=event_source_kind(event),
        review_status=event_review_status(event),
        lexical_score=round(lexical_score, 3),
        cosine_score=round(cosine_score, 6) if cosine_score is not None else None,
        rerank_score=round(rerank_score, 6) if rerank_score is not None else None,
    )


def _eligible_events(min_confidence: str) -> list[dict[str, Any]]:
    minimum = _CONFIDENCE_ORDER.get(str(min_confidence).lower(), _CONFIDENCE_ORDER["high"])
    return [
        event
        for event in _event_rows()
        if _CONFIDENCE_ORDER.get(str(event.get("confidence") or "low").lower(), 0) >= minimum
    ]


def _rank_lexical(query: str, events: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    return rank_events(query, events)


def _lexical_result(
    query: str,
    *,
    top_k: int = 3,
    min_confidence: str = "high",
    retrieval_strategy: str = "lexical",
    fallback_reason: str = "",
) -> EventMemoryResult:
    """Retrieve and individually filter high-signal lexical event candidates."""
    if not _event_rows():
        return EventMemoryResult(status="unavailable", reason="event_asset_unavailable")
    query_text = str(query or "").strip()
    if not query_text:
        return EventMemoryResult(status="no_hit", reason="empty_query")
    if not _has_specific_event_cue(query_text):
        return EventMemoryResult(status="no_hit", reason="insufficient_event_cues")
    scored = _rank_lexical(query_text, _eligible_events(min_confidence))
    if not scored:
        return EventMemoryResult(
            status="no_hit",
            reason="no_relevant_event",
            retrieval_strategy=retrieval_strategy,
            fallback_reason=fallback_reason,
        )
    top_score = scored[0][0]
    score_margin = round(top_score - scored[1][0], 3) if len(scored) > 1 else top_score
    candidate_count = len(scored)
    if top_score < _MIN_ISOLATED_SCORE:
        return EventMemoryResult(
            status="no_hit",
            reason="low_score",
            top_score=top_score,
            score_margin=score_margin,
            candidate_count=candidate_count,
            retrieval_strategy=retrieval_strategy,
            fallback_reason=fallback_reason,
        )
    top_event_signal_count = event_signal_cue_count(query_text, scored[0][1])
    if top_score < _MIN_STRONG_SCORE and score_margin < _MIN_SCORE_MARGIN and top_event_signal_count < 2:
        return EventMemoryResult(
            status="no_hit",
            reason="ambiguous_candidates",
            top_score=top_score,
            score_margin=score_margin,
            candidate_count=candidate_count,
            retrieval_strategy=retrieval_strategy,
            fallback_reason=fallback_reason,
        )
    selected, curated_priority = qualified_events(
        scored,
        top_k=top_k,
        minimum_score=_MIN_ISOLATED_SCORE,
        max_score_gap=_LEXICAL_MAX_SCORE_GAP,
    )
    selected_ids = {str(event.get("event_id")) for _, event in selected}
    diagnostics: list[EventMemoryCandidate] = []
    for score, event in scored[:10]:
        event_id = str(event.get("event_id"))
        kept = event_id in selected_ids
        if kept:
            reason = ""
        elif score < _MIN_ISOLATED_SCORE:
            reason = "lexical_below_min"
        elif top_score - score > _LEXICAL_MAX_SCORE_GAP:
            reason = "outside_top_gap"
        elif curated_priority and event_source_kind(event) != "curated_story":
            reason = "legacy_shadowed_by_curated"
        else:
            reason = "top_k_limit"
        diagnostics.append(
            EventMemoryCandidate(
                event_id=event_id,
                source_kind=event_source_kind(event),
                lexical_score=score,
                kept=kept,
                drop_reason=reason,
            )
        )
    if not selected:
        return EventMemoryResult(
            status="no_hit",
            reason="no_relevant_event",
            top_score=top_score,
            score_margin=score_margin,
            candidate_count=candidate_count,
            retrieval_strategy=retrieval_strategy,
            diagnostics=tuple(diagnostics),
            fallback_reason=fallback_reason,
        )
    hits = tuple(_build_hit(query_text, event, lexical_score=score) for score, event in selected)
    return EventMemoryResult(
        status="hit",
        hits=hits,
        top_score=top_score,
        score_margin=score_margin,
        candidate_count=candidate_count,
        retrieval_strategy=retrieval_strategy,
        diagnostics=tuple(diagnostics),
        fallback_reason=fallback_reason,
    )


def _retrieval_mode(value: str | None = None) -> str:
    configured = str(value or os.environ.get("AKITO_EVENT_MEMORY_RETRIEVAL") or "lexical").strip().lower()
    return configured if configured in {"lexical", "hybrid"} else "lexical"


async def retrieve_event_memories(
    query: str,
    *,
    top_k: int = 3,
    min_confidence: str = "high",
    retrieval_ctx: RetrievalContext | None = None,
    retrieval_mode: str | None = None,
) -> EventMemoryResult:
    """Retrieve event memories with safe lexical or hybrid ranking."""
    mode = _retrieval_mode(retrieval_mode)
    lexical = _lexical_result(query, top_k=top_k, min_confidence=min_confidence)
    if mode != "hybrid" or lexical.status in {"unavailable"}:
        return lexical
    query_text = str(query or "").strip()
    if not query_text or not _has_specific_event_cue(query_text):
        return lexical
    events = _eligible_events(min_confidence)
    event_index = {str(event.get("event_id")): event for event in events}
    lexical_ranked = _rank_lexical(query_text, events)
    lexical_scores = {str(event.get("event_id")): score for score, event in lexical_ranked}
    lexical_ids = [str(event.get("event_id")) for _, event in lexical_ranked[:_HYBRID_RECALL_K]]
    ctx = retrieval_ctx or await build_retrieval_context(query_text, enable_expansion=len(query_text) >= 3)
    semantic = await retrieve_result("event_memory", ctx.query, _HYBRID_RECALL_K, ctx=ctx, use_rerank=False)
    if semantic.status == "unavailable":
        return _lexical_result(
            query_text,
            top_k=top_k,
            min_confidence=min_confidence,
            retrieval_strategy="lexical_fallback",
            fallback_reason=semantic.reason or "semantic_unavailable",
        )
    all_rows = _event_rows()
    cosine_scores = dict(semantic.cosine_scores or [])
    semantic_ids = [
        str(all_rows[index].get("event_id"))
        for index in semantic.ids
        if 0 <= index < len(all_rows) and str(all_rows[index].get("event_id")) in event_index
    ]
    candidate_ids = list(dict.fromkeys([*lexical_ids, *semantic_ids]))
    candidates = [event_index[event_id] for event_id in candidate_ids if event_id in event_index]
    if not candidates:
        return lexical
    from .api import rerank_documents

    ranked = await rerank_documents(
        query_text,
        [event_memory_retrieval_text(event) for event in candidates],
        top_n=len(candidates),
    )
    if ranked is None:
        return _lexical_result(
            query_text,
            top_k=top_k,
            min_confidence=min_confidence,
            retrieval_strategy="lexical_fallback",
            fallback_reason="rerank_unavailable",
        )
    reranked = [(float(score), candidates[index]) for index, score in ranked if 0 <= index < len(candidates)]
    if not reranked:
        return lexical
    top_rerank = reranked[0][0]
    qualifying: list[tuple[float, dict[str, Any]]] = []
    for score, event in reranked:
        threshold = _HYBRID_CURATED_MIN_SCORE if event_source_kind(event) == "curated_story" else _HYBRID_LEGACY_MIN_SCORE
        if score >= threshold and score >= top_rerank * _HYBRID_RELATIVE_SCORE:
            qualifying.append((score, event))
    curated = [item for item in qualifying if event_source_kind(item[1]) == "curated_story"]
    legacy = [item for item in qualifying if event_source_kind(item[1]) != "curated_story"]
    curated_priority = should_prioritize_curated(
        curated,
        legacy,
        margin=_HYBRID_CURATED_PRIORITY_MARGIN,
    )
    selected_pool = curated if curated_priority else qualifying
    selected = selected_pool[: max(1, min(3, int(top_k)))]
    selected_ids = {str(event.get("event_id")) for _, event in selected}
    diagnostics: list[EventMemoryCandidate] = []
    row_by_id = {str(event.get("event_id")): index for index, event in enumerate(all_rows)}
    for score, event in reranked[:10]:
        event_id = str(event.get("event_id"))
        source_kind = event_source_kind(event)
        threshold = _HYBRID_CURATED_MIN_SCORE if source_kind == "curated_story" else _HYBRID_LEGACY_MIN_SCORE
        kept = event_id in selected_ids
        if kept:
            reason = ""
        elif score < threshold:
            reason = "rerank_below_source_min"
        elif score < top_rerank * _HYBRID_RELATIVE_SCORE:
            reason = "rerank_below_relative_min"
        elif curated_priority and source_kind != "curated_story":
            reason = "legacy_shadowed_by_curated"
        else:
            reason = "top_k_limit"
        row_index = row_by_id.get(event_id)
        diagnostics.append(
            EventMemoryCandidate(
                event_id=event_id,
                source_kind=source_kind,
                lexical_score=lexical_scores.get(event_id, 0.0),
                cosine_score=cosine_scores.get(row_index) if row_index is not None else None,
                rerank_score=score,
                kept=kept,
                drop_reason=reason,
            )
        )
    hits = tuple(
        _build_hit(
            query_text,
            event,
            lexical_score=lexical_scores.get(str(event.get("event_id")), 0.0),
            cosine_score=cosine_scores.get(row_by_id.get(str(event.get("event_id")))),
            rerank_score=score,
        )
        for score, event in selected
    )
    if not hits:
        return EventMemoryResult(
            status="no_hit",
            reason="rerank_no_relevant_event",
            top_score=round(top_rerank, 6),
            candidate_count=len(candidates),
            retrieval_strategy="hybrid",
            diagnostics=tuple(diagnostics),
        )
    second_score = reranked[1][0] if len(reranked) > 1 else 0.0
    return EventMemoryResult(
        status="hit",
        hits=hits,
        top_score=round(top_rerank, 6),
        score_margin=round(top_rerank - second_score, 6),
        candidate_count=len(candidates),
        retrieval_strategy="hybrid",
        diagnostics=tuple(diagnostics),
    )


def format_event_memory_context(result: EventMemoryResult, *, max_evidence_chars: int = 360) -> str:
    """Render event cards as immutable historical evidence for a prompt."""
    if not result.hits:
        return ""
    lines = [
        "📚【原作事件记忆·只读证据】",
        "以下是从已整理原作资料召回的过去事件，只用于理解彰人的态度和反应思路。",
        "不得把事件证据改写成当前正在发生的事实；没有证据的细节必须明确说不确定。",
        "群友明确提到已证实经历时，可以用彰人的第一人称自然认领；不要提及检索、资料库或 event_id，也不要逐字复述原台词。",
    ]
    for hit in result.hits:
        is_reviewed_story = hit.source_kind == "curated_story" and hit.review_status == "reviewed"
        if is_reviewed_story:
            lines.append(f"- 已审核原作共同经历（置信度：{hit.confidence}）")
            summary_label = "已审核概括"
        else:
            lines.append(f"- 原作脚本自动整理片段（未人工复核；置信度：{hit.confidence}）")
            summary_label = "候选概括（以原始情境和台词为准）"
        if hit.summary:
            lines.append(f"  {summary_label}：{hit.summary}")
        if hit.category or hit.topics:
            labels = " / ".join(item for item in (hit.category, *hit.topics) if item)
            lines.append(f"  分类主题：{labels}")
        for unit_index, evidence in enumerate(hit.evidence[:3], 1):
            lines.append(f"  共同经历单元 {unit_index}：")
            context = str(evidence.get("context_zh") or evidence.get("context") or "").strip()
            dialogue = str(evidence.get("dialogue_zh") or evidence.get("dialogue") or "").strip()
            original = str(
                evidence.get("original_ja") or evidence.get("text_ja") or evidence.get("dialogue_ja") or ""
            ).strip()
            if context:
                lines.append(f"  原始情境：{context[:max_evidence_chars]}")
            if dialogue:
                lines.append(f"  原始台词证据：{dialogue[:max_evidence_chars]}")
            if original and original not in {context, dialogue}:
                lines.append(f"  日文原文对照：{original[:max_evidence_chars]}")
        style_lines = []
        for style in hit.style_examples[:2]:
            refs = style.get("evidence_refs")
            if not isinstance(refs, list) or not refs:
                continue
            example = str(
                style.get("text_zh") or style.get("example_zh") or style.get("dialogue") or style.get("text") or ""
            ).strip()
            if example:
                style_lines.append(example[:max_evidence_chars])
        if style_lines:
            lines.append("  彰人口吻参考（只借鉴表达方式，不新增事实）：" + " / ".join(style_lines))
    return "\n".join(lines)


async def build_event_memory_context(
    query: str,
    *,
    mode: str,
    top_k: int = 3,
    retrieval_ctx: RetrievalContext | None = None,
) -> tuple[str, EventMemoryResult]:
    """Resolve one request under an M2 mode; ``shadow`` never returns prompt text."""
    normalized_mode = str(mode or "off").lower()
    if not mode_is_shadowing(normalized_mode):
        return "", EventMemoryResult(status="disabled", reason="m2_disabled")
    result = await retrieve_event_memories(
        query,
        top_k=top_k,
        min_confidence="high",
        retrieval_ctx=retrieval_ctx,
    )
    if normalized_mode in {"canary", "on"}:
        return format_event_memory_context(result), result
    return "", result
