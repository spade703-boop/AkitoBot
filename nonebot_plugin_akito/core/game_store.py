"""共享玩家存储层：积分 / 亲密度 / 每日数据的统一读写，供 gift、rpg 等社交小游戏模块复用。

抽自 features/gift.py。玩家档案与社交关系按 QQ 全局共享，群记录只保留成员索引与群级 RPG 状态；
每日闸门仍按日期重置，读写保持原子化、文件优先 + 缺省兜底。
所有玩法模块读写**同一份 gift_data.json**、共用**同一把 LOCK**，避免多模块并发写产生竞态。

依赖方向：本模块属 core 基础层，只依赖 core 常量与 onebot 适配器；不反向依赖任何 features 模块。
签到这类「一个动作、多个玩法都想搭车」的场景，用 SIGNIN_HOOKS 注册表解耦（gift 触发，rpg 等订阅）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
import json
import os
import re

from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.log import logger

from . import ALLOWED_CHAT_GROUPS, TZ_CN
from .paths import find_data_path, get_data_dir

# 物理文件沿用 gift_data.json：现已是 gift + rpg 共享的玩家库，改名会让线上既有数据失联，故保持不变。
DATA_FILE = "gift_data.json"
SCHEMA_VERSION = 3
GLOBAL_PROFILE_SOURCE_GROUP = os.environ.get("GLOBAL_PROFILE_SOURCE_GROUP", "691188576").strip()

# 多模块共用一把锁，串行化对 DATA_FILE 的并发读写。
LOCK = asyncio.Lock()


# ==================== 每日键 ====================

def _today_str() -> str:
    return datetime.now(TZ_CN).date().isoformat()


# ==================== 数据骨架与归一化 ====================

def _new_data() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "users": {},
        "intimacy": {},
        "counts": {},
        "wedding_invitations": {},
        "groups": {},
    }


def _new_group() -> dict:
    return {
        "user_ids": [],
        "users": {},
        "intimacy": {},
        "counts": {},
        "wedding_invitations": {},
        "rpg": {},
    }


def _clean_users(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(uid): dict(rec) for uid, rec in value.items() if isinstance(rec, dict)}


def _clean_int_map(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): int(amount) for key, amount in value.items() if isinstance(amount, (int, float))}


def _clean_record_map(value: object) -> dict:
    if not isinstance(value, dict):
        return {}
    return {str(key): dict(rec) for key, rec in value.items() if isinstance(rec, dict)}


def _ordered_groups(groups: dict) -> list[tuple[object, dict]]:
    items = [(gid, group) for gid, group in groups.items() if isinstance(group, dict)]
    return sorted(items, key=lambda item: str(item[0]) != GLOBAL_PROFILE_SOURCE_GROUP)


def _attach_group_views(data: dict, group: dict) -> dict:
    global_users = data.setdefault("users", {})
    user_ids = [str(uid) for uid in group.get("user_ids", []) if str(uid)]
    local_users = _clean_users(group.get("users"))
    for uid, rec in local_users.items():
        if uid not in global_users:
            global_users[uid] = rec
        if uid not in user_ids:
            user_ids.append(uid)

    group["user_ids"] = user_ids
    group["_global_users"] = global_users
    group["users"] = {uid: global_users[uid] for uid in user_ids if uid in global_users}
    group["intimacy"] = data.setdefault("intimacy", {})
    group["counts"] = data.setdefault("counts", {})
    group["wedding_invitations"] = data.setdefault("wedding_invitations", {})
    group.setdefault("rpg", {})
    return group


def _normalize_data(raw: object) -> dict:
    """容错归一并迁移旧结构：玩家档案和社交关系提升为全局状态。"""
    data = _new_data()
    if not isinstance(raw, dict):
        return data
    groups = raw.get("groups") if isinstance(raw.get("groups"), dict) else {}
    ordered_groups = _ordered_groups(groups)

    data["users"] = _clean_users(raw.get("users"))
    data["intimacy"] = _clean_int_map(raw.get("intimacy"))
    data["counts"] = _clean_int_map(raw.get("counts"))
    data["wedding_invitations"] = _clean_record_map(raw.get("wedding_invitations"))

    for _gid, group in ordered_groups:
        for uid, rec in _clean_users(group.get("users")).items():
            if uid not in data["users"]:
                data["users"][uid] = rec
            else:
                for key, value in rec.items():
                    data["users"][uid].setdefault(key, value)
        for key, value in _clean_int_map(group.get("intimacy")).items():
            data["intimacy"].setdefault(key, value)
        for key, value in _clean_int_map(group.get("counts")).items():
            data["counts"].setdefault(key, value)
        for key, value in _clean_record_map(group.get("wedding_invitations")).items():
            data["wedding_invitations"].setdefault(key, value)

    for gid, group in groups.items():
        if not isinstance(group, dict):
            continue
        user_ids = [str(uid) for uid in group.get("user_ids", []) if str(uid)]
        for uid in _clean_users(group.get("users")):
            if uid not in user_ids:
                user_ids.append(uid)
        data["groups"][str(gid)] = {
            "user_ids": user_ids,
            "rpg": group.get("rpg") if isinstance(group.get("rpg"), dict) else {},
        }

    for group in data["groups"].values():
        _attach_group_views(data, group)
    return data


def _serializable_data(data: object) -> dict:
    normalized = _normalize_data(data)
    return {
        "schema_version": SCHEMA_VERSION,
        "users": normalized["users"],
        "intimacy": normalized["intimacy"],
        "counts": normalized["counts"],
        "wedding_invitations": normalized["wedding_invitations"],
        "groups": {
            str(gid): {
                "user_ids": list(group.get("user_ids", [])),
                "rpg": group.get("rpg", {}),
            }
            for gid, group in normalized["groups"].items()
        },
    }


def _load_data() -> dict:
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    if not path.exists():
        return _new_data()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.warning(f"读取 {DATA_FILE} 失败，已重置游戏数据")
        return _new_data()
    return _normalize_data(raw)


def _save_data(data: dict) -> None:
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_serializable_data(data), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ==================== 群 / 用户访问器 ====================

def _get_group(data: dict, group_id) -> dict:
    groups = data.setdefault("groups", {})
    group = groups.get(str(group_id))
    if not isinstance(group, dict):
        group = {"user_ids": [], "rpg": {}}
        groups[str(group_id)] = group
    return _attach_group_views(data, group)


def get_user(group: dict, user_id, display_name: str = "") -> dict:
    """取/建用户记录，仅保证**通用字段**（points / display_name）。

    各玩法模块在此基础上自行 setdefault 专属字段（gift 的偷窃字段、rpg 的经验/精力等），
    互不污染。返回的就是存在 group["users"][uid] 里的同一个 dict，可直接原地改。
    """
    global_users = group.get("_global_users")
    users = global_users if isinstance(global_users, dict) else group.setdefault("users", {})
    user = users.get(str(user_id))
    if not isinstance(user, dict):
        user = {}
        users[str(user_id)] = user
    if isinstance(global_users, dict):
        group.setdefault("users", {})[str(user_id)] = user
        user_ids = group.setdefault("user_ids", [])
        if str(user_id) not in user_ids:
            user_ids.append(str(user_id))
    user.setdefault("points", 0)
    user.setdefault("display_name", "")
    if display_name:
        user["display_name"] = display_name
    return user


def _add_points(group: dict, user_id, amount: int) -> int:
    user = get_user(group, user_id)
    user["points"] = int(user.get("points", 0)) + int(amount)
    return user["points"]


# ==================== 亲密度（无方向 pair 累积） ====================

def _pair_key(uid1, uid2) -> str:
    return "|||".join(sorted([str(uid1), str(uid2)]))


def _add_intimacy(group: dict, uid1, uid2, amount: int) -> int:
    intimacy = group.setdefault("intimacy", {})
    key = _pair_key(uid1, uid2)
    intimacy[key] = int(intimacy.get(key, 0)) + int(amount)
    return intimacy[key]


def _get_intimacy(group: dict, uid1, uid2) -> int:
    return int(group.get("intimacy", {}).get(_pair_key(uid1, uid2), 0))


# ==================== 加权随机 ====================

def _weighted_choice(weights: dict, rng) -> str:
    keys = list(weights.keys())
    values = [max(0, weights[k]) for k in keys]
    if not keys or sum(values) <= 0:
        return keys[0] if keys else ""
    return rng.choices(keys, weights=values, k=1)[0]


# ==================== 消息组装：@ 渲染 ====================

_PLACEHOLDER_RE = re.compile(r"(\{[a-z_]+\})")
_AT_KEYS = {"a", "b"}


def _render_with_ats(template: str, ctx: dict):
    """把模板渲染成消息：{a}{b} → 真 @，其余占位符 → 文本；未提供的占位符原样保留。"""
    rendered = None
    for part in _PLACEHOLDER_RE.split(template):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            key = part[1:-1]
            value = ctx.get(key)
            if key in _AT_KEYS and value is not None:
                seg = MessageSegment.at(value)
            elif value is None:
                seg = part
            else:
                seg = str(value)
        else:
            seg = part
        rendered = seg if rendered is None else rendered + seg
    return rendered if rendered is not None else ""


# ==================== 群上下文 / @ 解析 / 昵称 ====================

def resolve_group_id(event) -> tuple[str | None, bool]:
    """返回 (group_id, is_private)。私聊 → (None, True)；非白名单群 → (None, False) 静默；命中 → (str(gid), False)。

    各模块据此映射成自己的提示文案（私聊给提示，非白名单静默忽略）。
    """
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None, True
    if int(group_id) not in ALLOWED_CHAT_GROUPS:
        return None, False
    return str(group_id), False


def _display_name(event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        name = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return f"用户{event.get_user_id()}"


def _first_at_qq(original_message) -> str | None:
    """取消息里第一个 @ 的 QQ（含 'all'）；没有 @ 返回 None。"""
    for seg in original_message or []:
        if getattr(seg, "type", None) == "at":
            qq = str(seg.data.get("qq"))
            if qq:
                return qq
    return None


# ==================== 签到钩子注册表（解耦：gift 触发，rpg 等订阅） ====================

SIGNIN_HOOKS: list = []


def register_signin_hook(fn) -> None:
    """注册签到附加钩子：fn(group, user_id, rng) -> str（返回追加播报行，空串忽略）。

    钩子在签到结算（持锁、group 已加载）期间被调用，须为纯内存操作：
    只读写传入的 group/user dict，**不要**再次加锁或读写文件（会与签到主流程争锁/重复 IO）。
    """
    if fn not in SIGNIN_HOOKS:
        SIGNIN_HOOKS.append(fn)


def run_signin_hooks(group: dict, user_id: str, rng) -> list[str]:
    """运行所有已注册签到钩子，收集非空播报行。单个钩子异常被吞掉，不影响签到主流程。"""
    lines: list[str] = []
    for fn in list(SIGNIN_HOOKS):
        try:
            line = fn(group, user_id, rng)
        except Exception as e:
            logger.warning(f"签到钩子 {getattr(fn, '__name__', fn)} 执行失败：{e}")
            continue
        if line:
            lines.append(str(line))
    return lines
