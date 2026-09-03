"""测试 gallery.py 中抽出的分类与分页辅助函数。"""

from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import Bot
from nonebot.exception import FinishedException
import pytest

import nonebot_plugin_akito.features.gallery as gallery
from nonebot_plugin_akito.features.gallery.hash_index import GalleryIndexSyncResult


def _pick_first(options: list[str]) -> str:
    return options[0]


def _make_gallery_hash_index(tmp_path):
    return gallery.GalleryHashIndex(
        image_root=tmp_path,
        database_path=tmp_path / "gallery_hash_index.sqlite3",
        fixed_storage_keys=tuple(item.storage_key for item in gallery.FIXED_GALLERIES),
        image_suffixes=gallery.GALLERY_IMAGE_SUFFIXES,
    )


def test_resolve_save_category_and_reply_uses_matching_bucket():
    category, reply = gallery._resolve_save_category_and_reply(
        "存松饼",
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


def test_resolve_collect_category_requires_exact_existing_gallery():
    assert gallery._resolve_collect_category("自拍") == "self"
    assert gallery._resolve_collect_category("彰人自拍") == ""
    assert gallery._resolve_collect_category("随便发吧") == ""


def test_pet_gallery_is_available_across_gallery_resolvers():
    category, reply = gallery._resolve_save_category_and_reply(
        "存宠物",
        {"pet": ["存好了"]},
        chooser=_pick_first,
    )

    assert category == "pet"
    assert reply == "存好了"
    assert gallery._resolve_collect_category("宠物") == "pet"
    assert gallery._resolve_send_image_request("宠物") == ("pet", "")
    assert gallery._resolve_gallery_category("宠物") == "pet"
    assert gallery._resolve_collect_category("卡车") == "pet"
    assert gallery._resolve_send_image_request("丑猫") == ("pet", "")
    assert gallery._resolve_send_image_request("丑猫照片") == ("", "")
    assert gallery._resolve_gallery_category("卡车") == "pet"


def test_resolve_send_image_request_only_accepts_exact_gallery_name_or_alias():
    explicit_category, explicit_hint = gallery._resolve_send_image_request("表情")
    fallback_category, fallback_hint = gallery._resolve_send_image_request("")

    assert explicit_category == "meme"
    assert "表情" in explicit_hint
    assert fallback_category == ""
    assert fallback_hint == ""


def test_parse_send_image_request_accepts_spaced_and_attached_counts():
    assert gallery._parse_send_image_request("宠物 3") == ("宠物", 3, True)
    assert gallery._parse_send_image_request("宠物3") == ("宠物", 3, True)
    assert gallery._parse_send_image_request("宠物 0") == ("宠物", 0, False)
    assert gallery._parse_send_image_request("宠物6") == ("宠物", 6, False)


def test_parse_send_image_request_prioritizes_numeric_gallery_names(monkeypatch):
    custom = gallery.GalleryDefinition(
        storage_key="custom/33",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])

    assert gallery._parse_send_image_request("33") == ("33", 1, True)


def test_resolve_send_image_request_rejects_unknown_non_empty_category():
    category, hint = gallery._resolve_send_image_request("根本不存在的图库")
    compatible_category, _ = gallery._resolve_send_image_request("冬弥照片")
    exact_category, _ = gallery._resolve_send_image_request("冬弥")

    assert category == ""
    assert hint == ""
    assert compatible_category == ""
    assert exact_category == "toya"


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
    assert gallery._resolve_send_image_request("猫猫")[0] == custom.storage_key
    assert gallery._resolve_send_image_request("猫猫照片")[0] == ""


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
    assert gallery._resolve_send_image_request("三三")[0] == custom.storage_key
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
    assert gallery._resolve_send_image_request("毛孩子")[0] == "pet"


@pytest.mark.asyncio
async def test_delete_gallery_alias_command_persists_removal(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {"pet": ["毛孩子", "小动物"]})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.delete_gallery_alias_cmd.handlers[0](
            Event(plain_text="删除图库别名 宠物 毛孩子", user_id=gallery.SUPERUSER_QQ)
        )

    assert str(exc.value.result) == "行。【宠物】不再叫【毛孩子】了。"
    assert gallery.GALLERY_ALIASES == {"pet": ["小动物"]}
    assert json.loads(registry_path.read_text(encoding="utf-8"))["aliases"] == {
        "pet": ["小动物"]
    }
    assert gallery._resolve_send_image_request("毛孩子")[0] == ""
    assert gallery._resolve_send_image_request("小动物")[0] == "pet"


