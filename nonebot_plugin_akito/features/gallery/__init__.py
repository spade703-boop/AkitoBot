"""图库引擎：本地图片随机抽取、手动 / 自动存图、主动发图、图库清单渲染。"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
from html import escape as html_escape
from io import BytesIO
import json
import os
from pathlib import Path
import random
import re
import shutil
import time
import uuid

import aiohttp
from nonebot import get_driver, on_command, on_message, on_regex
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.adapters.onebot.v11 import Message as OB11Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot_plugin_apscheduler import scheduler
from nonebot_plugin_htmlrender import html_to_pic
from PIL import Image as PILImage

from ...core import (
    GROUP_IMAGE_PERMISSIONS,
    IMAGE_BASE_PATH,
    REACTIONS_DB,
    SUPERUSER_QQ,
    call_deepseek_api,
    find_data_path,
    get_base_persona,
    get_data_dir,
    get_memory_key,
    get_user_memory,
    grant_safety_pass,
    is_sleeping,
    sleep_block,
)
from .hash_index import GalleryHashIndex

# ==============================================================================
# 模块 8：相册图库引擎 (IMAGE & GALLERY SYSTEM)
# ==============================================================================

ITEMS_PER_PAGE = 30
MAX_SEND_IMAGE_COUNT = 5
GALLERY_REGISTRY_FILE = "gallery_registry.json"
GALLERY_REGISTRY_VERSION = 2
SUPPORTED_GALLERY_REGISTRY_VERSIONS = {1, GALLERY_REGISTRY_VERSION}
CUSTOM_GALLERY_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\u3400-\u4dbf\u4e00-\u9fff]{1,20}$")
GALLERY_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".gif"}
GALLERY_HASH_INDEX_FILE = "gallery_hash_index.sqlite3"


@dataclass(frozen=True)
class GalleryDefinition:
    storage_key: str
    name: str
    gallery_id: str = ""
    caption_enabled: bool = True
    save_aliases: tuple[str, ...] = ()
    send_aliases: tuple[str, ...] = ()
    collect_aliases: tuple[str, ...] = ()
    list_aliases: tuple[str, ...] = ()
    prompt_hint: str = ""
    caption_subject: str = "照片"
    permission_tokens: tuple[str, ...] = ()
    custom: bool = False


FIXED_GALLERIES = (
    GalleryDefinition(
        storage_key="toya",
        name="冬弥",
        save_aliases=("冬弥", "搭档", "toya", "老婆"),
        send_aliases=("冬弥", "搭档", "toya", "老婆"),
        collect_aliases=("冬弥", "搭档", "toya", "老婆"),
        list_aliases=("冬弥", "搭档", "toya"),
        prompt_hint="表现：嘴上说'为什么要给你看'，但还是发了。",
        caption_subject="搭档（青柳冬弥）的照片",
        permission_tokens=("toya", "冬弥"),
    ),
    GalleryDefinition(
        storage_key="self",
        name="彰人",
        save_aliases=("你自己", "彰人", "自拍", "akito"),
        send_aliases=("你自己", "自拍", "彰人", "akito"),
        collect_aliases=("你自己", "彰人", "自拍", "akito"),
        list_aliases=("你", "彰人", "自拍", "self", "akito"),
        prompt_hint="表现：稍微有点自恋但又装作不在意。",
        caption_subject="自己的帅气自拍/单人照",
        permission_tokens=("self", "彰人"),
    ),
    GalleryDefinition(
        storage_key="food",
        name="美食",
        save_aliases=("松饼", "吃的", "蛋糕", "甜点"),
        send_aliases=("松饼", "吃的", "蛋糕", "甜点"),
        collect_aliases=("松饼", "吃的", "蛋糕", "甜点"),
        list_aliases=("吃", "食", "food", "松饼", "蛋糕", "甜点"),
        prompt_hint="发一张探店图并评价。",
        caption_subject="刚吃过的甜点/松饼等美食照",
        permission_tokens=("food", "美食"),
    ),
    GalleryDefinition(
        storage_key="groupmate",
        name="群友",
        caption_enabled=False,
        save_aliases=("群友",),
        send_aliases=("群友",),
        collect_aliases=("群友",),
        list_aliases=("群友", "groupmate"),
        permission_tokens=("groupmate", "群友"),
    ),
    GalleryDefinition(
        storage_key="vbs",
        name="合照",
        save_aliases=("合照", "vbs", "队友"),
        send_aliases=("合照", "vbs", "队友"),
        collect_aliases=("合照", "vbs", "队友"),
        list_aliases=("合照", "vbs", "队友"),
        prompt_hint="发一张大家的日常。",
        caption_subject="VBS小队成员的合照或日常",
        permission_tokens=("vbs", "合照"),
    ),
    GalleryDefinition(
        storage_key="meme",
        name="表情",
        save_aliases=("表情", "梗图", "meme"),
        send_aliases=("表情", "梗图", "meme"),
        collect_aliases=("表情", "meme", "梗图"),
        list_aliases=("表情", "meme", "梗图"),
        prompt_hint="随便发一张手机里存的表情。",
        caption_subject="手机里存的搞笑表情包/梗图",
        permission_tokens=("meme", "表情"),
    ),
    GalleryDefinition(
        storage_key="pet",
        name="宠物",
        caption_enabled=False,
        save_aliases=("宠物", "卡车", "丑猫"),
        send_aliases=("宠物", "卡车", "丑猫"),
        collect_aliases=("宠物", "卡车", "丑猫"),
        list_aliases=("宠物", "pet", "卡车", "丑猫"),
        permission_tokens=("pet", "宠物", "卡车", "丑猫"),
    ),
)
FIXED_GALLERY_BY_KEY = {gallery.storage_key: gallery for gallery in FIXED_GALLERIES}
IMAGE_CATEGORIES = tuple(FIXED_GALLERY_BY_KEY)

DEFAULT_SAVE_REPLIES = {
    "toya": ["……哦，谢了。"],
    "self": ["……发这个干嘛。"],
    "food": ["……看起来还行。"],
    "groupmate": ["又在说什么傻话？"],
    "vbs": ["……哼。"],
    "meme": ["……啧。"],
    "pet": ["……行，存下了。", "收到了。", "这张我存了。"],
    "captionless": ["……行，存下了。", "收到了。", "这张我存了。"],
}
DEFAULT_UNKNOWN_GALLERY_REPLIES = [
    "哈？根本没有这种图库。别随便给我编啊。",
    "没这类照片。换个名字再说。",
    "你到底想看哪一类？先把图库名说对。",
    "这种图库不存在。看清楚再发指令啊。",
    "都说了没有。换一个。",
]
DUPLICATE_IMAGE_REPLY = "……这张已经存过了。别重复塞给我。"
DUPLICATE_IMAGES_REPLY = "……这些图已经存过了。别重复塞给我。"
GALLERY_USER_HELP_ROWS = (
    ("发张[图库名] [数量]", "严格匹配后随机发图，多图使用折叠消息，数量限 1–5；未知图库会拒绝。"),
    ("存[图库名]", "保存本条或引用消息中的图片；全图库已存在时拒绝重复写入。"),
    ("开始收图 [图库名]", "连续保存图片，自动跳过所有图库中已经存在的图片。"),
    ("停止收图", "结束当前会话的连续收图模式。"),
    ("图库清单 [图库名] [页码]", "生成指定图库的缩略图清单；页码可省略。"),
    ("查看图库别名 [图库名]", "查看全部图库或指定图库的内置与手动别名。"),
    ("查看图库指令", "查看这张普通图库指令说明图。"),
)
GALLERY_SUPERUSER_HELP_ROWS = (
    ("新建图库[名称]", "创建一个不附带发图配文的自定义图库。"),
    ("添加图库别名 [图库] [别名]", "给固定或自定义图库添加一个全局唯一别名。"),
    ("删除图库别名 [图库] [别名]", "删除指定图库的手动别名；固定别名不可删除。"),
    ("关联子图库 [子图库] [父图库]", "让父图库抽图和清单递归包含指定自定义子图库。"),
    ("取消子图库关联 [子图库]", "解除指定自定义图库与父图库的关系。"),
    ("删除图库[名称]", "删除自定义图库、目录和图片，并解除相关子图库关联。"),
    ("关闭图库休眠", "临时绕过凌晨 0–6 点的图库睡眠拦截。"),
    ("开启图库休眠", "恢复凌晨 0–6 点的图库睡眠拦截。"),
    ("重建图库索引", "立即按原图片全量重建图库去重索引。"),
    ("查看超管图库指令", "查看这张超管图库指令说明图。"),
)
GALLERY_SLEEP_ENABLED = True


def _gallery_sleep_block(
    pool_key: str,
    silent_chance: float = 0.0,
    fallback: str = "……zzZ",
) -> str | None:
    """Apply the gallery-only sleep gate, unless the superuser disabled it."""
    if not GALLERY_SLEEP_ENABLED:
        return ""
    return sleep_block(pool_key, silent_chance=silent_chance, fallback=fallback)


def _grant_gallery_safety_pass(seconds: int = 5) -> None:
    """Keep deep-night self-complaints enabled while gallery sleep is disabled."""
    if not GALLERY_SLEEP_ENABLED and is_sleeping():
        return
    grant_safety_pass(seconds)


def _normalize_gallery_name(name: str) -> str:
    return name.strip().casefold()


def _definition_names(
    gallery: GalleryDefinition,
    aliases_by_key: dict[str, list[str]] | None = None,
) -> set[str]:
    if aliases_by_key is None:
        aliases_by_key = globals().get("GALLERY_ALIASES", {})
    values = {
        gallery.storage_key,
        gallery.name,
        *gallery.save_aliases,
        *gallery.send_aliases,
        *gallery.collect_aliases,
        *gallery.list_aliases,
        *gallery.permission_tokens,
        *aliases_by_key.get(gallery.storage_key, []),
    }
    return {_normalize_gallery_name(value) for value in values}


def _validate_custom_gallery_name(name: str, custom_galleries: list[GalleryDefinition]) -> str:
    normalized = _normalize_gallery_name(name)
    if not normalized:
        return "missing"
    if not CUSTOM_GALLERY_NAME_RE.fullmatch(name.strip()) or normalized == "all":
        return "invalid"
    for gallery in (*FIXED_GALLERIES, *custom_galleries):
        if normalized in _definition_names(gallery):
            return "duplicate"
    return ""


def _custom_gallery_from_record(record: dict) -> GalleryDefinition | None:
    gallery_id = record.get("id")
    name = record.get("name")
    if not isinstance(gallery_id, str) or not re.fullmatch(r"[0-9a-f]{32}", gallery_id):
        return None
    if not isinstance(name, str):
        return None
    directory = record.get("directory", gallery_id)
    if not isinstance(directory, str) or (
        directory != gallery_id and not CUSTOM_GALLERY_NAME_RE.fullmatch(directory)
    ):
        return None
    return GalleryDefinition(
        storage_key=f"custom/{directory}",
        name=name.strip(),
        gallery_id=gallery_id,
        caption_enabled=False,
        permission_tokens=(name.strip(),),
        custom=True,
    )


def _get_gallery_registry_path() -> Path:
    return find_data_path(GALLERY_REGISTRY_FILE, subdirs=("",)) or get_data_dir() / GALLERY_REGISTRY_FILE


def _would_create_gallery_cycle(child_key: str, parent_key: str, parents: dict[str, str]) -> bool:
    current_key = parent_key
    visited = {child_key}
    while current_key:
        if current_key in visited:
            return True
        visited.add(current_key)
        current_key = parents.get(current_key, "")
    return False


def _migrate_custom_gallery_directory(gallery: GalleryDefinition) -> tuple[GalleryDefinition, bool]:
    legacy_key = f"custom/{gallery.gallery_id}"
    target_key = f"custom/{gallery.name}"
    if gallery.storage_key != legacy_key:
        return gallery, False

    legacy_dir = IMAGE_BASE_PATH / legacy_key
    target_dir = IMAGE_BASE_PATH / target_key
    if legacy_dir.exists() and target_dir.exists():
        logger.error(f"图库目录迁移冲突：{legacy_dir} 与 {target_dir} 同时存在，暂时保留 UUID 目录")
        return gallery, False
    try:
        if legacy_dir.exists():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            legacy_dir.rename(target_dir)
        return replace(gallery, storage_key=target_key), True
    except OSError as exc:
        logger.error(f"迁移图库目录 {legacy_dir} -> {target_dir} 失败，暂时保留 UUID 目录: {exc}")
        return gallery, False


def _load_custom_gallery_registry(
    path: Path,
) -> tuple[list[GalleryDefinition], dict[str, list[str]], dict[str, str], bool]:
    if not path.exists():
        return [], {}, {}, False
    try:
        with open(path, encoding="utf-8-sig") as file:
            payload = json.load(file)
    except Exception as exc:
        logger.error(f"读取动态图库注册表失败，已锁定新建操作: {exc}")
        return [], {}, {}, True

    if not isinstance(payload, dict) or payload.get("schema_version") not in SUPPORTED_GALLERY_REGISTRY_VERSIONS:
        logger.error("动态图库注册表格式或版本无效，已锁定新建操作")
        return [], {}, {}, True
    records = payload.get("custom_galleries")
    if not isinstance(records, list):
        logger.error("动态图库注册表缺少 custom_galleries 列表，已锁定新建操作")
        return [], {}, {}, True

    galleries: list[GalleryDefinition] = []
    for record in records:
        if not isinstance(record, dict):
            logger.error("动态图库注册表包含无效记录，已锁定新建操作")
            return [], {}, {}, True
        gallery = _custom_gallery_from_record(record)
        if gallery is None or _validate_custom_gallery_name(gallery.name, galleries):
            logger.error("动态图库注册表包含无效或冲突名称，已锁定新建操作")
            return [], {}, {}, True
        galleries.append(gallery)

    key_migrations: dict[str, str] = {}
    migrated_galleries: list[GalleryDefinition] = []
    registry_needs_upgrade = payload.get("schema_version") != GALLERY_REGISTRY_VERSION
    for gallery in galleries:
        previous_key = gallery.storage_key
        migrated_gallery, migrated = _migrate_custom_gallery_directory(gallery)
        migrated_galleries.append(migrated_gallery)
        if migrated:
            key_migrations[previous_key] = migrated_gallery.storage_key
            registry_needs_upgrade = True
    galleries = migrated_galleries

    gallery_by_key = {gallery.storage_key: gallery for gallery in (*FIXED_GALLERIES, *galleries)}
    aliases_by_key: dict[str, list[str]] = {}
    raw_aliases = payload.get("aliases", {})
    if not isinstance(raw_aliases, dict):
        logger.error("动态图库注册表 aliases 字段无效，已锁定管理操作")
        return [], {}, {}, True
    occupied_names = {
        name
        for gallery in gallery_by_key.values()
        for name in _definition_names(gallery, aliases_by_key={})
    }
    for storage_key, aliases in raw_aliases.items():
        storage_key = key_migrations.get(storage_key, storage_key)
        if storage_key not in gallery_by_key or not isinstance(aliases, list):
            logger.error("动态图库注册表包含未知图库或无效别名列表，已锁定管理操作")
            return [], {}, {}, True
        normalized_aliases: list[str] = []
        for alias in aliases:
            if not isinstance(alias, str) or not CUSTOM_GALLERY_NAME_RE.fullmatch(alias.strip()):
                logger.error("动态图库注册表包含无效别名，已锁定管理操作")
                return [], {}, {}, True
            normalized = _normalize_gallery_name(alias)
            if normalized in occupied_names:
                logger.error("动态图库注册表包含冲突别名，已锁定管理操作")
                return [], {}, {}, True
            occupied_names.add(normalized)
            normalized_aliases.append(alias.strip())
        if normalized_aliases:
            aliases_by_key[storage_key] = normalized_aliases

    raw_parents = payload.get("parents", {})
    if not isinstance(raw_parents, dict):
        logger.error("动态图库注册表 parents 字段无效，已锁定管理操作")
        return [], {}, {}, True
    parents: dict[str, str] = {}
    custom_keys = {gallery.storage_key for gallery in galleries}
    for child_key, parent_key in raw_parents.items():
        child_key = key_migrations.get(child_key, child_key)
        parent_key = key_migrations.get(parent_key, parent_key)
        if child_key not in custom_keys or parent_key not in gallery_by_key or child_key == parent_key:
            logger.error("动态图库注册表包含无效父子关系，已锁定管理操作")
            return [], {}, {}, True
        parents[child_key] = parent_key
    for child_key, parent_key in parents.items():
        if _would_create_gallery_cycle(child_key, parent_key, parents):
            logger.error("动态图库注册表包含循环父子关系，已锁定管理操作")
            return [], {}, {}, True
    if registry_needs_upgrade:
        try:
            _save_custom_gallery_registry(path, galleries, aliases_by_key, parents)
        except OSError as exc:
            logger.error(f"升级动态图库注册表失败，已锁定管理操作: {exc}")
            return [], {}, {}, True
    return galleries, aliases_by_key, parents, False


def _save_custom_gallery_registry(
    path: Path,
    galleries: list[GalleryDefinition],
    aliases_by_key: dict[str, list[str]] | None = None,
    parents: dict[str, str] | None = None,
) -> None:
    if aliases_by_key is None:
        aliases_by_key = globals().get("GALLERY_ALIASES", {})
    if parents is None:
        parents = globals().get("GALLERY_PARENTS", {})
    payload = {
        "schema_version": GALLERY_REGISTRY_VERSION,
        "custom_galleries": [
            {
                "id": gallery.gallery_id or gallery.storage_key.removeprefix("custom/"),
                "name": gallery.name,
                "directory": gallery.storage_key.removeprefix("custom/"),
            }
            for gallery in galleries
        ],
        "aliases": {key: aliases for key, aliases in aliases_by_key.items() if aliases},
        "parents": dict(parents),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


CUSTOM_GALLERIES, GALLERY_ALIASES, GALLERY_PARENTS, GALLERY_REGISTRY_LOAD_ERROR = (
    _load_custom_gallery_registry(_get_gallery_registry_path())
)
GALLERY_CREATE_LOCK = asyncio.Lock()
GALLERY_HASH_INDEX = GalleryHashIndex(
    image_root=IMAGE_BASE_PATH,
    database_path=get_data_dir() / GALLERY_HASH_INDEX_FILE,
    fixed_storage_keys=tuple(gallery.storage_key for gallery in FIXED_GALLERIES),
    image_suffixes=GALLERY_IMAGE_SUFFIXES,
)


async def initialize_gallery_hash_index() -> None:
    try:
        result = await GALLERY_HASH_INDEX.sync_incremental()
        logger.info(f"图库哈希索引已校准，共登记 {result.indexed_count} 张图片")
    except Exception as exc:
        logger.error(f"图库哈希索引启动校准失败，将在存图或定时任务时重试: {exc}")


_driver = get_driver()
if hasattr(_driver, "on_startup"):
    _driver.on_startup(initialize_gallery_hash_index)


@scheduler.scheduled_job(
    "interval",
    minutes=10,
    id="sync_gallery_hash_index",
    max_instances=1,
    coalesce=True,
)
async def sync_gallery_hash_index() -> None:
    try:
        result = await GALLERY_HASH_INDEX.sync_incremental()
        if result.updated_count or result.deleted_count or result.failed_count or result.rebuilt:
            logger.info(
                "图库哈希索引后台同步完成: "
                f"登记 {result.indexed_count}，更新 {result.updated_count}，"
                f"删除 {result.deleted_count}，失败 {result.failed_count}"
            )
    except Exception as exc:
        logger.error(f"图库哈希索引后台同步失败，将在下次任务时重试: {exc}")


def _all_galleries() -> tuple[GalleryDefinition, ...]:
    return (*FIXED_GALLERIES, *CUSTOM_GALLERIES)


def _get_gallery(storage_key: str) -> GalleryDefinition | None:
    return next((gallery for gallery in _all_galleries() if gallery.storage_key == storage_key), None)


def _find_gallery_exact(text: str) -> GalleryDefinition | None:
    normalized = _normalize_gallery_name(text)
    if not normalized:
        return None
    return next((gallery for gallery in _all_galleries() if normalized in _definition_names(gallery)), None)


def _find_custom_gallery_exact(text: str) -> GalleryDefinition | None:
    normalized = _normalize_gallery_name(text)
    if not normalized:
        return None
    return next(
        (gallery for gallery in CUSTOM_GALLERIES if normalized in _definition_names(gallery)),
        None,
    )


def _match_fixed_gallery(text: str, aliases_attr: str) -> GalleryDefinition | None:
    for gallery in FIXED_GALLERIES:
        aliases = (*getattr(gallery, aliases_attr), *GALLERY_ALIASES.get(gallery.storage_key, []))
        if any(alias in text for alias in aliases):
            return gallery
    return None


def _validate_gallery_alias(alias: str) -> str:
    normalized = _normalize_gallery_name(alias)
    if not normalized or normalized == "all" or not CUSTOM_GALLERY_NAME_RE.fullmatch(alias.strip()):
        return "invalid"
    if any(normalized in _definition_names(gallery) for gallery in _all_galleries()):
        return "duplicate"
    return ""


def _gallery_storage_keys_for_read(storage_key: str) -> list[str]:
    storage_keys: list[str] = []
    pending = [storage_key]
    while pending:
        current_key = pending.pop(0)
        if current_key in storage_keys:
            continue
        storage_keys.append(current_key)
        pending.extend(
            child_key
            for child_key, parent_key in GALLERY_PARENTS.items()
            if parent_key == current_key
        )
    return storage_keys


def _get_direct_child_galleries(storage_key: str) -> list[GalleryDefinition]:
    return [
        gallery
        for gallery in CUSTOM_GALLERIES
        if GALLERY_PARENTS.get(gallery.storage_key) == storage_key
    ]


def _gallery_allowed(group_id: int | None, gallery: GalleryDefinition) -> bool:
    allowed = GROUP_IMAGE_PERMISSIONS.get(group_id, [])
    normalized_allowed = {_normalize_gallery_name(str(token)) for token in allowed}
    if "all" in normalized_allowed:
        return True
    return bool(normalized_allowed & _definition_names(gallery))


def _resolve_save_category_and_reply(
    text: str,
    replies_db: dict,
    chooser: Callable[[list[str]], str] = random.choice,
) -> tuple[str, str]:
    """Resolve which category a manual save request targets and pick its reply."""
    match = re.fullmatch(r"存\s*(\S+)", text.strip())
    gallery = _find_gallery_exact(match.group(1)) if match else None
    if gallery is None:
        return "", ""
    reply_key = "captionless" if gallery.custom else gallery.storage_key
    replies = replies_db.get(reply_key) or DEFAULT_SAVE_REPLIES[reply_key]
    return gallery.storage_key, chooser(replies)


def _extract_image_urls(message: object) -> list[str]:
    """Extract usable image URL/file values from an OneBot message."""
    try:
        segments = iter(message)  # type: ignore[arg-type]
    except TypeError:
        return []
    image_urls: list[str] = []
    for segment in segments:
        if getattr(segment, "type", "") != "image":
            continue
        data = getattr(segment, "data", {})
        if isinstance(data, dict):
            image_value = data.get("url") or data.get("file")
            if image_value:
                image_urls.append(str(image_value))
    return image_urls


def _extract_image_url(message: object) -> str:
    image_urls = _extract_image_urls(message)
    return image_urls[0] if image_urls else ""


def _build_collect_session_key(group_id: int | None, user_id: str) -> str:
    """Build the collecting session key for a group or private chat."""
    return f"group_{group_id}" if group_id else f"private_{user_id}"


def _resolve_collect_category(text: str) -> str:
    """Resolve an exact gallery name or alias for collect mode."""
    gallery = _find_gallery_exact(text)
    return gallery.storage_key if gallery else ""


def _resolve_send_image_request(text: str) -> tuple[str, str]:
    """Resolve only an exact existing gallery name or alias."""
    gallery = _find_gallery_exact(text)
    return (gallery.storage_key, gallery.prompt_hint) if gallery else ("", "")


def _parse_send_image_request(text: str) -> tuple[str, int, bool]:
    """Parse an exact gallery target with an optional trailing image count."""
    raw = text.strip()
    if not raw:
        return "", 1, False

    if _find_gallery_exact(raw) is not None:
        return raw, 1, True

    parts = raw.split()
    if len(parts) == 2:
        target, count_text = parts
        if not count_text.isdecimal():
            return target, 1, False
        try:
            requested_count = int(count_text)
        except ValueError:
            return target, 1, False
        return target, requested_count, 1 <= requested_count <= MAX_SEND_IMAGE_COUNT
    if len(parts) != 1:
        return parts[0] if parts else raw, 1, False

    attached_count = re.fullmatch(r"(?P<target>.+?)(?P<count>\d+)", raw)
    if not attached_count:
        return raw, 1, True
    target = attached_count.group("target")
    if _find_gallery_exact(target) is None:
        return raw, 1, True
    try:
        requested_count = int(attached_count.group("count"))
    except ValueError:
        return target, 1, False
    return target, requested_count, 1 <= requested_count <= MAX_SEND_IMAGE_COUNT


def _has_gallery_name_prefix(text: str) -> bool:
    """Return whether text starts with a gallery name but contains extra content."""
    normalized = _normalize_gallery_name(text)
    return any(
        normalized.startswith(name) and normalized != name
        for gallery in _all_galleries()
        for name in _definition_names(gallery)
    )


def _resolve_gallery_category(text: str) -> str:
    """Resolve the category named in a gallery list request."""
    gallery = _find_custom_gallery_exact(text) or _match_fixed_gallery(text, "list_aliases")
    return gallery.storage_key if gallery else ""


def _paginate_gallery(total_files: int, requested_page: int, items_per_page: int) -> tuple[int, int, int, int]:
    """Clamp the requested page and return (page, total_pages, start, end)."""
    total_pages = max(1, (total_files + items_per_page - 1) // items_per_page)
    page = max(1, min(requested_page, total_pages))
    start = (page - 1) * items_per_page
    end = page * items_per_page
    return page, total_pages, start, end


def _build_gallery_command_help_html(
    title: str,
    rows: tuple[tuple[str, str], ...],
    note: str,
) -> str:
    cards = "".join(
        f"""
        <div class="command-card">
            <div class="command">{command}</div>
            <div class="description">{description}</div>
        </div>
        """
        for command, description in rows
    )
    return f"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
        <meta charset="utf-8">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                width: 860px;
                margin: 0;
                padding: 34px;
                color: #352f33;
                background: linear-gradient(145deg, #fff7ef 0%, #f5e8e4 100%);
                font-family: "Microsoft YaHei", "Noto Sans SC", sans-serif;
            }}
            .sheet {{
                padding: 30px;
                border: 2px solid #e7a26f;
                border-radius: 24px;
                background: rgba(255, 255, 255, 0.9);
                box-shadow: 0 14px 36px rgba(111, 65, 50, 0.14);
            }}
            h1 {{ margin: 0 0 8px; color: #b45d38; font-size: 34px; }}
            .subtitle {{ margin-bottom: 24px; color: #7c6c68; font-size: 15px; }}
            .commands {{ display: grid; gap: 12px; }}
            .command-card {{
                display: grid;
                grid-template-columns: 280px 1fr;
                gap: 18px;
                align-items: center;
                padding: 16px 18px;
                border-left: 6px solid #e88a52;
                border-radius: 12px;
                background: #fffaf6;
            }}
            .command {{ color: #9f4726; font-size: 19px; font-weight: 700; }}
            .description {{ color: #51474a; font-size: 16px; line-height: 1.6; }}
            .note {{
                margin-top: 22px;
                padding: 14px 16px;
                border-radius: 10px;
                color: #6a554e;
                background: #f8ece3;
                font-size: 14px;
                line-height: 1.7;
            }}
            .note strong {{ color: #a84f2d; font-size: 17px; }}
            .inventory-label {{ color: #8f513a; font-weight: 700; }}
            .inventory-tip {{ margin-top: 6px; color: #89736b; font-size: 13px; }}
        </style>
    </head>
    <body>
        <main class="sheet">
            <h1>{title}</h1>
            <div class="subtitle">AkitoBot Gallery Commands</div>
            <div class="commands">{cards}</div>
            <div class="note">{note}</div>
        </main>
    </body>
    </html>
    """


