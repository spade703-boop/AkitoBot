"""Typed access to the RPG view stored on a group record."""

from __future__ import annotations

from typing import cast

from ...core.types import GroupRecord
from .types import RpgState


def _rpg_state(group: GroupRecord) -> RpgState:
    state = group.get("rpg")
    if not isinstance(state, dict):
        state = {}
        group["rpg"] = state
    return cast(RpgState, state)