@pytest.mark.asyncio
async def test_delete_gallery_alias_command_cannot_remove_builtin_alias(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.delete_gallery_alias_cmd.handlers[0](
            Event(plain_text="删除图库别名 宠物 卡车", user_id=gallery.SUPERUSER_QQ)
        )

    assert str(exc.value.result) == "【宠物】没有登记这个自定义别名。"
    assert gallery._resolve_send_image_request("卡车")[0] == "pet"
    assert not registry_path.exists()


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
    delete_alias_result = await gallery.delete_gallery_alias_cmd.handlers[0](
        Event(plain_text="删除图库别名 宠物 毛孩子", user_id="10001")
    )
    link_result = await gallery.link_child_gallery_cmd.handlers[0](
        Event(plain_text="关联子图库 33 群友", user_id="10001")
    )
    unlink_result = await gallery.unlink_child_gallery_cmd.handlers[0](
        Event(plain_text="取消子图库关联 33", user_id="10001")
    )

    assert alias_result is None
    assert delete_alias_result is None
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
    bot = Bot()

    with pytest.raises(FinishedException) as exc:
        await gallery.send_img_cmd.handlers[0](
            bot,
            Event(plain_text="发张宠物", group_id=1001),
        )

    assert str(exc.value.result) == "[image]"
    caption_api.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_multiple_pet_images_as_forward_message(monkeypatch, tmp_path):
    image_paths = []
    for index in range(5):
        image_path = tmp_path / f"pet-{index}.jpg"
        image_path.write_bytes(f"pet-image-{index}".encode())
        image_paths.append(image_path)
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "get_memory_key", lambda event: "group_1001")
    monkeypatch.setattr(gallery, "get_user_memory", lambda key: {"temp_implants": []})
    monkeypatch.setattr(gallery, "get_random_local_images", lambda category, count: image_paths[:count])
    monkeypatch.setattr(gallery, "grant_safety_pass", lambda seconds: None)
    bot = Bot()

    result = await gallery.send_img_cmd.handlers[0](
        bot,
        Event(plain_text="发张宠物 5", group_id=1001),
    )

    assert result is None
    bot.call_api.assert_awaited_once()
    api_name = bot.call_api.await_args.args[0]
    payload = bot.call_api.await_args.kwargs
    assert api_name == "send_group_forward_msg"
    assert payload["group_id"] == 1001
    assert len(payload["messages"]) == 1
    node = payload["messages"][0]
    assert node["type"] == "node"
    assert node["data"]["name"] == "东云彰人"
    assert str(node["data"]["content"]) == "[image]" * 5


@pytest.mark.asyncio
async def test_send_multiple_captioned_gallery_images_without_default_caption(monkeypatch, tmp_path):
    image_paths = []
    for index in range(3):
        image_path = tmp_path / f"food-{index}.jpg"
        image_path.write_bytes(f"food-image-{index}".encode())
        image_paths.append(image_path)
    caption_api = AsyncMock(return_value="不应该附带这句评价")
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "get_memory_key", lambda event: "group_1001")
    monkeypatch.setattr(gallery, "get_user_memory", lambda key: {"temp_implants": []})
    monkeypatch.setattr(gallery, "get_random_local_images", lambda category, count: image_paths[:count])
    monkeypatch.setattr(gallery, "call_deepseek_api", caption_api)
    monkeypatch.setattr(gallery, "grant_safety_pass", lambda seconds: None)
    bot = Bot()

    result = await gallery.send_img_cmd.handlers[0](
        bot,
        Event(plain_text="发张美食3", group_id=1001),
    )

    assert result is None
    caption_api.assert_not_awaited()
    payload = bot.call_api.await_args.kwargs
    node_content = payload["messages"][0]["data"]["content"]
    assert str(node_content) == "[image]" * 3