def _unique_gallery_aliases(values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    aliases: list[str] = []
    for value in values:
        normalized = _normalize_gallery_name(value)
        if normalized and normalized not in seen:
            seen.add(normalized)
            aliases.append(value)
    return aliases


def _build_gallery_alias_html(target: GalleryDefinition | None = None) -> str:
    if target is None:
        galleries = [*FIXED_GALLERIES, *sorted(CUSTOM_GALLERIES, key=lambda item: item.name.casefold())]
    else:
        galleries = [target]

    cards: list[str] = []
    for gallery in galleries:
        builtin_aliases = _unique_gallery_aliases(
            (
                *gallery.save_aliases,
                *gallery.send_aliases,
                *gallery.collect_aliases,
                *gallery.list_aliases,
            )
        )
        manual_aliases = GALLERY_ALIASES.get(gallery.storage_key, [])
        builtin_text = "、".join(html_escape(alias) for alias in builtin_aliases) or "无"
        manual_text = "、".join(html_escape(alias) for alias in manual_aliases) or "无"
        cards.append(
            f"""
            <div class="alias-card">
                <div class="gallery-name">{html_escape(gallery.name)}</div>
                <div class="alias-line"><span class="alias-label builtin">内置</span>{builtin_text}</div>
                <div class="alias-line"><span class="alias-label manual">手动</span>{manual_text}</div>
            </div>
            """
        )

    if not cards:
        cards.append('<div class="empty">当前没有可展示的图库。</div>')
    title = "图库别名" if target is None else f"{html_escape(target.name)} · 图库别名"
    note = "内置别名由程序固定提供；手动别名可用“添加图库别名”与“删除图库别名”管理。"
    return f"""
    <!doctype html>
    <html lang="zh-CN">
    <head>
        <meta charset="utf-8">
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                width: 700px;
                margin: 0;
                padding: 20px;
                color: #352f33;
                background: linear-gradient(145deg, #fff7ef 0%, #f5e8e4 100%);
                font-family: "Microsoft YaHei", "Noto Sans SC", sans-serif;
            }}
            .sheet {{
                padding: 20px;
                border: 2px solid #e7a26f;
                border-radius: 18px;
                background: rgba(255, 255, 255, 0.92);
                box-shadow: 0 10px 26px rgba(111, 65, 50, 0.12);
            }}
            h1 {{ margin: 0 0 4px; color: #b45d38; font-size: 27px; }}
            .subtitle {{ margin-bottom: 14px; color: #7c6c68; font-size: 12px; }}
            .aliases {{ display: grid; gap: 8px; }}
            .alias-card {{
                display: grid;
                grid-template-columns: 100px 1fr;
                gap: 3px 12px;
                padding: 10px 12px;
                border-left: 4px solid #e88a52;
                border-radius: 9px;
                background: #fffaf6;
                font-size: 13px;
                line-height: 1.5;
            }}
            .gallery-name {{ grid-row: span 2; align-self: center; color: #9f4726; font-size: 16px; font-weight: 700; }}
            .alias-line {{ color: #51474a; overflow-wrap: anywhere; }}
            .alias-label {{ display: inline-block; min-width: 34px; margin-right: 6px; font-size: 11px; font-weight: 700; }}
            .builtin {{ color: #a06b4c; }}
            .manual {{ color: #4d7d70; }}
            .empty {{ padding: 16px; color: #7c6c68; text-align: center; }}
            .note {{ margin-top: 14px; color: #89736b; font-size: 11px; line-height: 1.5; }}
        </style>
    </head>
    <body>
        <main class="sheet">
            <h1>{title}</h1>
            <div class="subtitle">AkitoBot Gallery Aliases</div>
            <div class="aliases">{"".join(cards)}</div>
            <div class="note">{note}</div>
        </main>
    </body>
    </html>
    """


def _build_gallery_inventory_note() -> str:
    fixed_names = "、".join(gallery.name for gallery in FIXED_GALLERIES)
    custom_names = "、".join(
        gallery.name
        for gallery in sorted(CUSTOM_GALLERIES, key=lambda item: item.name.casefold())
    ) or "暂无"
    return (
        "<strong>已有图库</strong><br>"
        f"<span class=\"inventory-label\">固定图库：</span>{fixed_names}<br>"
        f"<span class=\"inventory-label\">自定义图库：</span>{custom_names}"
        "<div class=\"inventory-tip\">图库名和别名必须完整匹配。</div>"
    )


async def _finish_gallery_command_help(
    matcher,
    title: str,
    rows: tuple[tuple[str, str], ...],
    note: str,
) -> None:
    try:
        html = _build_gallery_command_help_html(title, rows, note)
        pic = await html_to_pic(html, viewport={"width": 930, "height": 100})
    except Exception as exc:
        logger.error(f"图库指令说明图渲染失败: {exc}")
        await matcher.finish("……指令图没生成出来。稍后再试。")
        return
    _grant_gallery_safety_pass(5)
    await matcher.finish(MessageSegment.image(pic))


create_gallery_cmd = on_regex(r"^新建图库\s*.*$", priority=5, block=True)
add_gallery_alias_cmd = on_regex(r"^添加图库别名\s*.*$", priority=5, block=True)
delete_gallery_alias_cmd = on_regex(r"^删除图库别名\s*.*$", priority=5, block=True)
link_child_gallery_cmd = on_regex(r"^关联子图库\s*.*$", priority=5, block=True)
unlink_child_gallery_cmd = on_regex(r"^取消子图库关联\s*.*$", priority=5, block=True)
delete_gallery_cmd = on_regex(r"^删除图库\s*.*$", priority=5, block=True)
disable_gallery_sleep_cmd = on_regex(r"^关闭图库休眠\s*$", priority=5, block=True)
enable_gallery_sleep_cmd = on_regex(r"^开启图库休眠\s*$", priority=5, block=True)
rebuild_gallery_index_cmd = on_regex(r"^重建图库索引\s*$", priority=5, block=True)
gallery_help_cmd = on_regex(r"^查看图库指令\s*$", priority=5, block=True)
superuser_gallery_help_cmd = on_regex(r"^查看超管图库指令\s*$", priority=5, block=True)
view_gallery_alias_cmd = on_regex(r"^查看图库别名(?:\s+.*)?$", priority=5, block=True)


@gallery_help_cmd.handle()
async def _(event: Event):
    if not isinstance(event, GroupMessageEvent) or event.group_id not in GROUP_IMAGE_PERMISSIONS:
        return
    await _finish_gallery_command_help(
        gallery_help_cmd,
        "普通图库指令",
        GALLERY_USER_HELP_ROWS,
        _build_gallery_inventory_note(),
    )


@superuser_gallery_help_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    await _finish_gallery_command_help(
        superuser_gallery_help_cmd,
        "超管图库指令",
        GALLERY_SUPERUSER_HELP_ROWS,
        "仅超管可用；其他用户发送这些指令时会静默处理。",
    )


@view_gallery_alias_cmd.handle()
async def _(event: Event):
    if not isinstance(event, GroupMessageEvent) or event.group_id not in GROUP_IMAGE_PERMISSIONS:
        return
    target_text = re.sub(r"^查看图库别名\s*", "", event.get_plaintext().strip(), count=1).strip()
    target = None
    if target_text:
        if len(target_text.split()) != 1:
            await view_gallery_alias_cmd.finish("格式是“查看图库别名 [图库名]”。")
        target = _find_gallery_exact(target_text)
        if target is None:
            await view_gallery_alias_cmd.finish(f"没有【{target_text}】这个图库。")
    try:
        html = _build_gallery_alias_html(target)
        pic = await html_to_pic(html, viewport={"width": 740, "height": 100})
    except Exception as exc:
        logger.error(f"图库别名图渲染失败: {exc}")
        await view_gallery_alias_cmd.finish("……别名图没生成出来。稍后再试。")
        return
    _grant_gallery_safety_pass(5)
    await view_gallery_alias_cmd.finish(MessageSegment.image(pic))


@disable_gallery_sleep_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    global GALLERY_SLEEP_ENABLED
    GALLERY_SLEEP_ENABLED = False
    await disable_gallery_sleep_cmd.finish("……行，图库休眠先关了。测试完记得开回来。")


@enable_gallery_sleep_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    global GALLERY_SLEEP_ENABLED
    GALLERY_SLEEP_ENABLED = True
    await enable_gallery_sleep_cmd.finish("图库休眠恢复。凌晨六点前别想让我发图。")


@rebuild_gallery_index_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    try:
        result = await GALLERY_HASH_INDEX.rebuild()
    except Exception as exc:
        logger.error(f"超管重建图库哈希索引失败: {exc}")
        await rebuild_gallery_index_cmd.finish("……索引没重建成功。原图片没动，稍后再试。")
        return
    reply = f"图库索引重建完成，共登记 {result.indexed_count} 张图片。"
    if result.failed_count:
        reply += f"另有 {result.failed_count} 个文件读取失败。"
    await rebuild_gallery_index_cmd.finish(reply)


@create_gallery_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    name = re.sub(r"^新建图库\s*", "", event.get_plaintext().strip(), count=1).strip()
    if not name:
        await create_gallery_cmd.finish("图库名呢？格式是“新建图库XX”。")

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await create_gallery_cmd.finish("……注册表读坏了，没法新建。先检查 gallery_registry.json。")

        validation_error = _validate_custom_gallery_name(name, CUSTOM_GALLERIES)
        if validation_error == "duplicate":
            await create_gallery_cmd.finish(f"【{name}】已经有了。别让我重复建。")
        if validation_error:
            await create_gallery_cmd.finish("名字不合规。只能用 1 到 20 个中英文字、数字、下划线或短横线。")

        gallery = GalleryDefinition(
            storage_key=f"custom/{name}",
            name=name,
            gallery_id=uuid.uuid4().hex,
            caption_enabled=False,
            permission_tokens=(name,),
            custom=True,
        )
        save_dir = IMAGE_BASE_PATH / gallery.storage_key
        try:
            save_dir.mkdir(parents=True, exist_ok=False)
            CUSTOM_GALLERIES.append(gallery)
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            if gallery in CUSTOM_GALLERIES:
                CUSTOM_GALLERIES.remove(gallery)
            try:
                save_dir.rmdir()
            except OSError:
                pass
            logger.error(f"新建图库失败: {exc}")
            await create_gallery_cmd.finish("……没建成。稍后再试。")

    await create_gallery_cmd.finish(f"……建好了。【{name}】图库，别乱塞东西进去。")


@add_gallery_alias_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    params = re.sub(r"^添加图库别名\s*", "", event.get_plaintext().strip(), count=1).split()
    if len(params) != 2:
        await add_gallery_alias_cmd.finish("格式是“添加图库别名 图库名 别名”。")
    gallery_name, alias = params

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await add_gallery_alias_cmd.finish("……注册表读坏了，先检查 gallery_registry.json。")
        gallery = _find_gallery_exact(gallery_name)
        if gallery is None:
            await add_gallery_alias_cmd.finish(f"没有【{gallery_name}】这个图库。")
        validation_error = _validate_gallery_alias(alias)
        if validation_error == "duplicate":
            await add_gallery_alias_cmd.finish(f"【{alias}】已经被图库名称或别名占用了。")
        if validation_error:
            await add_gallery_alias_cmd.finish("别名不合规。只能用 1 到 20 个中英文字、数字、下划线或短横线。")

        aliases = GALLERY_ALIASES.setdefault(gallery.storage_key, [])
        aliases.append(alias)
        try:
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            aliases.remove(alias)
            if not aliases:
                GALLERY_ALIASES.pop(gallery.storage_key, None)
            logger.error(f"添加图库别名失败: {exc}")
            await add_gallery_alias_cmd.finish("……别名没存上。稍后再试。")

    await add_gallery_alias_cmd.finish(f"行。【{gallery.name}】以后也可以叫【{alias}】。")


@delete_gallery_alias_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    params = re.sub(r"^删除图库别名\s*", "", event.get_plaintext().strip(), count=1).split()
    if len(params) != 2:
        await delete_gallery_alias_cmd.finish("格式是“删除图库别名 图库名 别名”。")
    gallery_name, alias = params

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await delete_gallery_alias_cmd.finish("……注册表读坏了，先检查 gallery_registry.json。")
        gallery = _find_gallery_exact(gallery_name)
        if gallery is None:
            await delete_gallery_alias_cmd.finish(f"没有【{gallery_name}】这个图库。")

        aliases = GALLERY_ALIASES.get(gallery.storage_key)
        if not aliases:
            await delete_gallery_alias_cmd.finish(f"【{gallery.name}】没有登记这个自定义别名。")
        alias_index = next(
            (
                index
                for index, registered_alias in enumerate(aliases)
                if _normalize_gallery_name(registered_alias) == _normalize_gallery_name(alias)
            ),
            None,
        )
        if alias_index is None:
            await delete_gallery_alias_cmd.finish(f"【{alias}】不是【{gallery.name}】的自定义别名。")

        removed_alias = aliases.pop(alias_index)
        if not aliases:
            GALLERY_ALIASES.pop(gallery.storage_key, None)
        try:
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            aliases.insert(alias_index, removed_alias)
            GALLERY_ALIASES[gallery.storage_key] = aliases
            logger.error(f"删除图库别名失败: {exc}")
            await delete_gallery_alias_cmd.finish("……别名没删掉。稍后再试。")

    await delete_gallery_alias_cmd.finish(f"行。【{gallery.name}】不再叫【{removed_alias}】了。")


@link_child_gallery_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    params = re.sub(r"^关联子图库\s*", "", event.get_plaintext().strip(), count=1).split()
    if len(params) != 2:
        await link_child_gallery_cmd.finish("格式是“关联子图库 子图库 父图库”。")
    child_name, parent_name = params

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await link_child_gallery_cmd.finish("……注册表读坏了，先检查 gallery_registry.json。")
        child = _find_gallery_exact(child_name)
        parent = _find_gallery_exact(parent_name)
        if child is None:
            await link_child_gallery_cmd.finish(f"没有【{child_name}】这个图库。")
        if parent is None:
            await link_child_gallery_cmd.finish(f"没有【{parent_name}】这个图库。")
        if not child.custom:
            await link_child_gallery_cmd.finish("只有自定义图库能作为子图库。")
        if child.storage_key == parent.storage_key or _would_create_gallery_cycle(
            child.storage_key,
            parent.storage_key,
            GALLERY_PARENTS,
        ):
            await link_child_gallery_cmd.finish("这样会形成循环关系，不能关联。")

        previous_parent = GALLERY_PARENTS.get(child.storage_key)
        GALLERY_PARENTS[child.storage_key] = parent.storage_key
        try:
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            if previous_parent is None:
                GALLERY_PARENTS.pop(child.storage_key, None)
            else:
                GALLERY_PARENTS[child.storage_key] = previous_parent
            logger.error(f"关联子图库失败: {exc}")
            await link_child_gallery_cmd.finish("……关联没存上。稍后再试。")

    await link_child_gallery_cmd.finish(f"已把【{child.name}】关联为【{parent.name}】的子图库。")


@unlink_child_gallery_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    child_name = re.sub(r"^取消子图库关联\s*", "", event.get_plaintext().strip(), count=1).strip()
    if not child_name:
        await unlink_child_gallery_cmd.finish("格式是“取消子图库关联 子图库”。")

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await unlink_child_gallery_cmd.finish("……注册表读坏了，先检查 gallery_registry.json。")
        child = _find_gallery_exact(child_name)
        if child is None:
            await unlink_child_gallery_cmd.finish(f"没有【{child_name}】这个图库。")
        previous_parent = GALLERY_PARENTS.pop(child.storage_key, None)
        if previous_parent is None:
            await unlink_child_gallery_cmd.finish(f"【{child.name}】目前没有关联父图库。")
        try:
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            GALLERY_PARENTS[child.storage_key] = previous_parent
            logger.error(f"取消子图库关联失败: {exc}")
            await unlink_child_gallery_cmd.finish("……取消关联没存上。稍后再试。")

    await unlink_child_gallery_cmd.finish(f"已取消【{child.name}】的子图库关联。")


@delete_gallery_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = re.sub(r"^删除图库\s*", "", event.get_plaintext().strip(), count=1).strip()
    if not name:
        await delete_gallery_cmd.finish("图库名呢？格式是“删除图库XX”。")

    async with GALLERY_CREATE_LOCK:
        if GALLERY_REGISTRY_LOAD_ERROR:
            await delete_gallery_cmd.finish("……注册表读坏了，没法删除。先检查 gallery_registry.json。")

        gallery = _find_custom_gallery_exact(name)
        if gallery is None:
            fixed = _find_gallery_exact(name)
            if fixed is not None:
                await delete_gallery_cmd.finish(f"【{fixed.name}】是固定图库，删不掉。")
            await delete_gallery_cmd.finish(f"没有【{name}】这个图库。")

        child_keys = [child.storage_key for child in _get_direct_child_galleries(gallery.storage_key)]
        own_parent = GALLERY_PARENTS.get(gallery.storage_key)
        aliases = GALLERY_ALIASES.get(gallery.storage_key)

        CUSTOM_GALLERIES.remove(gallery)
        GALLERY_PARENTS.pop(gallery.storage_key, None)
        for child_key in child_keys:
            GALLERY_PARENTS.pop(child_key, None)
        GALLERY_ALIASES.pop(gallery.storage_key, None)

        try:
            _save_custom_gallery_registry(_get_gallery_registry_path(), CUSTOM_GALLERIES)
        except Exception as exc:
            CUSTOM_GALLERIES.append(gallery)
            if own_parent is not None:
                GALLERY_PARENTS[gallery.storage_key] = own_parent
            for child_key in child_keys:
                GALLERY_PARENTS[child_key] = gallery.storage_key
            if aliases is not None:
                GALLERY_ALIASES[gallery.storage_key] = aliases
            logger.error(f"删除图库失败: {exc}")
            await delete_gallery_cmd.finish("……没删成。稍后再试。")

        save_dir = IMAGE_BASE_PATH / gallery.storage_key
        if save_dir.exists():
            try:
                shutil.rmtree(save_dir)
            except OSError as exc:
                logger.error(f"删除图库目录 {save_dir} 失败: {exc}")
                await delete_gallery_cmd.finish(
                    f"已从图库清单删除【{gallery.name}】，但目录删除失败，需手动清理 {save_dir}。"
                )

    await delete_gallery_cmd.finish(f"……删掉了。【{gallery.name}】图库，子图库关联也一并解除了。")


def get_random_local_image(category: str) -> Path | None:
    """从图库及其全部子图库中随机返回一张有效图片。"""
    images = get_random_local_images(category, 1)
    return images[0] if images else None


def get_random_local_images(category: str, count: int) -> list[Path]:
    """从图库及其全部子图库中随机返回指定数量的有效图片。"""
    folders = [IMAGE_BASE_PATH / storage_key for storage_key in _gallery_storage_keys_for_read(category)]
    if not folders[0].exists():
        try:
            folders[0].mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"📁 创建图库目录 {category} 失败: {e}")
    images = [
        image
        for folder in folders
        if folder.exists()
        for pattern in ("*.jpg", "*.png", "*.gif", "*.jpeg")
        for image in folder.glob(pattern)
    ]
    valid_images = [img for img in images if img.stat().st_size > 0]
    if not valid_images or count <= 0:
        return []
    return random.sample(valid_images, k=min(count, len(valid_images)))

