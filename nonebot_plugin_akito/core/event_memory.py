"""Read-only retrieval of evidence-backed Akito/Toya episodic memories."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
import unicodedata

from .data import EVENT_MEMORY_DB
from .rollout import mode_is_shadowing

_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}
_MIN_STRONG_SCORE = 4.5
_MIN_ISOLATED_SCORE = 3.0
_MIN_SCORE_MARGIN = 1.0
_GENERIC_TERMS = {"彰人", "冬弥", "青柳", "东云", "東雲", "toya", "akito"}
_QUERY_STOP_PHRASES = (
    "你还记得",
    "还记得",
    "你们以前",
    "以前",
    "那一次",
    "那次",
    "那天",
    "上次",
    "那个事情",
    "那个事",
    "后来",
    "当时",
    "发生过什么",
    "发生了什么",
    "发生过",
    "怎么样了",
    "怎么样",
    "怎么说的",
    "怎么说",
    "说说",
    "是不是",
    "有没有",
    "什么",
    "青柳冬弥",
    "东云彰人",
    "東雲彰人",
    "冬弥",
    "彰人",
    "青柳",
    "东云",
    "東雲",
    "你们",
    "你自己",
    "自己",
    "你",
    "他",
    "的",
    "了",
    "吗",
    "吧",
    "呢",
    "挺",
    "有点",
)
_QUERY_ALIASES = (
    ("庆生", "生日"),
    ("庆祝", "生日"),
    ("生日歌", "生日唱歌"),
    ("吃饭", "聚餐"),
    ("一起吃饭", "聚餐"),
    ("闹过头", "张扬"),
    ("别闹", "张扬"),
    ("努力学习", "学习"),
)
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


@dataclass(frozen=True)
class EventMemoryResult:
    status: str
    hits: tuple[EventMemoryHit, ...] = ()
    reason: str = ""
    top_score: float = 0.0
    score_margin: float = 0.0
    candidate_count: int = 0

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
    text = unicodedata.normalize("NFKC", str(value or "")).lower().strip()
    text = re.sub(r"\s+", "", text)
    for source, target in _QUERY_ALIASES:
        text = text.replace(source, target)
    return text


def _ngrams(text: str) -> set[str]:
    compact = _normalize(text)
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", compact))
    for match in re.findall(r"[\u4e00-\u9fff]+", compact):
        for size in (2, 3, 4):
            terms.update(match[index : index + size] for index in range(max(0, len(match) - size + 1)))
    return {term for term in terms if term and term not in _GENERIC_TERMS}


def _has_specific_event_cue(query: str) -> bool:
    cue_text = _normalize(query)
    for phrase in _QUERY_STOP_PHRASES:
        cue_text = cue_text.replace(phrase, "")
    latin_terms = re.findall(r"[a-z0-9]{2,}", cue_text)
    chinese_text = "".join(re.findall(r"[\u4e00-\u9fff]+", cue_text))
    return bool(latin_terms or len(chinese_text) >= 2)


def _signal_cue_count(query: str) -> int:
    query_text = _normalize(query)
    return sum(term in query_text for term in _SIGNAL_TERMS)


def _event_rows() -> list[dict[str, Any]]:
    if not isinstance(EVENT_MEMORY_DB, dict):
        return []
    events = EVENT_MEMORY_DB.get("events", [])
    return [event for event in events if isinstance(event, dict) and event.get("event_id")]


def _score_event(query: str, event: dict[str, Any]) -> float:
    query_text = _normalize(query)
    query_terms = _ngrams(query)
    score = 0.0
    title = _normalize(event.get("title", ""))
    summary = _normalize(event.get("summary", ""))
    category = _normalize(event.get("category", ""))
    topic_values = event.get("topics", [])
    keywords = event.get("keywords", [])
    values = [title, summary, category]
    if isinstance(topic_values, list):
        values.extend(_normalize(item) for item in topic_values)
    if isinstance(keywords, list):
        values.extend(_normalize(item) for item in keywords)
    evidence = event.get("evidence", [])
    if isinstance(evidence, list):
        for item in evidence:
            if not isinstance(item, dict):
                continue
            values.extend(
                _normalize(item.get(field))
                for field in ("context", "context_zh", "dialogue", "dialogue_zh")
                if item.get(field)
            )
    for value in dict.fromkeys(item for item in values if item):
        if len(value) >= 2 and value in query_text:
            score += 3.0 if value == title else 1.5
        overlap = _ngrams(value) & query_terms
        score += min(3.0, 0.5 * len(overlap))
        score += 2.0 * sum(term in query_text and term in value for term in _SIGNAL_TERMS)
    return score


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


def retrieve_event_memories(
    query: str,
    *,
    top_k: int = 3,
    min_confidence: str = "high",
) -> EventMemoryResult:
    """Retrieve high-signal event cards without embeddings or mutable memory writes."""
    if not _event_rows():
        return EventMemoryResult(status="unavailable", reason="event_asset_unavailable")
    query_text = str(query or "").strip()
    if not query_text:
        return EventMemoryResult(status="no_hit", reason="empty_query")
    if not _has_specific_event_cue(query_text):
        return EventMemoryResult(status="no_hit", reason="insufficient_event_cues")
    minimum = _CONFIDENCE_ORDER.get(str(min_confidence).lower(), _CONFIDENCE_ORDER["high"])
    scored: list[EventMemoryHit] = []
    for event in _event_rows():
        confidence = str(event.get("confidence") or "low").lower()
        if _CONFIDENCE_ORDER.get(confidence, 0) < minimum:
            continue
        score = _score_event(query_text, event)
        if score <= 0:
            continue
        evidence = event.get("evidence", [])
        evidence_rows = tuple(item for item in evidence if isinstance(item, dict)) if isinstance(evidence, list) else ()
        evidence_rows = _rank_evidence(query_text, evidence_rows)
        topics = event.get("topics", [])
        style_examples = event.get("style_examples", [])
        style_rows = (
            tuple(item for item in style_examples if isinstance(item, dict)) if isinstance(style_examples, list) else ()
        )
        scored.append(
            EventMemoryHit(
                event_id=str(event.get("event_id")),
                title=str(event.get("title") or "未命名事件"),
                summary=str(event.get("summary") or ""),
                category=str(event.get("category") or ""),
                confidence=confidence,
                score=round(score, 3),
                evidence=evidence_rows,
                topics=tuple(str(item) for item in topics if str(item).strip()) if isinstance(topics, list) else (),
                style_examples=style_rows,
            )
        )
    scored.sort(key=lambda hit: (-hit.score, -_CONFIDENCE_ORDER.get(hit.confidence, 0), hit.event_id))
    if not scored:
        return EventMemoryResult(status="no_hit", reason="no_relevant_event")
    top_score = scored[0].score
    score_margin = round(top_score - scored[1].score, 3) if len(scored) > 1 else top_score
    candidate_count = len(scored)
    if top_score < _MIN_ISOLATED_SCORE:
        return EventMemoryResult(
            status="no_hit",
            reason="low_score",
            top_score=top_score,
            score_margin=score_margin,
            candidate_count=candidate_count,
        )
    if top_score < _MIN_STRONG_SCORE and score_margin < _MIN_SCORE_MARGIN and _signal_cue_count(query_text) < 2:
        return EventMemoryResult(
            status="no_hit",
            reason="ambiguous_candidates",
            top_score=top_score,
            score_margin=score_margin,
            candidate_count=candidate_count,
        )
    hits = tuple(scored[: max(1, int(top_k))])
    return EventMemoryResult(
        status="hit",
        hits=hits,
        top_score=top_score,
        score_margin=score_margin,
        candidate_count=candidate_count,
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
        lines.append(f"- 已确认共同经历（置信度：{hit.confidence}）")
        if hit.summary:
            lines.append(f"  已确认概括：{hit.summary}")
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


def build_event_memory_context(query: str, *, mode: str, top_k: int = 3) -> tuple[str, EventMemoryResult]:
    """Resolve one request under an M2 mode; ``shadow`` never returns prompt text."""
    normalized_mode = str(mode or "off").lower()
    if not mode_is_shadowing(normalized_mode):
        return "", EventMemoryResult(status="disabled", reason="m2_disabled")
    result = retrieve_event_memories(query, top_k=top_k, min_confidence="high")
    if normalized_mode in {"canary", "on"}:
        return format_event_memory_context(result), result
    return "", result
