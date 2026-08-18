"""测试 gallery.py 中抽出的分类与分页辅助函数。"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

from nonebot.adapters import Event, Message
from nonebot.exception import FinishedException
import pytest

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


def test_pet_gallery_is_available_across_gallery_resolvers():
    category, reply = gallery._resolve_save_category_and_reply(
        "存宠物",
        {"pet": ["存好了"]},
        chooser=_pick_first,
    )

    assert category == "pet"
    assert reply == "存好了"
    assert gallery._resolve_collect_category("宠物") == "pet"
    assert gallery._resolve_send_image_request("宠物", ["all"], False, _pick_first) == ("pet", "")
    assert gallery._resolve_gallery_category("宠物") == "pet"


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


def test_resolve_send_image_request_rejects_unknown_non_empty_category():
    category, hint = gallery._resolve_send_image_request(
        "根本不存在的图库",
        allowed_categories=["all"],
        is_wl2_active=False,
        chooser=_pick_first,
    )
    compatible_category, _ = gallery._resolve_send_image_request(
        "冬弥照片",
        allowed_categories=["all"],
        is_wl2_active=False,
        chooser=_pick_first,
    )

    assert category == ""
    assert hint == ""
    assert compatible_category == "toya"


def test_custom_gallery_uses_exact_name_and_captionless_save_reply(monkeypatch):
    custom = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="猫猫",
        caption_enabled=False,
        permission_tokens=("猫猫",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])

    save_category, reply = gallery._resolve_save_category_and_reply(
        "存猫猫",
        {"captionless": ["收到了"]},
        chooser=_pick_first,
    )

    assert save_category == custom.storage_key
    assert reply == "收到了"
    assert gallery._resolve_send_image_request("猫猫", ["all"], False, _pick_first)[0] == custom.storage_key
    assert gallery._resolve_send_image_request("猫猫照片", ["all"], False, _pick_first)[0] == ""


def test_gallery_permissions_accept_fixed_keys_and_custom_names(monkeypatch):
    custom = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="猫猫",
        caption_enabled=False,
        permission_tokens=("猫猫",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["pet", "猫猫"]})
    pet = gallery._get_gallery("pet")
    self_gallery = gallery._get_gallery("self")

    assert pet is not None
    assert self_gallery is not None
    assert gallery._gallery_allowed(1001, pet) is True
    assert gallery._gallery_allowed(1001, custom) is True
    assert gallery._gallery_allowed(1001, self_gallery) is False


def test_custom_gallery_registry_round_trip_and_validation(tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    custom = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="猫猫",
        caption_enabled=False,
        permission_tokens=("猫猫",),
        custom=True,
    )

    gallery._save_custom_gallery_registry(registry_path, [custom])
    loaded, load_error = gallery._load_custom_gallery_registry(registry_path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))

    assert load_error is False
    assert [(item.storage_key, item.name, item.caption_enabled) for item in loaded] == [
        (custom.storage_key, "猫猫", False)
    ]
    assert payload["schema_version"] == gallery.GALLERY_REGISTRY_VERSION
    assert gallery._validate_custom_gallery_name("猫猫", loaded) == "duplicate"
    assert gallery._validate_custom_gallery_name("名字 有空格", []) == "invalid"
    assert gallery._validate_custom_gallery_name("all", []) == "invalid"


def test_invalid_registry_is_locked_instead_of_overwritten(tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    registry_path.write_text("{broken", encoding="utf-8")

    loaded, load_error = gallery._load_custom_gallery_registry(registry_path)

    assert loaded == []
    assert load_error is True


def test_resolve_gallery_category_and_paginate_gallery_clamp_values():
    target_cat = gallery._resolve_gallery_category("彰人 999")
    page, total_pages, start, end = gallery._paginate_gallery(65, 999, 30)

    assert target_cat == "self"
    assert page == 3
    assert total_pages == 3
    assert start == 60
    assert end == 90


@pytest.mark.asyncio
async def test_create_gallery_command_is_silent_for_non_superuser(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path / "images")
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    result = await gallery.create_gallery_cmd.handlers[0](
        Event(plain_text="新建图库猫猫", user_id="10001")
    )

    assert result is None
    assert gallery.CUSTOM_GALLERIES == []
    assert not registry_path.exists()


@pytest.mark.asyncio
async def test_create_gallery_command_persists_superuser_gallery(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path / "images")
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.create_gallery_cmd.handlers[0](
            Event(plain_text="新建图库 猫猫", user_id=gallery.SUPERUSER_QQ)
        )

    assert "建好了" in str(exc.value.result)
    assert len(gallery.CUSTOM_GALLERIES) == 1
    created = gallery.CUSTOM_GALLERIES[0]
    assert created.name == "猫猫"
    assert created.caption_enabled is False
    assert (tmp_path / "images" / created.storage_key).is_dir()
    assert json.loads(registry_path.read_text(encoding="utf-8"))["custom_galleries"][0]["name"] == "猫猫"


@pytest.mark.asyncio
async def test_pet_send_outputs_only_image_and_skips_caption_api(monkeypatch, tmp_path):
    image_path = tmp_path / "pet.jpg"
    image_path.write_bytes(b"pet-image")
    caption_api = AsyncMock(return_value="不该出现")
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "get_memory_key", lambda event: "group_1001")
    monkeypatch.setattr(gallery, "get_user_memory", lambda key: {"temp_implants": []})
    monkeypatch.setattr(gallery, "get_random_local_image", lambda category: image_path)
    monkeypatch.setattr(gallery, "call_deepseek_api", caption_api)
    monkeypatch.setattr(gallery, "grant_safety_pass", lambda seconds: None)

    with pytest.raises(FinishedException) as exc:
        await gallery.send_img_cmd.handlers[0](
            Event(plain_text="发张宠物", group_id=1001),
            Message("宠物"),
        )

    assert str(exc.value.result) == "[image]"
    caption_api.assert_not_awaited()