# --- 1. 手动存图 ---
save_img_cmd = on_message(priority=6, block=False)
@save_img_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    if not isinstance(event, GroupMessageEvent):
        return
    group_id = event.group_id
    if group_id not in GROUP_IMAGE_PERMISSIONS:
        return

    text = event.get_plaintext().strip()
    if not re.fullmatch(r"存\s*\S+", text):
        return

    replies_db = REACTIONS_DB.get("save_img_replies", {})
    category, save_msg = _resolve_save_category_and_reply(text, replies_db)
    if not category:
        return
    gallery = _get_gallery(category)
    if gallery is None or not _gallery_allowed(group_id, gallery):
        return

    img_url = _extract_image_url(event.message)
    if not img_url and event.reply and event.reply.message:
        img_url = _extract_image_url(event.reply.message)
    if not img_url:
        return

    result = _gallery_sleep_block("sleep_save_img", silent_chance=0.8,
                                  fallback="……明天再存……zzZ")
    if result is None:
        return
    if result:
        _grant_gallery_safety_pass(5)
        await bot.send(event=event, message=result)
        return

    try:
        save_dir = IMAGE_BASE_PATH / category
        save_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{int(time.time())}_{random.randint(100, 999)}.jpg"
        async with aiohttp.ClientSession() as session, session.get(img_url) as resp:
            if resp.status != 200:
                return
            image_data = await resp.read()
        if not image_data:
            return
        saved = await GALLERY_HASH_INDEX.save_unique(save_dir, file_name, image_data)
        _grant_gallery_safety_pass(5)
        await bot.send(event=event, message=save_msg if saved else DUPLICATE_IMAGE_REPLY)
    except Exception as e:
        logger.debug(f"💾 手动存图失败: {e}")