@pytest.mark.asyncio
async def test_multi_image_forward_failure_falls_back_to_normal_message(monkeypatch, tmp_path):
    image_paths = []
    for index in range(2):
        image_path = tmp_path / f"pet-{index}.jpg"
        image_path.write_bytes(f"pet-image-{index}".encode())
        image_paths.append(image_path)
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "get_memory_key", lambda event: "group_1001")
    monkeypatch.setattr(gallery, "get_user_memory", lambda key: {"temp_implants": []})
    monkeypatch.setattr(gallery, "get_random_local_images", lambda category, count: image_paths[:count])
    monkeypatch.setattr(gallery, "grant_safety_pass", lambda seconds: None)
    bot = Bot()
    bot.call_api.side_effect = RuntimeError("forward unsupported")

    with pytest.raises(FinishedException) as exc:
        await gallery.send_img_cmd.handlers[0](
            bot,
            Event(plain_text="发张宠物 2", group_id=1001),
        )

    assert str(exc.value.result) == "[image]" * 2
    bot.call_api.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_image_count_must_be_between_one_and_five(monkeypatch, tmp_path):
    image_path = tmp_path / "pet.jpg"
    image_path.write_bytes(b"pet-image")
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "get_random_local_image", lambda category: image_path)
    bot = Bot()

    for request in ("发张宠物 0", "发张宠物 6", "发张宠物 10", "发张宠物 五", "发张宠物 2 额外"):
        result = await gallery.send_img_cmd.handlers[0](
            bot,
            Event(plain_text=request, group_id=1001),
        )
        assert result is None


def test_gallery_command_registrations_only_keep_supported_entrypoints():
    assert gallery.send_img_cmd.args == (r"^发张\s*\S+(?:\s+\S+)?\s*$",)
    assert gallery.save_img_cmd.args == ()
    assert gallery.save_img_cmd.kwargs == {"priority": 6, "block": False}
    assert gallery.collect_cmd.args == ("开始收图",)
    assert gallery.collect_cmd.kwargs["aliases"] == {"停止收图"}
    assert gallery.gallery_cmd.args == ("图库清单",)
    assert "aliases" not in gallery.gallery_cmd.kwargs
    assert gallery.view_gallery_alias_cmd.args == (r"^查看图库别名(?:\s+.*)?$",)


@pytest.mark.asyncio
async def test_unknown_send_request_is_rejected_but_extra_text_and_empty_are_silent(monkeypatch):
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "REACTIONS_DB", {"unknown_gallery_replies": ["没有这个图库。"]})

    def fail_sleep_block(*args, **kwargs):
        pytest.fail("invalid send requests must be discarded before the sleep gate")

    monkeypatch.setattr(gallery, "_gallery_sleep_block", fail_sleep_block)
    bot = Bot()

    extra_text_result = await gallery.send_img_cmd.handlers[0](
        bot,
        Event(plain_text="发张宠物照片", group_id=1001)
    )
    empty_result = await gallery.send_img_cmd.handlers[0](
        bot,
        Event(plain_text="发张", group_id=1001)
    )

    with pytest.raises(FinishedException) as unknown_exc:
        await gallery.send_img_cmd.handlers[0](
            bot,
            Event(plain_text="发张不存在图库", group_id=1001)
        )

    assert extra_text_result is None
    assert empty_result is None
    assert str(unknown_exc.value.result) == "没有这个图库。"


