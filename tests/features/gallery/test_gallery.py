"""测试 gallery.py 中抽出的分类与分页辅助函数。"""

from __future__ import annotations

import asyncio
import json
import re
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
    assert gallery._resolve_collect_category("卡车") == "pet"
    assert gallery._resolve_send_image_request("丑猫照片", ["all"], False, _pick_first) == ("pet", "")
    assert gallery._resolve_gallery_category("卡车") == "pet"


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

    aliases = {custom.storage_key: ["小猫"]}
    parents = {custom.storage_key: "groupmate"}
    gallery._save_custom_gallery_registry(registry_path, [custom], aliases, parents)
    loaded, loaded_aliases, loaded_parents, load_error = gallery._load_custom_gallery_registry(registry_path)
    payload = json.loads(registry_path.read_text(encoding="utf-8"))

    assert load_error is False
    assert [(item.storage_key, item.name, item.caption_enabled) for item in loaded] == [
        ("custom/猫猫", "猫猫", False)
    ]
    assert loaded_aliases == {"custom/猫猫": ["小猫"]}
    assert loaded_parents == {"custom/猫猫": "groupmate"}
    assert payload["schema_version"] == gallery.GALLERY_REGISTRY_VERSION
    assert payload["custom_galleries"][0]["directory"] == "猫猫"
    assert gallery._validate_custom_gallery_name("猫猫", loaded) == "duplicate"
    assert gallery._validate_custom_gallery_name("名字 有空格", []) == "invalid"
    assert gallery._validate_custom_gallery_name("all", []) == "invalid"