# --- 2. 自动进货模式 ---
COLLECTING_MODE = {}
collect_cmd = on_command("开始收图", aliases={"停止收图"}, priority=5, block=True)
@collect_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, user_id = getattr(event, 'group_id', None), event.get_user_id()
    if group_id:
        session_key = _build_collect_session_key(group_id, user_id)
        if group_id not in GROUP_IMAGE_PERMISSIONS: return
    else: session_key = _build_collect_session_key(group_id, user_id)

    text = event.get_plaintext()
    if text.strip().startswith("停止收图"):
        if session_key in COLLECTING_MODE:
            del COLLECTING_MODE[session_key]
            await collect_cmd.finish("（合上相册）……收工。刚才发的图都存好了。")
        else: await collect_cmd.finish("哦，行。")

    target = args.extract_plain_text().strip()
    category = _resolve_collect_category(target)
    gallery = _get_gallery(category)

    if gallery is None:
        await collect_cmd.finish("先把图库名说清楚。格式是“开始收图 图库名”。")

    if group_id and not _gallery_allowed(group_id, gallery):
        await collect_cmd.finish("（皱眉）……这是什么图。")
        return

    COLLECTING_MODE[session_key] = category
    display_name = gallery.name if gallery else category
    await collect_cmd.finish(f"""（拿出手机准备好）……行，发吧。现在开始自动存【{display_name}】的图。\n（发完记得说"停止收图"）""")