@pytest.mark.asyncio
async def test_save_request_without_image_is_silent_before_sleep_gate(monkeypatch):
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})

    def fail_sleep_block(*args, **kwargs):
        pytest.fail("image-free save requests must be discarded before the sleep gate")

    monkeypatch.setattr(gallery, "_gallery_sleep_block", fail_sleep_block)
    bot = Bot()

    result = await gallery.save_img_cmd.handlers[0](
        bot,
        Event(plain_text="存宠物", group_id=1001),
    )

    assert result is None
    bot.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_request_accepts_image_from_quoted_message(monkeypatch):
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "_gallery_sleep_block", lambda *args, **kwargs: "……在睡。")
    quoted_image = SimpleNamespace(type="image", data={"url": "https://example.com/pet.jpg"})
    bot = Bot()

    await gallery.save_img_cmd.handlers[0](
        bot,
        Event(
            plain_text="存宠物",
            group_id=1001,
            reply=SimpleNamespace(message=[quoted_image]),
        ),
    )

    bot.send.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("segments", "expected_url"),
    [
        (
            [
                SimpleNamespace(type="text", data={"text": "存月城"}),
                SimpleNamespace(type="image", data={"url": "https://example.com/moon.jpg"}),
            ],
            "https://example.com/moon.jpg",
        ),
        (
            [
                SimpleNamespace(type="image", data={"file": "https://example.com/moon-file.jpg"}),
                SimpleNamespace(type="text", data={"text": "存月城"}),
            ],
            "https://example.com/moon-file.jpg",
        ),
    ],
)
async def test_save_custom_gallery_accepts_image_in_same_message(
    monkeypatch,
    tmp_path,
    segments,
    expected_url,
):
    custom = gallery.GalleryDefinition(
        storage_key="custom/月城",
        name="月城",
        caption_enabled=False,
        permission_tokens=("月城",),
        custom=True,
    )
    response = MagicMock(status=200)
    response.read = AsyncMock(return_value=b"moon-image")
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = response
    session.__aenter__.return_value = session
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path)
    monkeypatch.setattr(gallery, "GALLERY_HASH_INDEX", _make_gallery_hash_index(tmp_path))
    monkeypatch.setattr(gallery, "_gallery_sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "_grant_gallery_safety_pass", lambda seconds: None)
    monkeypatch.setattr(gallery.aiohttp, "ClientSession", lambda: session)
    bot = Bot()

    await gallery.save_img_cmd.handlers[0](
        bot,
        Event(plain_text="存月城", group_id=1001, message=segments),
    )

    assert next((tmp_path / custom.storage_key).glob("*.jpg")).read_bytes() == b"moon-image"
    session.get.assert_called_once_with(expected_url)
    bot.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_gallery_with_image_is_silent_before_sleep_gate(monkeypatch):
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})

    def fail_sleep_block(*args, **kwargs):
        pytest.fail("unknown galleries must be discarded before the sleep gate")

    monkeypatch.setattr(gallery, "_gallery_sleep_block", fail_sleep_block)
    image = SimpleNamespace(type="image", data={"url": "https://example.com/unknown.jpg"})
    bot = Bot()

    result = await gallery.save_img_cmd.handlers[0](
        bot,
        Event(plain_text="存不存在图库", group_id=1001, message=[image]),
    )

    assert result is None
    bot.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_global_duplicate_index_covers_fixed_and_custom_galleries_only(tmp_path):
    fixed_dir = tmp_path / "toya"
    custom_dir = tmp_path / "custom" / "旧图库"
    avatar_dir = tmp_path / "paro_avatars" / "彰人"
    fixed_dir.mkdir(parents=True)
    custom_dir.mkdir(parents=True)
    avatar_dir.mkdir(parents=True)
    (fixed_dir / "fixed.jpg").write_bytes(b"fixed-image")
    (custom_dir / "custom.png").write_bytes(b"custom-image")
    (avatar_dir / "avatar.jpg").write_bytes(b"avatar-image")
    hash_index = _make_gallery_hash_index(tmp_path)
    await hash_index.sync_incremental()

    assert await hash_index.is_duplicate(b"fixed-image") is True
    assert await hash_index.is_duplicate(b"custom-image") is True
    assert await hash_index.is_duplicate(b"avatar-image") is False
    assert await hash_index.is_duplicate(b"other-image") is False


@pytest.mark.asyncio
async def test_duplicate_index_skips_unreadable_existing_file(monkeypatch, tmp_path):
    unreadable = tmp_path / "toya" / "broken.jpg"
    unreadable.parent.mkdir(parents=True)
    unreadable.write_bytes(b"same")
    hash_index = _make_gallery_hash_index(tmp_path)

    def fail_hash(relative_path, path):
        raise OSError("unreadable")

    monkeypatch.setattr(hash_index, "_hash_file_record", fail_hash)
    result = await hash_index.sync_incremental()

    assert result.failed_count == 1
    assert await hash_index.is_duplicate(b"same") is False


@pytest.mark.asyncio
async def test_unique_save_does_not_overwrite_filename_collision(tmp_path):
    save_dir = tmp_path / "custom" / "月城"
    save_dir.mkdir(parents=True)
    hash_index = _make_gallery_hash_index(tmp_path)

    assert await hash_index.save_unique(save_dir, "same.jpg", b"first") is True
    assert await hash_index.save_unique(save_dir, "same.jpg", b"second") is True

    assert {path.read_bytes() for path in save_dir.glob("*.jpg")} == {b"first", b"second"}


