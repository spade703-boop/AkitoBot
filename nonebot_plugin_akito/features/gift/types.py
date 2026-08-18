"""送礼玩法附加到共享用户记录上的字段类型。"""

from __future__ import annotations

from typing import TypedDict

from ...core.types import BaseUserRecord


class GiftUserRecord(BaseUserRecord, total=False):
    last_sign_in: str
    last_gift: str
    steal_date: str
    steal_used: int
    robbed_date: str
    robbed_count: int
    protect_until: float
    wedding_first_bonus_claimed: bool


class WeddingInvitationRecord(TypedDict, total=False):
    sender_id: str
    recipient_id: str
    date: str
    historical: bool
    has_1314: bool
    count: int
    last_sender_id: str
    last_recipient_id: str
    last_date: str
    bonus: int
    amount: int