auto_save_monitor = on_message(priority=7, block=False)
@auto_save_monitor.handle()
async def _(bot: Bot, event: Event):
    group_id, user_id = getattr(event, 'group_id', None), event.get_user_id()
    session_key = _build_collect_session_key(group_id, user_id)
    if session_key not in COLLECTING_MODE: return
    if re.fullmatch(r"存\s*\S+\s*", event.get_plaintext().strip()): return

    img_urls: list[str] = []
    try:
        img_urls = _extract_image_urls(event.get_message())
    except Exception as e:
        logger.debug(f"📥 提取图片 URL 失败: {e}")
    if not img_urls: return

    category = COLLECTING_MODE[session_key]
    downloaded_images: list[bytes] = []
    try:
        async with aiohttp.ClientSession() as session:
            for url in img_urls:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            if image_data:
                                downloaded_images.append(image_data)
                except Exception as e:
                    logger.debug(f"📥 自动存图下载失败: {e}")
    except Exception as e:
        logger.debug(f"📥 自动进货批次失败: {e}")
        return
    if not downloaded_images:
        return

    save_dir = IMAGE_BASE_PATH / category
    save_dir.mkdir(parents=True, exist_ok=True)
    saved_count = 0
    duplicate_count = 0
    try:
        for image_data in downloaded_images:
            file_name = f"{int(time.time())}_{random.randint(1000, 9999)}.jpg"
            if await GALLERY_HASH_INDEX.save_unique(save_dir, file_name, image_data):
                saved_count += 1
            else:
                duplicate_count += 1
    except Exception as e:
        logger.debug(f"📥 自动进货写入失败: {e}")
        return

    if duplicate_count and not saved_count:
        _grant_gallery_safety_pass(5)
        await bot.send(event=event, message=DUPLICATE_IMAGES_REPLY)
    elif duplicate_count:
        _grant_gallery_safety_pass(5)
        await bot.send(event=event, message=f"存了 {saved_count} 张，另外 {duplicate_count} 张之前就有了。")
    elif saved_count > 0 and random.random() < 0.3:
        _grant_gallery_safety_pass(5)
        await bot.send(event=event, message="👌")

