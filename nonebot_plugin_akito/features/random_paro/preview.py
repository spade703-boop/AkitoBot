"""Deterministic preview fixtures used by superuser-only debug commands."""

from __future__ import annotations

from .ranking_images import (
    build_egg_rank_pil_image_from_stats,
    build_paro_rank_pil_image_from_stats,
    build_personal_paro_pil_image_from_user_stats,
)
from .stats import (
    _new_user_egg_history,
    _record_user_draw_stats,
    _record_user_egg_history_entry,
    _today_str,
)
from .store import _new_group_stats, _new_period_stats, _new_user_stats
from .views import (
    render_egg_rank_image_from_stats,
    render_paro_rank_image_from_stats,
    render_personal_paro_image_from_user_stats,
)


def build_rank_preview_stats(scope: str) -> tuple[dict, dict]:
    today = _today_str()
    group_stats = _new_group_stats(today)
    group_stats["profiles"] = {str(10000 + index): f"测试群友{chr(64 + index)}" for index in range(1, 7)}
    period_stats = _new_period_stats(date=today if scope == "daily" else None)
    period_stats.update(
        {
            "total_draws": 36 if scope == "daily" else 128,
            "user_draw_counts": {"10001": 12, "10002": 8, "10003": 8, "10004": 4, "10005": 3, "10006": 1},
            "akito_hits": {"Callboy彰": 9, "白骑": 8, "王子彰": 8, "WL2彰": 8, "白恶魔": 8},
            "toya_hits": {"Callboy冬": 11, "白百合": 10, "王子冬": 10, "WL2冬": 10, "黑骑": 10},
            "egg_user_counts": {"10001": 4, "10002": 3, "10003": 3, "10004": 2},
            "foxrabbit_total": 3,
            "foxbun_total": 5,
            "fox_total": 4,
            "rabbit_total": 1,
        }
    )
    return group_stats, period_stats


async def render_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = build_rank_preview_stats(scope)
    return await render_paro_rank_image_from_stats(group_stats, period_stats, scope)


def build_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = build_rank_preview_stats(scope)
    return build_paro_rank_pil_image_from_stats(group_stats, period_stats, scope)


async def render_egg_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = build_rank_preview_stats(scope)
    return await render_egg_rank_image_from_stats(group_stats, period_stats, scope)


def build_egg_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = build_rank_preview_stats(scope)
    return build_egg_rank_pil_image_from_stats(group_stats, period_stats, scope)


def _build_personal_preview_stats() -> tuple[dict, dict]:
    user_stats = _new_user_stats()
    egg_history = _new_user_egg_history()
    results = [
        ("Callboy彰", "Callboy冬", True, None),
        ("白骑", "王子冬", True, None),
        ("白骑", "王子冬", False, None),
        ("王子彰", "白百合", True, None),
        ("WL2彰", "WL2冬", False, None),
        ("Callboy彰", "Callboy冬", False, None),
        ("法师彰", "青鸟", True, None),
        ("白恶魔", "黑骑", False, None),
        ("Callboy彰", "Callboy冬", False, "foxbun"),
    ]
    _record_user_draw_stats(user_stats, results=results)
    for akito, toya, is_egg, fox_type in results:
        if is_egg:
            _record_user_egg_history_entry(egg_history, akito_name=akito, toya_name=toya, egg_type="cooking")
        elif fox_type == "foxbun":
            _record_user_egg_history_entry(egg_history, akito_name=akito, toya_name=toya, egg_type="foxbun")
    return user_stats, egg_history


async def render_personal_preview_image() -> bytes:
    user_stats, egg_history = _build_personal_preview_stats()
    return await render_personal_paro_image_from_user_stats("10001", "测试群友甲甲甲甲", user_stats, egg_history)


def build_personal_preview_image() -> bytes:
    user_stats, egg_history = _build_personal_preview_stats()
    return build_personal_paro_pil_image_from_user_stats("10001", "测试群友甲甲甲甲", user_stats, egg_history)


__all__ = [name for name in globals() if name.startswith("build_") or name.startswith("render_")]
