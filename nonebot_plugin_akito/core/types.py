"""共享持久化记录的静态类型定义，不改变运行时字典语义。"""

from __future__ import annotations

from typing import Any, Optional, TypedDict

JsonRecord = dict[str, Any]
MessageRow = tuple[int, str, str, str, Optional[str], str]


class WeeklyInvestmentRecord(TypedDict):
    week: str
    supply_count: int
    supply_spent: int
    gift_spent: int


class BaseUserRecord(TypedDict, total=False):
    points: int
    display_name: str
    weekly_investment: WeeklyInvestmentRecord


class _StoredGroupRecord(TypedDict):
    user_ids: list[str]
    rpg: JsonRecord


class GroupRecord(_StoredGroupRecord, total=False):
    users: dict[str, BaseUserRecord]
    _global_users: dict[str, BaseUserRecord]
    intimacy: dict[str, int]
    counts: dict[str, int]
    wedding_invitations: dict[str, JsonRecord]


class GameData(TypedDict):
    schema_version: int
    users: dict[str, BaseUserRecord]
    intimacy: dict[str, int]
    counts: dict[str, int]
    wedding_invitations: dict[str, JsonRecord]
    groups: dict[str, GroupRecord]


class HistoryMessage(TypedDict, total=False):
    role: str
    content: Any


class TempImplant(TypedDict, total=False):
    id: str
    content: str
    expire_at: float


class MemorySession(TypedDict, total=False):
    history: list[HistoryMessage]
    temp_implants: list[TempImplant]
    long_term_facts: list[str]


class Turn(TypedDict, total=False):
    """Structured input envelope for one conversation turn."""

    request_id: str
    session_key: str
    message_id: str | int | None
    user_id: str
    group_id: str | int | None
    sender_nickname: str
    content: str
    has_image: bool
    has_reply: bool
    reply_target_is_toya: bool


class ConversationState(TypedDict, total=False):
    """Stable state passed between context and memory orchestration layers."""

    session_key: str
    recent_messages: list[HistoryMessage]
    conversation_summary: str
    active_event_id: str
    long_term_facts: list[str]
    temp_implants: list[TempImplant]


class ContextBlock(TypedDict, total=False):
    """One prompt input block with enough metadata for future budgeting."""

    kind: str
    content: str
    source: str
    priority: int
    ttl_seconds: int
    token_estimate: int


class ToolResult(TypedDict, total=False):
    """Normalized result envelope for search and future tools."""

    name: str
    status: str
    query: str
    content: str
    source: str
    latency_ms: float


class ResponseEnvelope(TypedDict, total=False):
    """Model response contract independent of transport formatting."""

    request_id: str
    dialogue: str
    action: str
    inner_os: str
    memory_events: list[dict[str, Any]]
    finish_reason: str