# --- 3. 主动发图 ---
send_img_cmd = on_regex(r"^发张\s*\S+(?:\s+\S+)?\s*$", priority=5, block=True)
async def _send_gallery_forward_message(
    bot: Bot,
    event: GroupMessageEvent,
    image_segments: list[MessageSegment],
    caption: str,
) -> bool:
    call_api = getattr(bot, "call_api", None)
    if not callable(call_api):
        return False
    content = OB11Message(caption.strip() + "\n") if caption else OB11Message()
    for image_segment in image_segments:
        content += image_segment
    bot_uin = str(getattr(bot, "self_id", ""))
    node = {
        "type": "node",
        "data": {
            "name": "东云彰人",
            "uin": int(bot_uin) if bot_uin.isdigit() else bot_uin,
            "content": content,
        },
    }
    try:
        await call_api(
            "send_group_forward_msg",
            group_id=event.group_id,
            messages=[node],
        )
    except Exception as exc:
        logger.warning(f"图库多图合并转发失败，将回退为普通多图消息: {exc}")
        return False
    return True


@send_img_cmd.handle()
async def _(bot: Bot, event: Event):
    if not isinstance(event, GroupMessageEvent):
        return
    group_id = event.group_id
    if group_id not in GROUP_IMAGE_PERMISSIONS:
        return

    request_text = re.sub(r"^发张\s*", "", event.get_plaintext().strip(), count=1).strip()
    target, requested_count, count_valid = _parse_send_image_request(request_text)
    category, prompt_hint = _resolve_send_image_request(target)
    if not category:
        if not target or _has_gallery_name_prefix(target):
            return
        replies = REACTIONS_DB.get("unknown_gallery_replies") or DEFAULT_UNKNOWN_GALLERY_REPLIES
        _grant_gallery_safety_pass(5)
        await send_img_cmd.finish(random.choice(replies))
    gallery = _get_gallery(category)
    if gallery is None:
        return
    if not count_valid:
        return

    result = _gallery_sleep_block("sleep_replies_img", silent_chance=0.0, fallback="……zzZ")
    if result:
        _grant_gallery_safety_pass(5)
        await send_img_cmd.finish(result)

    mem = get_user_memory(get_memory_key(event))
    is_wl2_active = any(item.get("id") == "WL2" for item in mem.get("temp_implants", []))

    if is_wl2_active and category in ["toya", "vbs"]:
        _grant_gallery_safety_pass(5)
        await send_img_cmd.finish(random.choice([
            "……手机里没那种照片了。早就删了。",
            "（直接锁上手机屏幕）……没有可以给你看的东西。",
            "（瞥了一眼）……没有这种图可以发。"
        ]))

    if not _gallery_allowed(group_id, gallery):
        await send_img_cmd.finish("（瞥了一眼）……没有这种图可以发。" if category in ["toya", "self"] else "（摆手）……不想发这个。")

    if requested_count == 1:
        image_paths = [get_random_local_image(category)]
        image_paths = [path for path in image_paths if path is not None]
    else:
        image_paths = get_random_local_images(category, requested_count)
    if not image_paths:
        await send_img_cmd.finish(f"（翻了翻相册）……啧，相册里还没存【{gallery.name}】的照片。你先发给我几张？")

    try:
        if requested_count > 1 or not gallery.caption_enabled:
            caption = ""
        else:
            random_angles = REACTIONS_DB.get("send_img_angles") or ["语气切入点：随意的发言，像是随手丢过去的。"]
            current_angle = random.choice(random_angles)

            img_prompt = f"""
            {get_base_persona()}
            【当前动作】：你从手机相册里翻出{requested_count}张【{gallery.caption_subject}】发送给对方。
            【导演要求】：{prompt_hint}
            【随机微表情】：{current_angle}

            【强制约束】：
            1. 根据发图类型写配文，如果是发食物或自拍的话不需要强扯到冬弥身上！
            2. 只输出发图时附带的一句简短配文（20字以内），符合男高中生口吻。
            3. 纯文本，无引号，无动作描写。
            4. ⚠️不可视警告：你不知道图里具体是啥！绝对不能描写具体物体（如猫狗风景）！用万能代词模糊评价！
            5. 🚫【降重警告】：严禁使用"喏"、"给你"、"这张图"、"看看"等老套开头！每句话必须像第一次说一样自然，强迫自己使用全新、多变的句式！
            """
            caption = await call_deepseek_api([{"role": "user", "content": img_prompt}])
    except Exception as e:
        logger.error(f"配文生成失败: {e}")
        caption = "喏，你要的照片。"

    final_msg = None
    try:
        image_segments = []
        for image_path in image_paths:
            with open(image_path, "rb") as image_file:
                base64_url = f"base64://{base64.b64encode(image_file.read()).decode()}"
            image_segments.append(MessageSegment.image(base64_url))
        if len(image_segments) > 1 and await _send_gallery_forward_message(
            bot,
            event,
            image_segments,
            caption,
        ):
            _grant_gallery_safety_pass(5)
            return
        if caption:
            final_msg = OB11Message(caption.strip() + "\n")
            for image_segment in image_segments:
                final_msg += image_segment
        elif len(image_segments) == 1:
            final_msg = image_segments[0]
        else:
            final_msg = OB11Message()
            for image_segment in image_segments:
                final_msg += image_segment
    except Exception: await send_img_cmd.finish("（划手机）……啧，图片加载失败了。")

    if final_msg:
        _grant_gallery_safety_pass(5)
        await send_img_cmd.finish(final_msg)