def test_invalid_registry_is_locked_instead_of_overwritten(tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    registry_path.write_text("{broken", encoding="utf-8")

    loaded, aliases, parents, load_error = gallery._load_custom_gallery_registry(registry_path)

    assert loaded == []
    assert aliases == {}
    assert parents == {}
    assert load_error is True


def test_v1_registry_migrates_uuid_directory_and_preserves_relations(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    image_root = tmp_path / "images"
    legacy_dir = image_root / "custom" / "0123456789abcdef0123456789abcdef"
    legacy_dir.mkdir(parents=True)
    (legacy_dir / "cat.jpg").write_bytes(b"cat")
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", image_root)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "custom_galleries": [
                    {"id": "0123456789abcdef0123456789abcdef", "name": "猫猫"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    loaded, aliases, parents, load_error = gallery._load_custom_gallery_registry(registry_path)

    assert [item.name for item in loaded] == ["猫猫"]
    assert [item.storage_key for item in loaded] == ["custom/猫猫"]
    assert aliases == {}
    assert parents == {}
    assert load_error is False
    assert not legacy_dir.exists()
    assert (image_root / "custom" / "猫猫" / "cat.jpg").read_bytes() == b"cat"
    upgraded = json.loads(registry_path.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == gallery.GALLERY_REGISTRY_VERSION
    assert upgraded["custom_galleries"][0]["directory"] == "猫猫"


def test_registry_migration_leaves_unmapped_uuid_directories_untouched(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    image_root = tmp_path / "images"
    mapped_dir = image_root / "custom" / "0123456789abcdef0123456789abcdef"
    orphan_dir = image_root / "custom" / "fedcba9876543210fedcba9876543210"
    mapped_dir.mkdir(parents=True)
    orphan_dir.mkdir(parents=True)
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", image_root)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "custom_galleries": [
                    {"id": "0123456789abcdef0123456789abcdef", "name": "猫猫"}
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gallery._load_custom_gallery_registry(registry_path)

    assert not mapped_dir.exists()
    assert (image_root / "custom" / "猫猫").is_dir()
    assert orphan_dir.is_dir()


def test_gallery_aliases_resolve_and_must_be_unique(monkeypatch):
    custom = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {custom.storage_key: ["三三"]})

    assert gallery._find_gallery_exact("三三") == custom
    assert gallery._resolve_send_image_request("三三", ["all"], False, _pick_first)[0] == custom.storage_key
    assert gallery._validate_gallery_alias("三三") == "duplicate"
    assert gallery._validate_gallery_alias("群友") == "duplicate"
    assert gallery._validate_gallery_alias("新别名") == ""


def test_parent_gallery_reads_descendants_but_child_stays_scoped(monkeypatch, tmp_path):
    child = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    grandchild = gallery.GalleryDefinition(
        storage_key="custom/fedcba9876543210fedcba9876543210",
        name="33精选",
        caption_enabled=False,
        permission_tokens=("33精选",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [child, grandchild])
    monkeypatch.setattr(
        gallery,
        "GALLERY_PARENTS",
        {child.storage_key: "groupmate", grandchild.storage_key: child.storage_key},
    )
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path)
    (tmp_path / "groupmate").mkdir()
    (tmp_path / child.storage_key).mkdir(parents=True)
    (tmp_path / grandchild.storage_key).mkdir(parents=True)
    parent_image = tmp_path / "groupmate" / "parent.jpg"
    child_image = tmp_path / child.storage_key / "child.png"
    grandchild_image = tmp_path / grandchild.storage_key / "grandchild.gif"
    parent_image.write_bytes(b"parent")
    child_image.write_bytes(b"child")
    grandchild_image.write_bytes(b"grandchild")

    assert gallery._gallery_storage_keys_for_read("groupmate") == [
        "groupmate",
        child.storage_key,
        grandchild.storage_key,
    ]
    assert set(gallery.get_file_list_safe("groupmate")) == {parent_image, child_image, grandchild_image}
    assert set(gallery.get_file_list_safe(child.storage_key)) == {child_image, grandchild_image}
    assert gallery.get_file_list_safe(grandchild.storage_key) == [grandchild_image]


def test_gallery_parent_cycle_detection():
    parents = {"custom/b": "custom/a"}

    assert gallery._would_create_gallery_cycle("custom/a", "custom/b", parents) is True
    assert gallery._would_create_gallery_cycle("custom/c", "custom/b", parents) is False


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
    assert created.storage_key == "custom/猫猫"
    assert re.fullmatch(r"[0-9a-f]{32}", created.gallery_id)
    assert created.caption_enabled is False
    assert (tmp_path / "images" / created.storage_key).is_dir()
    assert json.loads(registry_path.read_text(encoding="utf-8"))["custom_galleries"][0]["name"] == "猫猫"


@pytest.mark.asyncio
async def test_add_gallery_alias_command_persists_alias(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.add_gallery_alias_cmd.handlers[0](
            Event(plain_text="添加图库别名 宠物 毛孩子", user_id=gallery.SUPERUSER_QQ)
        )

    assert "也可以叫【毛孩子】" in str(exc.value.result)
    assert gallery.GALLERY_ALIASES == {"pet": ["毛孩子"]}
    assert json.loads(registry_path.read_text(encoding="utf-8"))["aliases"] == {"pet": ["毛孩子"]}
    assert gallery._resolve_send_image_request("毛孩子", ["all"], False, _pick_first)[0] == "pet"


@pytest.mark.asyncio
async def test_link_and_unlink_child_gallery_commands(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    child = gallery.GalleryDefinition(
        storage_key="custom/0123456789abcdef0123456789abcdef",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [child])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as link_exc:
        await gallery.link_child_gallery_cmd.handlers[0](
            Event(plain_text="关联子图库 33 群友", user_id=gallery.SUPERUSER_QQ)
        )

    assert "关联为【群友】的子图库" in str(link_exc.value.result)
    assert {child.storage_key: "groupmate"} == gallery.GALLERY_PARENTS
    assert json.loads(registry_path.read_text(encoding="utf-8"))["parents"] == {
        child.storage_key: "groupmate"
    }

    with pytest.raises(FinishedException) as unlink_exc:
        await gallery.unlink_child_gallery_cmd.handlers[0](
            Event(plain_text="取消子图库关联 33", user_id=gallery.SUPERUSER_QQ)
        )

    assert "已取消【33】" in str(unlink_exc.value.result)
    assert gallery.GALLERY_PARENTS == {}
    assert json.loads(registry_path.read_text(encoding="utf-8"))["parents"] == {}


@pytest.mark.asyncio
async def test_gallery_management_commands_are_silent_for_non_superuser(monkeypatch):
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})

    alias_result = await gallery.add_gallery_alias_cmd.handlers[0](
        Event(plain_text="添加图库别名 宠物 毛孩子", user_id="10001")
    )
    link_result = await gallery.link_child_gallery_cmd.handlers[0](
        Event(plain_text="关联子图库 33 群友", user_id="10001")
    )
    unlink_result = await gallery.unlink_child_gallery_cmd.handlers[0](
        Event(plain_text="取消子图库关联 33", user_id="10001")
    )

    assert alias_result is None
    assert link_result is None
    assert unlink_result is None
    assert gallery.GALLERY_ALIASES == {}
    assert gallery.GALLERY_PARENTS == {}


@pytest.mark.asyncio
async def test_gallery_sleep_commands_are_superuser_only(monkeypatch):
    monkeypatch.setattr(gallery, "GALLERY_SLEEP_ENABLED", True)

    disable_result = await gallery.disable_gallery_sleep_cmd.handlers[0](
        Event(plain_text="关闭图库休眠", user_id="10001")
    )
    assert disable_result is None
    assert gallery.GALLERY_SLEEP_ENABLED is True

    with pytest.raises(FinishedException) as disable_exc:
        await gallery.disable_gallery_sleep_cmd.handlers[0](
            Event(plain_text="关闭图库休眠", user_id=gallery.SUPERUSER_QQ)
        )
    assert "图库休眠先关了" in str(disable_exc.value.result)
    assert gallery.GALLERY_SLEEP_ENABLED is False

    with pytest.raises(FinishedException) as enable_exc:
        await gallery.enable_gallery_sleep_cmd.handlers[0](
            Event(plain_text="开启图库休眠", user_id=gallery.SUPERUSER_QQ)
        )
    assert "图库休眠恢复" in str(enable_exc.value.result)
    assert gallery.GALLERY_SLEEP_ENABLED is True

    enable_result = await gallery.enable_gallery_sleep_cmd.handlers[0](
        Event(plain_text="开启图库休眠", user_id="10001")
    )
    assert enable_result is None
    assert gallery.GALLERY_SLEEP_ENABLED is True


def test_gallery_sleep_wrapper_bypasses_sleep_gate_when_disabled(monkeypatch):
    def sleep_block(*args, **kwargs):
        pytest.fail("sleep gate should be bypassed")

    monkeypatch.setattr(gallery, "sleep_block", sleep_block)
    monkeypatch.setattr(gallery, "GALLERY_SLEEP_ENABLED", False)

    assert gallery._gallery_sleep_block("sleep_replies_img") == ""


def test_gallery_safety_wrapper_preserves_night_complaint_when_disabled(monkeypatch):
    granted: list[int] = []
    monkeypatch.setattr(gallery, "GALLERY_SLEEP_ENABLED", False)
    monkeypatch.setattr(gallery, "is_sleeping", lambda: True)
    monkeypatch.setattr(gallery, "grant_safety_pass", granted.append)

    gallery._grant_gallery_safety_pass(5)
    assert granted == []

    monkeypatch.setattr(gallery, "GALLERY_SLEEP_ENABLED", True)
    gallery._grant_gallery_safety_pass(7)
    assert granted == [7]

    monkeypatch.setattr(gallery, "GALLERY_SLEEP_ENABLED", False)
    monkeypatch.setattr(gallery, "is_sleeping", lambda: False)
    gallery._grant_gallery_safety_pass(9)
    assert granted == [7, 9]


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