@pytest.mark.asyncio
async def test_manual_save_rejects_duplicate_from_another_gallery(monkeypatch, tmp_path):
    custom = gallery.GalleryDefinition(
        storage_key="custom/月城",
        name="月城",
        caption_enabled=False,
        permission_tokens=("月城",),
        custom=True,
    )
    existing_dir = tmp_path / "toya"
    existing_dir.mkdir()
    (existing_dir / "existing.jpg").write_bytes(b"duplicate-image")
    response = MagicMock(status=200)
    response.read = AsyncMock(return_value=b"duplicate-image")
    response.__aenter__.return_value = response
    session = MagicMock()
    session.get.return_value = response
    session.__aenter__.return_value = session
    image = SimpleNamespace(type="image", data={"url": "https://example.com/duplicate.jpg"})
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path)
    monkeypatch.setattr(gallery, "GALLERY_HASH_INDEX", _make_gallery_hash_index(tmp_path))
    monkeypatch.setattr(gallery, "_gallery_sleep_block", lambda *args, **kwargs: "")
    monkeypatch.setattr(gallery, "_grant_gallery_safety_pass", lambda seconds: None)
    monkeypatch.setattr(gallery.aiohttp, "ClientSession", lambda: session)
    bot = Bot()

    await gallery.save_img_cmd.handlers[0](
        bot,
        Event(plain_text="存月城", group_id=1001, message=[image]),
    )

    assert list((tmp_path / custom.storage_key).glob("*.jpg")) == []
    assert bot.send.await_args.kwargs["message"] == gallery.DUPLICATE_IMAGE_REPLY


@pytest.mark.asyncio
async def test_collect_mode_saves_new_images_and_reports_duplicates(monkeypatch, tmp_path):
    custom = gallery.GalleryDefinition(
        storage_key="custom/月城",
        name="月城",
        caption_enabled=False,
        permission_tokens=("月城",),
        custom=True,
    )
    existing_dir = tmp_path / "groupmate"
    existing_dir.mkdir()
    (existing_dir / "existing.jpg").write_bytes(b"old-image")
    image_payloads = {
        "https://example.com/old.jpg": b"old-image",
        "https://example.com/new-a.jpg": b"new-image",
        "https://example.com/new-b.jpg": b"new-image",
    }
    responses = {}
    for url, image_data in image_payloads.items():
        response = MagicMock(status=200)
        response.read = AsyncMock(return_value=image_data)
        response.__aenter__.return_value = response
        responses[url] = response
    session = MagicMock()
    session.get.side_effect = responses.__getitem__
    session.__aenter__.return_value = session
    images = [SimpleNamespace(type="image", data={"url": url}) for url in image_payloads]
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "COLLECTING_MODE", {"group_1001": custom.storage_key})
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path)
    monkeypatch.setattr(gallery, "GALLERY_HASH_INDEX", _make_gallery_hash_index(tmp_path))
    monkeypatch.setattr(gallery, "_grant_gallery_safety_pass", lambda seconds: None)
    monkeypatch.setattr(gallery.aiohttp, "ClientSession", lambda: session)
    bot = Bot()

    await gallery.auto_save_monitor.handlers[0](
        bot,
        Event(plain_text="", group_id=1001, message=images),
    )

    saved_files = list((tmp_path / custom.storage_key).glob("*.jpg"))
    assert len(saved_files) == 1
    assert saved_files[0].read_bytes() == b"new-image"
    assert bot.send.await_args.kwargs["message"] == "存了 1 张，另外 2 张之前就有了。"

    bot.send.reset_mock()
    duplicate_images = [
        SimpleNamespace(type="image", data={"url": "https://example.com/old.jpg"}),
        SimpleNamespace(type="image", data={"url": "https://example.com/new-a.jpg"}),
    ]
    await gallery.auto_save_monitor.handlers[0](
        bot,
        Event(plain_text="", group_id=1001, message=duplicate_images),
    )

    assert len(list((tmp_path / custom.storage_key).glob("*.jpg"))) == 1
    assert bot.send.await_args.kwargs["message"] == gallery.DUPLICATE_IMAGES_REPLY


@pytest.mark.asyncio
async def test_save_lock_prevents_concurrent_duplicate_writes(tmp_path):
    save_dir = tmp_path / "custom" / "月城"
    save_dir.mkdir(parents=True)
    hash_index = _make_gallery_hash_index(tmp_path)

    async def save_image(file_name):
        return await hash_index.save_unique(save_dir, file_name, b"same-image")

    results = await asyncio.gather(save_image("first.jpg"), save_image("second.jpg"))

    assert sorted(results) == [False, True]
    assert len(list(save_dir.glob("*.jpg"))) == 1