# --- 4. 相册清单 ---
def get_file_list_safe(category: str) -> list[Path] | None:
    """返回图库及其全部子图库图片（按修改时间倒序）。"""
    folders = [IMAGE_BASE_PATH / storage_key for storage_key in _gallery_storage_keys_for_read(category)]
    if not any(folder.exists() for folder in folders): return None
    files = [
        image
        for folder in folders
        if folder.exists()
        for pattern in ("*.jpg", "*.png", "*.gif", "*.jpeg")
        for image in folder.glob(pattern)
    ]
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files

def get_thumbnail_safe(file_path: Path) -> str:
    """将图片压成 140px 缩略图并返回 base64 字符串；失败返回空串。"""
    try:
        with PILImage.open(file_path) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((140, 140))
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=50)
            return base64.b64encode(buffer.getvalue()).decode()
    except Exception: return ""

gallery_cmd = on_command("图库清单", priority=5, block=True)
@gallery_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    result = _gallery_sleep_block("sleep_gallery_list", silent_chance=0.0,
                                  fallback="💤 (小彰正在睡觉，请早上6点后再来...)")
    if result:
        await gallery_cmd.finish(result)
        return

    if isinstance(event, GroupMessageEvent):
        gid = event.group_id
        if gid not in GROUP_IMAGE_PERMISSIONS: return
    else: return

    params = args.extract_plain_text().strip().split()
    cat_raw = params[0] if len(params) > 0 else ""
    page = int(params[1]) if len(params) > 1 and params[1].isdigit() else 1

    target_cat = _resolve_gallery_category(cat_raw)
    if not target_cat: await gallery_cmd.finish("请指定分类！例如：图库清单 表情")
    gallery = _get_gallery(target_cat)
    if gallery is None: await gallery_cmd.finish("请指定分类！例如：图库清单 表情")
    if not _gallery_allowed(gid, gallery): await gallery_cmd.finish(f"🚫 本群没有查看【{gallery.name}】的权限。")

    all_files = get_file_list_safe(target_cat)
    if not all_files: await gallery_cmd.finish(f"📂 【{gallery.name}】相册是空的！")

    total_files = len(all_files)
    page, total_pages, start, end = _paginate_gallery(total_files, page, ITEMS_PER_PAGE)
    current_files = all_files[start:end]

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: "Microsoft YaHei", sans-serif; background-color: #f3f4f6; padding: 10px; }}
            .grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; }}
            .card {{ background: white; border-radius: 4px; padding: 4px; text-align: center; }}
            .img-box {{ width: 100%; height: 80px; background: #eee; display: flex; align-items: center; justify-content: center; overflow: hidden; }}
            img {{ width: 100%; height: 100%; object-fit: cover; }}
            .tag {{ background: #ff9f43; color: white; padding: 0 4px; border-radius: 4px; font-size: 10px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div style="text-align:center; margin-bottom:10px;">
            <b style="font-size:20px; color:#333;">📂 {gallery.name} ({page}/{total_pages})</b><br>
            <span style="color:#888; font-size:12px;">共 {total_files} 张</span>
        </div>
        <div class="grid">
    """
    for i, f in enumerate(current_files):
        idx = (page-1)*ITEMS_PER_PAGE + i + 1
        _thumb = get_thumbnail_safe(f)
        src = f"data:image/jpeg;base64,{_thumb}" if _thumb else ""
        html += f'<div class="card"><div class="img-box"><img src="{src}"></div><div class="tag">#{idx}</div></div>'
    html += "</div></body></html>"

    try:
        pic = await html_to_pic(html, viewport={"width": 800, "height": 100})
        _grant_gallery_safety_pass(5)
        await gallery_cmd.finish(MessageSegment.image(pic))
    except Exception as e:
        logger.error(f"渲染失败: {e}")
