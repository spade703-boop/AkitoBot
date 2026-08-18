"""Page-data builders and HTML orchestration for random_paro."""

from __future__ import annotations

import json
from pathlib import Path

from nonebot.log import logger

from .assets import avatar_uri, find_foxrabbit_asset, fox_icon_uris, path_to_uri
from .render import render_random_paro_page
from .stats import (
    _build_personal_cooking_pair_items,
    _collect_user_egg_history,
    _count_total_cooking_hits,
    _get_or_create_group_stats,
    _sorted_counter_items,
    _sorted_ranked_items,
    _today_str,
)
from .store import _save_stats

_HTML_PAGE_CACHE: dict[str, tuple[str, bytes]] = {}


def _path_to_uri(path: Path | None) -> str:
    return path_to_uri(path)


def _find_foxrabbit_asset(name: str) -> Path | None:
    return find_foxrabbit_asset(name)


def _avatar_uri(character: str, name: str) -> str:
    return avatar_uri(character, name)


def _fox_icon_uris(fox_type: str) -> list[str]:
    return fox_icon_uris(fox_type)


def _subtitle_for_scope(period_stats: dict, scope: str) -> str:
    return f"{period_stats.get('date')} 00:00 起累计" if scope == "daily" else "功能上线后累计"


def _build_user_contract(counter: dict[str, int], profiles: dict[str, str], *, limit: int = 5) -> list[dict]:
    users = [
        {"name": profiles.get(user_id) or f"用户{user_id}", "count": count}
        for user_id, count in _sorted_counter_items(counter)[:limit]
    ]
    return users or [{"name": "暂无", "count": 0}]


def _build_character_contract(
    counter: dict[str, int],
    *,
    title: str,
    cls: str,
    last_hit_seq: dict[str, int] | None = None,
    limit: int = 3,
) -> dict:
    grouped: list[tuple[list[str], int]] = []
    for name, count in _sorted_ranked_items(counter, last_hit_seq):
        if grouped and grouped[-1][1] == count:
            grouped[-1][0].append(name)
        else:
            grouped.append(([name], count))

    items = []
    character = "彰人" if cls == "akito" else "冬弥"
    for names, count in grouped[:limit]:
        visible_names = names[:3]
        items.append(
            {
                "names": visible_names,
                "count": count,
                "more": len(names) > 3,
                "icons": [_avatar_uri(character, name) for name in visible_names if name != "暂无"],
            }
        )
    if not items:
        items.append({"names": ["暂无"], "count": 0, "more": False, "icons": []})
    return {"cls": cls, "title": title, "en": "AKITO" if cls == "akito" else "TOYA", "items": items}


def _build_fox_rows_contract(period_stats: dict) -> list[dict]:
    entries = [
        ("foxrabbit", "狐兔", period_stats["foxrabbit_total"], ["狐", "兔"]),
        ("foxbun", "狐兔饭", period_stats["foxbun_total"], ["狐", "兔"]),
        ("fox", "狐狸", period_stats["fox_total"], ["狐"]),
        ("rabbit", "兔子", period_stats["rabbit_total"], ["兔"]),
    ]
    return [
        {"name": label, "kinds": kinds, "count": count, "icons": _fox_icon_uris(fox_type)}
        for _index, (fox_type, label, count, kinds) in sorted(
            enumerate(entries), key=lambda item: (-item[1][2], item[0])
        )
    ]


def _build_profile_pair_contract(egg_history: dict) -> list[dict]:
    items = []
    for pair_item in _build_personal_cooking_pair_items(egg_history):
        items.append(
            {
                "count": pair_item["count"],
                "akito_img": _avatar_uri("彰人", pair_item["akito_name"]),
                "toya_img": _avatar_uri("冬弥", pair_item["toya_name"]),
                "akito_initial": pair_item["akito_name"][:1] or "彰",
                "toya_initial": pair_item["toya_name"][:1] or "冬",
                "akito_name": pair_item["akito_name"],
                "toya_name": pair_item["toya_name"],
            }
        )
    return items


