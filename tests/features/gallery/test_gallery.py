"""测试 gallery.py 中抽出的分类与分页辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.features.gallery as gallery


def _pick_first(options: list[str]) -> str:
    return options[0]


def test_resolve_save_category_and_reply_uses_matching_bucket():
    category, reply = gallery._resolve_save_category_and_reply(
        "这张松饼也给你存一下",
        {"food": ["收到了", "第二条"]},
        chooser=_pick_first,
    )

    assert category == "food"
    assert reply == "收到了"


def test_resolve_save_category_and_reply_returns_empty_for_unmatched_text():
    category, reply = gallery._resolve_save_category_and_reply("今天只是路过", {}, chooser=_pick_first)

    assert category == ""
    assert reply == ""


def test_build_collect_session_key_supports_group_and_private():
    assert gallery._build_collect_session_key(1001, "2002") == "group_1001"
    assert gallery._build_collect_session_key(None, "2002") == "private_2002"


def test_resolve_collect_category_defaults_to_toya():
    assert gallery._resolve_collect_category("彰人自拍") == "self"
    assert gallery._resolve_collect_category("随便发吧") == "toya"


def test_resolve_send_image_request_handles_explicit_and_fallback_cases():
    explicit_category, explicit_hint = gallery._resolve_send_image_request(
        "来张表情",
        allowed_categories=[],
        is_wl2_active=False,
        chooser=_pick_first,
    )
    fallback_category, fallback_hint = gallery._resolve_send_image_request(
        "",
        allowed_categories=["toya", "vbs", "meme"],
        is_wl2_active=True,
        chooser=_pick_first,
    )

    assert explicit_category == "meme"
    assert "表情" in explicit_hint
    assert fallback_category == "meme"
    assert fallback_hint == "用户只说了看看。随机发一张，并问他想干嘛。"


def test_resolve_gallery_category_and_paginate_gallery_clamp_values():
    target_cat = gallery._resolve_gallery_category("彰人 999")
    page, total_pages, start, end = gallery._paginate_gallery(65, 999, 30)

    assert target_cat == "self"
    assert page == 3
    assert total_pages == 3
    assert start == 60
    assert end == 90