@pytest.mark.asyncio
async def test_gallery_command_help_images_and_superuser_access(monkeypatch):
    render = AsyncMock(return_value=b"command-help")
    custom = gallery.GalleryDefinition(
        storage_key="custom/33",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "html_to_pic", render)
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "_grant_gallery_safety_pass", lambda seconds: None)

    with pytest.raises(FinishedException) as user_exc:
        await gallery.gallery_help_cmd.handlers[0](
            Event(plain_text="查看图库指令", group_id=1001, user_id="10001")
        )

    assert str(user_exc.value.result) == "[image]"
    user_html = render.await_args.args[0]
    assert "发张[图库名]" in user_html
    assert "开始收图 [图库名]" in user_html
    assert "查看图库别名 [图库名]" in user_html
    assert "固定图库：</span>冬弥、彰人、美食、群友、合照、表情、宠物" in user_html
    assert "自定义图库：</span>33" in user_html
    assert "新建图库[名称]" not in user_html

    render.reset_mock()
    non_superuser_result = await gallery.superuser_gallery_help_cmd.handlers[0](
        Event(plain_text="查看超管图库指令", group_id=1001, user_id="10001")
    )
    assert non_superuser_result is None
    render.assert_not_awaited()

    with pytest.raises(FinishedException) as superuser_exc:
        await gallery.superuser_gallery_help_cmd.handlers[0](
            Event(
                plain_text="查看超管图库指令",
                group_id=1001,
                user_id=gallery.SUPERUSER_QQ,
            )
        )

    assert str(superuser_exc.value.result) == "[image]"
    superuser_html = render.await_args.args[0]
    assert "新建图库[名称]" in superuser_html
    assert "删除图库别名 [图库] [别名]" in superuser_html
    assert "关闭图库休眠" in superuser_html
    assert "重建图库索引" in superuser_html


@pytest.mark.asyncio
async def test_view_gallery_alias_command_renders_all_and_targeted_aliases(monkeypatch):
    render = AsyncMock(return_value=b"alias-help")
    custom = gallery.GalleryDefinition(
        storage_key="custom/33",
        name="33",
        caption_enabled=False,
        permission_tokens=("33",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "html_to_pic", render)
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {"pet": ["毛孩子"], custom.storage_key: ["精选"]})
    monkeypatch.setattr(gallery, "_grant_gallery_safety_pass", lambda seconds: None)

    with pytest.raises(FinishedException) as all_exc:
        await gallery.view_gallery_alias_cmd.handlers[0](
            Event(plain_text="查看图库别名", group_id=1001)
        )

    assert str(all_exc.value.result) == "[image]"
    all_html = render.await_args.args[0]
    assert "宠物" in all_html
    assert "毛孩子" in all_html
    assert "33" in all_html
    assert "精选" in all_html
    assert "内置" in all_html
    assert "手动" in all_html

    render.reset_mock()
    with pytest.raises(FinishedException) as target_exc:
        await gallery.view_gallery_alias_cmd.handlers[0](
            Event(plain_text="查看图库别名 33", group_id=1001)
        )

    assert str(target_exc.value.result) == "[image]"
    target_html = render.await_args.args[0]
    assert "33 · 图库别名" in target_html
    assert "精选" in target_html
    assert "毛孩子" not in target_html