def _build_paro_rank_page_data_from_stats(group_stats: dict, period_stats: dict, scope: str) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "DAILY DRAW REPORT" if scope == "daily" else "HISTORY DRAW REPORT",
        "title": "每日派生排行榜" if scope == "daily" else "历史派生排行榜",
        "pill": _subtitle_for_scope(period_stats, scope),
        "total": period_stats["total_draws"],
        "users_title": "抽取次数最多的前 5 人",
        "users": _build_user_contract(period_stats["user_draw_counts"], group_stats["profiles"], limit=5),
        "characters": [
            _build_character_contract(
                period_stats["akito_hits"],
                title="被抽到最多次的彰人派生 TOP 3",
                cls="akito",
                last_hit_seq=period_stats.get("akito_last_hit_seq"),
            ),
            _build_character_contract(
                period_stats["toya_hits"],
                title="被抽到最多次的冬弥派生 TOP 3",
                cls="toya",
                last_hit_seq=period_stats.get("toya_last_hit_seq"),
            ),
        ],
        "footer_right": "",
    }


def _build_egg_rank_page_data_from_stats(group_stats: dict, period_stats: dict, scope: str) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "DAILY COOKING REPORT" if scope == "daily" else "HISTORY COOKING REPORT",
        "title": "每日做饭排行榜" if scope == "daily" else "历史做饭排行榜",
        "pill": _subtitle_for_scope(period_stats, scope),
        "users_title": "做饭 + 狐兔饭触发最多的前 5 人",
        "users": _build_user_contract(period_stats["egg_user_counts"], group_stats["profiles"], limit=5),
        "eggs": _build_fox_rows_contract(period_stats),
        "footer_right": "",
    }


def _build_personal_paro_page_data_from_user_stats(
    user_id: str, display_name: str, user_stats: dict, egg_history: dict
) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "PLAYER PROFILE",
        "title": display_name or f"用户{user_id}",
        "pill": "",
        "stats": [
            {"label": "累计抽取派生次数", "value": user_stats["draw_count"]},
            {"label": "累计抽到做饭的次数", "value": _count_total_cooking_hits(egg_history)},
        ],
        "characters": [
            _build_character_contract(
                user_stats["akito_hits"],
                title="抽到最多的彰人派生 TOP 3",
                cls="akito",
                last_hit_seq=user_stats.get("akito_last_hit_seq"),
            ),
            _build_character_contract(
                user_stats["toya_hits"],
                title="抽到最多的冬弥派生 TOP 3",
                cls="toya",
                last_hit_seq=user_stats.get("toya_last_hit_seq"),
            ),
        ],
        "pending_dishes": _build_profile_pair_contract(egg_history),
        "dish_empty_text": "还没有抽到做饭彩蛋",
        "fox_rabbit_count": egg_history["foxbun_count"],
        "fox_rabbit_icons": _fox_icon_uris("foxbun"),
        "footer_right": "",
    }


def _build_draw_result_page_data(
    results: list[tuple[str, str, bool, str | None]], remaining: int, nickname: str
) -> dict:
    items = []
    dishes = []
    foxbun_hit = False
    for akito_name, toya_name, is_egg, fox_type in results:
        if fox_type:
            items.append({"type": "fox", "fox_type": fox_type, "imgs": _fox_icon_uris(fox_type)})
            foxbun_hit = foxbun_hit or fox_type == "foxbun"
            continue
        items.append(
            {
                "type": "pair",
                "akito": akito_name,
                "toya": toya_name,
                "akito_img": _avatar_uri("彰人", akito_name),
                "toya_img": _avatar_uri("冬弥", toya_name),
                "cooking": is_egg,
            }
        )
        if is_egg:
            dishes.append({"akito": akito_name, "toya": toya_name})

    if dishes and len(results) == 1:
        summary = {"mode": "single", "nickname": nickname, **dishes[0]}
    elif dishes:
        summary = {"mode": "multi", "dishes": dishes, "with_foxbun": foxbun_hit}
    elif foxbun_hit:
        summary = {"mode": "foxbun_only"}
    else:
        summary = None
    return {
        "theme": "light",
        "page_width": 620,
        "eyebrow_tail": "GACHA RESULT",
        "title": "派生抽取结果",
        "pill": f"本次共 {len(results)} 抽",
        "results": items,
        "cooking_summary": summary,
        "quota_text": f"30 分钟内剩余 {remaining} 次",
        "footer_right": "",
    }


