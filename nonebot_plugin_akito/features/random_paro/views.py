"""random_paro 的页面数据与 HTML 渲染壳。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys


def _pkg():
    return sys.modules[__package__]


_HTML_PAGE_CACHE: dict[str, tuple[str, bytes]] = {}


def _run_async_render_sync(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("不能在运行中的事件循环里同步执行 HTML 渲染")


def _path_to_uri(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.resolve().as_uri()
    except Exception:
        return ""


def _find_foxrabbit_asset(name: str) -> Path | None:
    pkg = _pkg()
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = pkg.FOXRABBIT_DIR / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


def _avatar_uri(character: str, name: str) -> str:
    return _path_to_uri(_pkg()._find_avatar(character, name))


def _fox_icon_uris(fox_type: str) -> list[str]:
    if fox_type == "fox":
        return [_path_to_uri(_find_foxrabbit_asset("狐"))] if _find_foxrabbit_asset("狐") else []
    if fox_type == "rabbit":
        return [_path_to_uri(_find_foxrabbit_asset("兔"))] if _find_foxrabbit_asset("兔") else []
    if fox_type == "foxrabbit":
        icons = []
        fox_path = _find_foxrabbit_asset("狐")
        rabbit_path = _find_foxrabbit_asset("兔")
        if fox_path:
            icons.append(_path_to_uri(fox_path))
        if rabbit_path:
            icons.append(_path_to_uri(rabbit_path))
        return icons
    if fox_type == "foxbun":
        foxbun_path = _find_foxrabbit_asset("狐&兔")
        return [_path_to_uri(foxbun_path)] if foxbun_path else []
    return []


def _subtitle_for_scope(period_stats: dict, scope: str) -> str:
    return f"{period_stats.get('date')} 00:00 起累计" if scope == "daily" else "功能上线后累计"


def _build_user_contract(counter: dict[str, int], profiles: dict[str, str], *, limit: int = 5) -> list[dict]:
    users = []
    for user_id, count in _pkg()._sorted_counter_items(counter)[:limit]:
        users.append({"name": profiles.get(user_id) or f"用户{user_id}", "count": count})
    if not users:
        users.append({"name": "暂无", "count": 0})
    return users


def _build_character_contract(
    counter: dict[str, int],
    *,
    title: str,
    cls: str,
    last_hit_seq: dict[str, int] | None = None,
    limit: int = 3,
) -> dict:
    items = []
    grouped: list[tuple[list[str], int]] = []
    for name, count in _pkg()._sorted_ranked_items(counter, last_hit_seq):
        if grouped and grouped[-1][1] == count:
            grouped[-1][0].append(name)
        else:
            grouped.append(([name], count))

    for names, count in grouped[:limit]:
        visible_names = names[:3]
        items.append(
            {
                "names": visible_names,
                "count": count,
                "more": len(names) > 3,
                "icons": [
                    _avatar_uri("彰人" if cls == "akito" else "冬弥", name)
                    for name in visible_names
                    if name != "暂无"
                ],
            }
        )

    if not items:
        items.append({"names": ["暂无"], "count": 0, "more": False, "icons": []})

    return {
        "cls": cls,
        "title": title,
        "en": "AKITO" if cls == "akito" else "TOYA",
        "items": items,
    }


def _build_fox_rows_contract(period_stats: dict) -> list[dict]:
    entries = [
        ("foxrabbit", "狐兔", period_stats["foxrabbit_total"], ["狐", "兔"]),
        ("foxbun", "狐兔饭", period_stats["foxbun_total"], ["狐", "兔"]),
        ("fox", "狐狸", period_stats["fox_total"], ["狐"]),
        ("rabbit", "兔子", period_stats["rabbit_total"], ["兔"]),
    ]
    rows = []
    for _idx, (fox_type, label, count, kinds) in sorted(
        enumerate(entries),
        key=lambda item: (-item[1][2], item[0]),
    ):
        rows.append({"name": label, "kinds": kinds, "count": count, "icons": _fox_icon_uris(fox_type)})
    return rows


def _build_profile_pair_contract(egg_history: dict) -> list[dict]:
    items = []
    for pair_item in _pkg()._build_personal_cooking_pair_items(egg_history):
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
                title="被抽到最多次的彰人 TOP 3",
                cls="akito",
                last_hit_seq=period_stats.get("akito_last_hit_seq"),
            ),
            _build_character_contract(
                period_stats["toya_hits"],
                title="被抽到最多次的冬弥 TOP 3",
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


def _build_personal_paro_page_data_from_user_stats(user_id: str, display_name: str, user_stats: dict, egg_history: dict) -> dict:
    pkg = _pkg()
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "PLAYER PROFILE",
        "title": display_name or f"用户{user_id}",
        "pill": "",
        "stats": [
            {"label": "累计抽取派生次数", "value": user_stats["draw_count"]},
            {"label": "累计抽到做饭的次数", "value": pkg._count_total_cooking_hits(egg_history)},
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
    results: list[tuple[str, str, bool, str | None]],
    remaining: int,
    nickname: str,
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
        summary = {
            "mode": "single",
            "nickname": nickname,
            "akito": dishes[0]["akito"],
            "toya": dishes[0]["toya"],
        }
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


async def _render_html_page(
    template_name: str,
    data: dict,
    *,
    cache_key: str | None = None,
    fallback=None,
) -> bytes:
    pkg = _pkg()
    if cache_key:
        signature = json.dumps(data, ensure_ascii=False, sort_keys=True)
        cached = _HTML_PAGE_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
        try:
            pic = await pkg.render_random_paro_page(template_name, data)
        except Exception:
            pkg.logger.exception(f"{template_name} HTML 渲染失败")
            if fallback is not None:
                return fallback()
            raise
        _HTML_PAGE_CACHE[cache_key] = (signature, pic)
        return pic
    try:
        return await pkg.render_random_paro_page(template_name, data)
    except Exception:
        pkg.logger.exception(f"{template_name} HTML 渲染失败")
        if fallback is not None:
            return fallback()
        raise


def _get_group_stats(group_id: int) -> dict:
    pkg = _pkg()
    today_str = pkg._today_str()
    group_stats, rolled = pkg._get_or_create_group_stats(str(group_id), today_str)
    if rolled:
        pkg._save_stats()
    return group_stats


def _get_group_period_stats(group_id: int, scope: str) -> tuple[dict, dict]:
    group_stats = _get_group_stats(group_id)
    period_key = "daily" if scope == "daily" else "history"
    return group_stats, group_stats[period_key]
