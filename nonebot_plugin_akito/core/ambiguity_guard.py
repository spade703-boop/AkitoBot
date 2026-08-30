"""Deterministic preflight guard for underspecified plot references."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import re
from typing import Any

_EVENT_REFERENCE_TERMS = (
    "那次",
    "那一次",
    "那件事",
    "那个事情",
    "那个事",
    "那天",
    "上次",
    "之前",
    "以前",
    "当时",
    "后来",
    "之后",
    "那时候",
    "那一回",
    "刚才",
    "前面说的",
    "之前提到的",
)
_OMITTED_PERSON_TERMS = (
    "他",
    "她",
    "他们",
    "她们",
    "那个人",
    "那家伙",
    "对方",
    "谁",
)
_FOLLOW_UP_TERMS = (
    "后来呢",
    "后来",
    "然后呢",
    "然后",
    "之后呢",
    "之后",
    "那次呢",
    "那件事呢",
    "那天呢",
    "上次呢",
    "之前呢",
    "以前呢",
    "当时呢",
    "那后来",
    "后来怎么样",
    "后来怎么了",
    "当时怎么说",
    "当时说了什么",
    "怎么说的",
    "发生了什么",
    "发生过什么",
    "怎么样了",
    "怎么样",
    "你还记得",
    "你记得",
    "还记得吗",
    "继续说",
    "接着呢",
)
_BROAD_HISTORY_TERMS = (
    "你们以前",
    "你们之间",
    "我们以前",
    "我们之间",
    "你和",
    "我和",
)
_SPECIFIC_EVENT_TERMS = (
    "文化祭",
    "生日",
    "惊喜",
    "甜食",
    "唱歌",
    "胜负",
    "聚餐",
    "学校",
    "热闹",
    "规则",
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
    "早上",
    "练习",
    "演出",
    "舞蹈",
    "露营",
    "烟火",
    "足球",
    "笔记",
    "帐篷",
    "蘑菇",
    "养猫",
    "约定",
    "创造",
    "sekai",
    "radblast",
)

_CLARIFICATION_TEMPLATES = (
    "……你说的是哪一次？给我一个人名、事件或时间点，我才能接上。",
    "“那件事”是哪件？别只给我一个代词，补一句前情。",
    "后来怎么了是指哪段？报个关键词，我不想靠猜。",
    "我知道你在问前面的事，但现在缺少定位。说清楚是哪一次。",
)


@dataclass(frozen=True)
class AmbiguitySignals:
    """Privacy-safe lexical signals found in one user message."""

    event_references: tuple[str, ...] = ()
    omitted_person_markers: tuple[str, ...] = ()
    follow_up_markers: tuple[str, ...] = ()
    specific_event_cues: tuple[str, ...] = ()
    broad_history_markers: tuple[str, ...] = ()

    @property
    def has_event_reference(self) -> bool:
        return bool(self.event_references)

    @property
    def has_omitted_person(self) -> bool:
        return bool(self.omitted_person_markers)

    @property
    def is_follow_up(self) -> bool:
        return bool(self.follow_up_markers)

    @property
    def has_specific_event_cue(self) -> bool:
        return bool(self.specific_event_cues)

    @property
    def candidate(self) -> bool:
        return self.has_event_reference and (self.has_omitted_person or self.is_follow_up)

    def trace_names(self) -> list[str]:
        names: list[str] = []
        if self.has_event_reference:
            names.append("event_reference")
        if self.has_omitted_person:
            names.append("omitted_person")
        if self.is_follow_up:
            names.append("follow_up")
        return names


@dataclass(frozen=True)
class AmbiguityGuardDecision:
    """Result of preflight evaluation before retrieval or model calls."""

    triggered: bool
    reason: str = ""
    signals: AmbiguitySignals = field(default_factory=AmbiguitySignals)
    clarification: str = ""

    @property
    def should_guard(self) -> bool:
        return self.triggered


def is_ambiguity_guard_enabled() -> bool:
    """Read the runtime switch; the guard is enabled unless explicitly disabled."""
    value = os.environ.get("AKITO_AMBIGUITY_GUARD", "on").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


def _find_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for term in terms if term in text)


def detect_ambiguity_signals(text: str) -> AmbiguitySignals:
    """Extract high-confidence ambiguity signals without any I/O or model call."""
    normalized = re.sub(r"\s+", "", str(text or "")).strip()
    event_references = _find_terms(normalized, _EVENT_REFERENCE_TERMS)
    omitted_person_markers = _find_terms(normalized, _OMITTED_PERSON_TERMS)
    follow_up_markers = _find_terms(normalized, _FOLLOW_UP_TERMS)
    broad_history_markers = _find_terms(normalized, _BROAD_HISTORY_TERMS)

    specific_event_cues = _find_terms(normalized.lower(), _SPECIFIC_EVENT_TERMS)
    if specific_event_cues:
        # Keep the trace categorical; the original user text is never retained.
        specific_event_cues = ("specific_event_context",)

    return AmbiguitySignals(
        event_references=event_references,
        omitted_person_markers=omitted_person_markers,
        follow_up_markers=follow_up_markers,
        specific_event_cues=specific_event_cues,
        broad_history_markers=broad_history_markers,
    )


def select_clarification_template(signals: AmbiguitySignals) -> str:
    """Choose a stable in-character clarification without invoking a model."""
    if any(marker in {"后来呢", "后来", "然后呢", "然后", "之后呢", "之后", "后来怎么样", "后来怎么了", "怎么样了"} for marker in signals.follow_up_markers):
        return _CLARIFICATION_TEMPLATES[2]
    if any(marker in {"当时怎么说", "当时说了什么", "怎么说的"} for marker in signals.follow_up_markers):
        return _CLARIFICATION_TEMPLATES[1]
    if any(marker in {"你还记得", "还记得吗"} for marker in signals.follow_up_markers):
        return _CLARIFICATION_TEMPLATES[0]
    return _CLARIFICATION_TEMPLATES[3]


def evaluate_ambiguity_guard(
    text: str,
    *,
    has_history: bool = False,
    has_image: bool = False,
    has_valid_temporary_state: bool = False,
    explicit_web_intent: bool = False,
    query_intent: Any = None,
    enabled: bool | None = None,
) -> AmbiguityGuardDecision:
    """Evaluate whether a main-chat turn needs a deterministic clarification."""
    signals = detect_ambiguity_signals(text)
    if enabled is None:
        enabled = is_ambiguity_guard_enabled()
    if query_intent is not None:
        explicit_web_intent = bool(
            explicit_web_intent
            or getattr(query_intent, "intent", "") == "web_search"
            or getattr(query_intent, "explicit_search", False)
        )
    if not enabled:
        return AmbiguityGuardDecision(False, "disabled", signals)
    if has_history:
        return AmbiguityGuardDecision(False, "history_available", signals)
    if has_image:
        return AmbiguityGuardDecision(False, "image_available", signals)
    if has_valid_temporary_state:
        return AmbiguityGuardDecision(False, "temporary_state_available", signals)
    if explicit_web_intent:
        return AmbiguityGuardDecision(False, "explicit_web_intent", signals)
    if signals.has_specific_event_cue:
        return AmbiguityGuardDecision(False, "specific_event_context", signals)
    if signals.broad_history_markers:
        return AmbiguityGuardDecision(False, "broad_history_context", signals)
    if not signals.candidate:
        return AmbiguityGuardDecision(False, "no_high_confidence_ambiguity", signals)
    return AmbiguityGuardDecision(
        True,
        "ambiguous_event_reference",
        signals,
        select_clarification_template(signals),
    )


# Concise aliases make the detector convenient for callers and test fixtures.
detect_ambiguity = detect_ambiguity_signals
choose_clarification_template = select_clarification_template


__all__ = [
    "AmbiguitySignals",
    "AmbiguityGuardDecision",
    "choose_clarification_template",
    "detect_ambiguity",
    "detect_ambiguity_signals",
    "evaluate_ambiguity_guard",
    "is_ambiguity_guard_enabled",
    "select_clarification_template",
]