@pytest.mark.asyncio
async def test_view_gallery_alias_command_rejects_unknown_target_and_private_messages(monkeypatch):
    render = AsyncMock(return_value=b"alias-help")
    monkeypatch.setattr(gallery, "html_to_pic", render)
    monkeypatch.setattr(gallery, "GROUP_IMAGE_PERMISSIONS", {1001: ["all"]})

    private_result = await gallery.view_gallery_alias_cmd.handlers[0](
        Event(plain_text="查看图库别名", group_id=None)
    )
    assert private_result is None
    render.assert_not_awaited()

    with pytest.raises(FinishedException) as exc:
        await gallery.view_gallery_alias_cmd.handlers[0](
            Event(plain_text="查看图库别名 不存在", group_id=1001)
        )

    assert str(exc.value.result) == "没有【不存在】这个图库。"
    render.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_gallery_index_command_is_superuser_only(monkeypatch):
    hash_index = SimpleNamespace(
        rebuild=AsyncMock(return_value=GalleryIndexSyncResult(indexed_count=1126, rebuilt=True))
    )
    monkeypatch.setattr(gallery, "GALLERY_HASH_INDEX", hash_index)

    result = await gallery.rebuild_gallery_index_cmd.handlers[0](
        Event(plain_text="重建图库索引", user_id="10001")
    )

    assert result is None
    hash_index.rebuild.assert_not_awaited()

    with pytest.raises(FinishedException) as exc:
        await gallery.rebuild_gallery_index_cmd.handlers[0](
            Event(plain_text="重建图库索引", user_id=gallery.SUPERUSER_QQ)
        )

    assert str(exc.value.result) == "图库索引重建完成，共登记 1126 张图片。"
    hash_index.rebuild.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_gallery_command_is_silent_for_non_superuser(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    custom = gallery.GalleryDefinition(
        storage_key="custom/猫猫",
        name="猫猫",
        caption_enabled=False,
        permission_tokens=("猫猫",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [custom])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path / "images")
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    result = await gallery.delete_gallery_cmd.handlers[0](
        Event(plain_text="删除图库 猫猫", user_id="10001")
    )

    assert result is None
    assert [custom] == gallery.CUSTOM_GALLERIES
    assert not registry_path.exists()


@pytest.mark.asyncio
async def test_delete_gallery_command_removes_gallery_children_links_and_folder(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    target = gallery.GalleryDefinition(
        storage_key="custom/猫猫",
        name="猫猫",
        caption_enabled=False,
        permission_tokens=("猫猫",),
        custom=True,
    )
    child = gallery.GalleryDefinition(
        storage_key="custom/小猫",
        name="小猫",
        caption_enabled=False,
        permission_tokens=("小猫",),
        custom=True,
    )
    grandchild = gallery.GalleryDefinition(
        storage_key="custom/孙猫",
        name="孙猫",
        caption_enabled=False,
        permission_tokens=("孙猫",),
        custom=True,
    )
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [target, child, grandchild])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {
        target.storage_key: "groupmate",
        child.storage_key: target.storage_key,
        grandchild.storage_key: child.storage_key,
    })
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    image_root = tmp_path / "images"
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", image_root)
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    target_dir = image_root / target.storage_key
    target_dir.mkdir(parents=True)
    (target_dir / "a.png").write_bytes(b"x")

    with pytest.raises(FinishedException) as exc:
        await gallery.delete_gallery_cmd.handlers[0](
            Event(plain_text="删除图库 猫猫", user_id=gallery.SUPERUSER_QQ)
        )

    assert "删掉了" in str(exc.value.result)
    assert [child, grandchild] == gallery.CUSTOM_GALLERIES
    assert {grandchild.storage_key: child.storage_key} == gallery.GALLERY_PARENTS
    assert not target_dir.exists()
    saved = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [record["name"] for record in saved["custom_galleries"]] == ["小猫", "孙猫"]
    assert saved["parents"] == {grandchild.storage_key: child.storage_key}


@pytest.mark.asyncio
async def test_delete_gallery_command_rejects_fixed_gallery(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path / "images")
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.delete_gallery_cmd.handlers[0](
            Event(plain_text="删除图库 宠物", user_id=gallery.SUPERUSER_QQ)
        )

    assert "固定图库" in str(exc.value.result)
    assert gallery.CUSTOM_GALLERIES == []
    assert not registry_path.exists()


@pytest.mark.asyncio
async def test_delete_gallery_command_reports_missing_gallery(monkeypatch, tmp_path):
    registry_path = tmp_path / "gallery_registry.json"
    monkeypatch.setattr(gallery, "CUSTOM_GALLERIES", [])
    monkeypatch.setattr(gallery, "GALLERY_ALIASES", {})
    monkeypatch.setattr(gallery, "GALLERY_PARENTS", {})
    monkeypatch.setattr(gallery, "GALLERY_REGISTRY_LOAD_ERROR", False)
    monkeypatch.setattr(gallery, "GALLERY_CREATE_LOCK", asyncio.Lock())
    monkeypatch.setattr(gallery, "IMAGE_BASE_PATH", tmp_path / "images")
    monkeypatch.setattr(gallery, "_get_gallery_registry_path", lambda: registry_path)

    with pytest.raises(FinishedException) as exc:
        await gallery.delete_gallery_cmd.handlers[0](
            Event(plain_text="删除图库 不存在的图库", user_id=gallery.SUPERUSER_QQ)
        )

    assert "没有" in str(exc.value.result)
    assert gallery.CUSTOM_GALLERIES == []
