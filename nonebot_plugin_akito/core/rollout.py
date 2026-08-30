"""Runtime rollout controls for the M1 context planner and M2 event memory."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any

_VALID_MODES = {"off", "shadow", "canary", "on"}
_ARM_MODES = {
    "control": ("off", "off"),
    "m1": ("canary", "off"),
    "m2": ("off", "canary"),
    "combined": ("canary", "canary"),
}


@dataclass(frozen=True)
class RolloutConfig:
    """Resolved modes for a single group or private conversation."""

    arm: str
    m1_context_mode: str
    m2_memory_mode: str
    m3_tool_mode: str = "off"


def _normalize_mode(value: object, default: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in _VALID_MODES else default


def _base_config() -> RolloutConfig:
    return RolloutConfig(
        arm="default",
        m1_context_mode=_normalize_mode(os.environ.get("AKITO_M1_CONTEXT_MODE"), "shadow"),
        m2_memory_mode=_normalize_mode(os.environ.get("AKITO_M2_MEMORY_MODE"), "off"),
        m3_tool_mode=_normalize_mode(os.environ.get("AKITO_M3_TOOL_MODE"), "off"),
    )


def _tool_group_modes() -> dict[str, str]:
    raw = os.environ.get("AKITO_M3_TOOL_GROUPS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(group_id): str(mode).strip().lower()
        for group_id, mode in parsed.items()
        if str(mode).strip().lower() in _VALID_MODES
    }


def _group_arms() -> dict[str, str]:
    raw = os.environ.get("AKITO_EXPERIMENT_GROUPS", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        str(group_id): str(arm).strip().lower()
        for group_id, arm in parsed.items()
        if str(arm).strip().lower() in _ARM_MODES
    }


def resolve_rollout(group_id: int | str | None = None) -> RolloutConfig:
    """Resolve the experiment arm without changing any persistent state.

    ``AKITO_EXPERIMENT_GROUPS`` is an optional JSON mapping such as
    ``{"123": "combined"}``. Unmapped groups keep the explicit mode env vars,
    which defaults to M1 shadow and M2 off for a safe rollout.
    """
    base = _base_config()
    arm = _group_arms().get(str(group_id)) if group_id is not None else None
    tool_mode = _tool_group_modes().get(str(group_id)) if group_id is not None else None
    if arm is None:
        return RolloutConfig(
            arm=base.arm,
            m1_context_mode=base.m1_context_mode,
            m2_memory_mode=base.m2_memory_mode,
            m3_tool_mode=tool_mode or base.m3_tool_mode,
        )
    m1_mode, m2_mode = _ARM_MODES[arm]
    return RolloutConfig(
        arm=arm,
        m1_context_mode=m1_mode,
        m2_memory_mode=m2_mode,
        m3_tool_mode=tool_mode or base.m3_tool_mode,
    )


def mode_is_active(mode: str) -> bool:
    """Return whether a mode changes behavior rather than only observing."""
    return str(mode).lower() in {"canary", "on"}


def mode_is_shadowing(mode: str) -> bool:
    """Return whether retrieval/selection should run without prompt mutation."""
    return str(mode).lower() in {"shadow", "canary", "on"}


def rollout_as_dict(config: RolloutConfig) -> dict[str, Any]:
    return {
        "experiment_arm": config.arm,
        "m1_context_mode": config.m1_context_mode,
        "m2_memory_mode": config.m2_memory_mode,
        "m3_tool_mode": config.m3_tool_mode,
    }