async def _render_html_page(template_name: str, data: dict, *, cache_key: str | None = None, fallback=None) -> bytes:
    if cache_key:
        signature = json.dumps(data, ensure_ascii=False, sort_keys=True)
        cached = _HTML_PAGE_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
    try:
        result = await render_random_paro_page(template_name, data)
    except Exception:
        logger.exception("%s HTML 渲染失败", template_name)
        if fallback is not None:
            return fallback()
        raise
    if cache_key:
        _HTML_PAGE_CACHE[cache_key] = (signature, result)
    return result


def _get_group_stats(group_id: int) -> dict:
    today_str = _today_str()
    group_stats, rolled = _get_or_create_group_stats(str(group_id), today_str)
    if rolled:
        _save_stats()
    return group_stats


def _get_group_period_stats(group_id: int, scope: str) -> tuple[dict, dict]:
    group_stats = _get_group_stats(group_id)
    return group_stats, group_stats["daily" if scope == "daily" else "history"]


async def render_paro_rank_image_from_stats(
    group_stats: dict,
    period_stats: dict,
    scope: str,
    *,
    cache_key: str | None = None,
) -> bytes:
    from .ranking_images import build_paro_rank_pil_image_from_stats

    data = _build_paro_rank_page_data_from_stats(group_stats, period_stats, scope)
    return await _render_html_page(
        "ranking.html",
        data,
        cache_key=cache_key,
        fallback=lambda: build_paro_rank_pil_image_from_stats(group_stats, period_stats, scope),
    )


async def render_paro_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return await render_paro_rank_image_from_stats(
        group_stats,
        period_stats,
        scope,
        cache_key=f"paro_rank:{group_id}:{scope}",
    )


async def render_egg_rank_image_from_stats(
    group_stats: dict,
    period_stats: dict,
    scope: str,
    *,
    cache_key: str | None = None,
) -> bytes:
    from .ranking_images import build_egg_rank_pil_image_from_stats

    data = _build_egg_rank_page_data_from_stats(group_stats, period_stats, scope)
    return await _render_html_page(
        "cook_rank.html",
        data,
        cache_key=cache_key,
        fallback=lambda: build_egg_rank_pil_image_from_stats(group_stats, period_stats, scope),
    )


async def render_egg_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return await render_egg_rank_image_from_stats(
        group_stats,
        period_stats,
        scope,
        cache_key=f"egg_rank:{group_id}:{scope}",
    )


async def render_personal_paro_image_from_user_stats(
    user_id: str,
    display_name: str,
    user_stats: dict,
    egg_history: dict | None = None,
) -> bytes:
    from .ranking_images import build_personal_paro_pil_image_from_user_stats
    from .stats import _new_user_egg_history
    from .store import _normalize_user_stats

    normalized = _normalize_user_stats(user_stats)
    history = egg_history or _new_user_egg_history()
    data = _build_personal_paro_page_data_from_user_stats(user_id, display_name, normalized, history)
    return await _render_html_page(
        "profile.html",
        data,
        fallback=lambda: build_personal_paro_pil_image_from_user_stats(user_id, display_name, normalized, history),
    )


async def render_personal_paro_image(group_id: int, user_id: str, display_name: str) -> bytes:
    from .store import _normalize_user_stats

    group_stats = _get_group_stats(group_id)
    user_stats = _normalize_user_stats(group_stats.get("users", {}).get(user_id))
    egg_history = _collect_user_egg_history(group_id, user_id)
    return await render_personal_paro_image_from_user_stats(user_id, display_name, user_stats, egg_history)


__all__ = [name for name in globals() if name.startswith("_")]
