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
