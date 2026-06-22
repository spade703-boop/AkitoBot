"""送礼系统：彰冬同人圈主题的群友互送小游戏。

玩法闭环（完全自包含，不依赖其他模块）：
- `签到`：每天 1 次领取积分（赚取入口）。
- `送礼 @对方`：每天 1 次，系统从「你当前积分买得起的礼物」里随机送一份给对方，按权重抽随机事件
  （普通/暴击/回礼/失败/意外），累积两个群友之间的「亲密度（同好羁绊）」。
  顶档「自己产的彰冬饭」一旦抽中，必定触发「惊喜升级」固定结算。
- `我的积分` / `礼物列表` / `亲密度` / `亲密度排行` 查询；`重置送礼`（超管）清空本群数据。

数据与套路对照 features/random_keyword.py：按群存储、每日按日期重置、原子读写、文件优先+缺省兜底配置。
"""

from __future__ import annotations

import asyncio
import copy
from datetime import datetime
import json
import os
import random
import re

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from ..core import (
    ALLOWED_CHAT_GROUPS,
    SUPERUSER_QQ,
    TZ_CN,
    find_data_path,
    get_data_dir,
    is_sleeping,
    load_json_file,
)

CONFIG_FILE = "gift_config.json"
DATA_FILE = "gift_data.json"
SCHEMA_VERSION = 1

# ==================== 默认配置（可被 data/content/gift_config.json 覆盖） ====================

DEFAULT_GIFT_CONFIG: dict = {
    # 礼物按「心意/稀有度」递增：买来的 → 自己产的。消耗积分与基础亲密度同步递增。
    "gifts": [
        {"name": "彰冬无料", "cost": 50, "intimacy": 12},
        {"name": "彰冬谷子", "cost": 100, "intimacy": 28},
        {"name": "彰冬豆豆眼", "cost": 200, "intimacy": 60},
        {"name": "彰冬同人本", "cost": 350, "intimacy": 115},
        {"name": "彰冬约稿点图", "cost": 550, "intimacy": 200},
        {"name": "自己产的彰冬饭", "cost": 800, "intimacy": 320},
    ],
    "return_gift": "彰冬谷子",  # 回礼自动回赠的礼物
    "special_gift": "自己产的彰冬饭",  # 抽中它必定触发「惊喜升级」固定结算
    "special_intimacy": 320,  # 彰冬饭固定结算的羁绊值（抽中必定惊喜升级）
    "sign_in": {"min": 50, "max": 100},
    "crit_multiplier": 2,
    # 主事件权重（意外 5→10，让花样有机会出现）
    "event_weights": {"normal": 55, "crit": 16, "return": 12, "fail": 7, "mishap": 10},
    # 意外子事件表：每项 羁绊加成 / 返还积分比例 / 抽取权重（可增删调，热重载）
    "mishaps": {
        "damaged":     {"intimacy": 8,  "refund_ratio": 0.5, "weight": 3},  # 快递翻车
        "freebie":     {"intimacy": 28, "refund_ratio": 0.0, "weight": 2},  # 商家加赠
        "rare":        {"intimacy": 24, "refund_ratio": 0.0, "weight": 2},  # 买到稀有
        "handwritten": {"intimacy": 20, "refund_ratio": 0.0, "weight": 2},  # 附了手写卡
        "praised":     {"intimacy": 22, "refund_ratio": 0.0, "weight": 2},  # 被同好夸甜
        "overboard":   {"intimacy": 30, "refund_ratio": 0.0, "weight": 1},  # 一时上头加码
        "delayed":     {"intimacy": 12, "refund_ratio": 0.0, "weight": 2},  # 慢递迟到
        "dupe":        {"intimacy": 6,  "refund_ratio": 0.3, "weight": 2},  # 撞款了
        "lost":        {"intimacy": 0,  "refund_ratio": 1.0, "weight": 1},  # 寄丢了
    },
    # 羁绊等级：累计羁绊值 → 称号，门槛按礼物羁绊值校准（纯展示层，可热重载）
    "bond_levels": [
        {"min": 0, "name": "初识"},
        {"min": 100, "name": "相熟"},
        {"min": 400, "name": "要好"},
        {"min": 1000, "name": "挚友"},
        {"min": 2500, "name": "知己"},
        {"min": 6000, "name": "莫逆之交"},
    ],
    # 播报文案（平和同好口吻，每类多条随机选用）。占位符：
    #   {a}{b} → 真 @；{gift}{return_gift}{amount}{total}{cost}{refund}{name} → 文本
    "copy": {
        "sign_in": [
            "{a} 签到完成，今天领到 {amount} 积分，攒着慢慢送礼吧（当前 {total}）。",
            "{a} 来打卡了，+{amount} 积分到账（当前 {total}）。",
            "{a} 今天的签到积分 +{amount}，离心仪的礼物又近了点（当前 {total}）。",
        ],
        "normal": [
            "{a} 给 {b} 投喂了一份【{gift}】，同好羁绊 +{amount}。",
            "{a} 送了 {b} 一份【{gift}】，两个人的羁绊 +{amount}。",
            "{a} 把【{gift}】送给了 {b}，羁绊默默 +{amount}。",
        ],
        "crit": [
            "{a} 送的【{gift}】正好戳中 {b}，有点上头，羁绊翻倍 +{amount}。",
            "{a} 这份【{gift}】送到 {b} 心坎上了，羁绊翻倍 +{amount}。",
            "{a} 送的【{gift}】很对 {b} 的胃口，羁绊直接翻倍 +{amount}。",
        ],
        "return": [
            "{a} 送了【{gift}】，{b} 也回赠了一份【{return_gift}】，礼尚往来，羁绊 +{amount}。",
            "{a} 给 {b} 送了【{gift}】，{b} 顺手回了份【{return_gift}】，羁绊 +{amount}。",
        ],
        "fail": [
            "{a} 送了【{gift}】，{b} 没太感冒，羁绊原地踏步。",
            "{a} 送的【{gift}】好像没戳中 {b}，这次羁绊没怎么动。",
            "{a} 送出【{gift}】，{b} 反应平平，羁绊没变化。",
        ],
        "mishap_damaged": [
            "{a} 寄的【{gift}】路上有点压坏了，两个人一起心疼了一下，反而更亲近，羁绊各 +{amount}，返还 {refund} 积分。",
            "{a} 的【{gift}】运输途中磕了一下，{b} 陪着一起惋惜，羁绊各 +{amount}，退还 {refund} 积分。",
        ],
        "mishap_freebie": [
            "{a} 送 {b} 的【{gift}】，卖家居然多塞了份赠品小卡，意外加料，羁绊 +{amount}。",
            "{a} 给 {b} 的【{gift}】里附了点小周边，{b} 拆开惊喜了一下，羁绊 +{amount}。",
        ],
        "mishap_rare": [
            "{a} 送 {b} 的这份【{gift}】正好是早绝版的稀有款，可遇不可求，羁绊 +{amount}。",
            "{a} 给 {b} 淘来的【{gift}】是限定稀有版，运气爆棚，羁绊 +{amount}。",
        ],
        "mishap_handwritten": [
            "{a} 随【{gift}】夹了张手写小卡，{b} 看完心头一暖，羁绊 +{amount}。",
            "{a} 在【{gift}】里塞了句手写留言，{b} 反复看了好几遍，羁绊 +{amount}。",
        ],
        "mishap_praised": [
            "{a} 送 {b} 的【{gift}】被群里同好刷屏夸「这对好甜」，公开撒糖加成，羁绊 +{amount}。",
            "{a} 这份送给 {b} 的【{gift}】当众撒了把糖，同好们齐刷「磕到了」，羁绊 +{amount}。",
        ],
        "mishap_overboard": [
            "{a} 一时上头，给【{gift}】又默默加了码，{b} 哭笑不得地收下，羁绊 +{amount}。",
            "{a} 送【{gift}】送上了头，硬是多添了点，{b} 拗不过只好收下，羁绊 +{amount}。",
        ],
        "mishap_delayed": [
            "{a} 送 {b} 的【{gift}】物流慢了半拍，但心意照样送到，羁绊 +{amount}。",
            "{a} 的【{gift}】在路上磨蹭了几天才到，{b} 还是乐呵呵收下，羁绊 +{amount}。",
        ],
        "mishap_dupe": [
            "{a} 送的【{gift}】，{b} 居然早就有同款了，两个人笑作一团，羁绊 +{amount}，返还 {refund} 积分。",
            "{a} 送来【{gift}】，{b} 翻出自己那份同款，俩人乐了半天，羁绊 +{amount}，退还 {refund} 积分。",
        ],
        "mishap_lost": [
            "{a} 寄给 {b} 的【{gift}】半路寄丢了，心意还在，积分全额退回 {refund}。",
            "{a} 的【{gift}】在物流里弄丢了，{b} 说心意领了就好，积分如数退还 {refund}。",
        ],
        # 顶档「自己产的彰冬饭」专属固定文案
        "special": [
            "{a} 送的彰冬饭非常合 {b} 的胃口，羁绊 +{amount}。",
            "{a} 送的彰冬饭正好是 {b} 最喜欢的那个派生，羁绊 +{amount}。",
        ],
    },
    # 边界/错误提示（纯文本，可含 {cost}{total}{name}）
    "errors": {
        "private_only": "送礼系统在群里才能玩哦。",
        "sleeping": "💤 这会儿小彰睡着了，等 6 点天亮以后再来吧……",
        "already_gifted": "今天的礼已经送过了，明天再来吧。",
        "need_target": "送礼要 @一位群友 哦，系统会随机送出一份礼物。比如：送礼 @某人。",
        "self_target": "给自己送礼就没什么意思啦，去 @一个群友吧。",
        "bot_target": "小彰拒绝了你的礼物。",
        "insufficient": "积分还不太够，最便宜的【{name}】也要 {cost}，你现在有 {total}，先去签到攒一攒吧。",
    },
}

def _load_config() -> dict:
    """加载送礼配置；无文件 / 解析失败时回落到默认配置的深拷贝。

    关键：必须深拷贝，否则无文件时 GIFT_CONFIG 与 DEFAULT_GIFT_CONFIG 会是同一对象，
    reload 时的 clear() 会连默认配置一起清空。
    """
    loaded = load_json_file(CONFIG_FILE, None)
    return loaded if isinstance(loaded, dict) else copy.deepcopy(DEFAULT_GIFT_CONFIG)


GIFT_CONFIG: dict = _load_config()


def _cfg(key: str, default=None):
    """读配置项，缺失时回落到默认配置。"""
    if key in GIFT_CONFIG:
        return GIFT_CONFIG[key]
    return DEFAULT_GIFT_CONFIG.get(key, default)


def _copy(key: str) -> list[str]:
    table = _cfg("copy", {})
    if isinstance(table, dict) and table.get(key):
        return table[key]
    return DEFAULT_GIFT_CONFIG["copy"].get(key, [""])


def _error(key: str, **fmt) -> str:
    table = _cfg("errors", {})
    template = table.get(key) if isinstance(table, dict) else None
    if not template:
        template = DEFAULT_GIFT_CONFIG["errors"].get(key, "")
    try:
        return template.format(**fmt)
    except (KeyError, IndexError):
        return template


def reload_gift_config() -> None:
    """热重载礼物配置（原地 clear+update，保持已持有引用不失效）。"""
    GIFT_CONFIG.clear()
    GIFT_CONFIG.update(_load_config())
    logger.info("🔄 送礼配置已热重载")


# ==================== 礼物档位 ====================

def _gift_list() -> list[dict]:
    gifts = _cfg("gifts", [])
    return gifts if isinstance(gifts, list) else []


def _affordable_gifts(points: int) -> list[dict]:
    """返回当前积分买得起的礼物（cost ≤ points）。"""
    return [g for g in _gift_list() if int(g.get("cost", 0)) <= int(points)]


def _pick_gift(points: int, rng=random) -> dict | None:
    """从买得起的礼物里加权随机抽一份：越贵权重越大（按档位顺序 1..k）；都买不起返回 None。

    依赖 gifts 配置按 cost 升序排列（买得起的恰为前 k 档，权重即其档位序号）。
    """
    pool = _affordable_gifts(points)
    if not pool:
        return None
    weights = list(range(1, len(pool) + 1))
    return rng.choices(pool, weights=weights, k=1)[0]


def _cheapest_gift() -> dict | None:
    gifts = _gift_list()
    return min(gifts, key=lambda g: int(g.get("cost", 0))) if gifts else None


# ==================== 数据持久化 ====================

def _today_str() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def _new_data() -> dict:
    return {"schema_version": SCHEMA_VERSION, "groups": {}}


def _new_group() -> dict:
    return {"users": {}, "intimacy": {}, "counts": {}}


def _normalize_data(raw: object) -> dict:
    data = _new_data()
    if not isinstance(raw, dict):
        return data
    groups = raw.get("groups")
    if isinstance(groups, dict):
        for gid, group in groups.items():
            if not isinstance(group, dict):
                continue
            users = group.get("users") if isinstance(group.get("users"), dict) else {}
            intimacy = group.get("intimacy") if isinstance(group.get("intimacy"), dict) else {}
            counts = group.get("counts") if isinstance(group.get("counts"), dict) else {}
            data["groups"][str(gid)] = {
                "users": {str(uid): rec for uid, rec in users.items() if isinstance(rec, dict)},
                "intimacy": {str(k): int(v) for k, v in intimacy.items() if isinstance(v, (int, float))},
                "counts": {str(k): int(v) for k, v in counts.items() if isinstance(v, (int, float))},
            }
    return data


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
        logger.warning(f"读取 {DATA_FILE} 失败，已重置送礼数据")
        return _new_data()
    return _normalize_data(raw)


def _save_data(data: dict) -> None:
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_normalize_data(data), f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _get_group(data: dict, group_id) -> dict:
    groups = data.setdefault("groups", {})
    group = groups.get(str(group_id))
    if not isinstance(group, dict):
        group = _new_group()
        groups[str(group_id)] = group
    group.setdefault("users", {})
    group.setdefault("intimacy", {})
    group.setdefault("counts", {})
    return group


def _get_user(group: dict, user_id, display_name: str = "") -> dict:
    users = group.setdefault("users", {})
    user = users.get(str(user_id))
    if not isinstance(user, dict):
        user = {}
        users[str(user_id)] = user
    user.setdefault("points", 0)
    user.setdefault("last_sign_in", "")
    user.setdefault("last_gift", "")
    user.setdefault("display_name", "")
    if display_name:
        user["display_name"] = display_name
    return user


# ==================== 亲密度 ====================

def _pair_key(uid1, uid2) -> str:
    """无方向 pair key：两端 id 排序后用 '|||' 连接（对照 random_paro 的 pair_hits）。"""
    return "|||".join(sorted([str(uid1), str(uid2)]))


def _add_intimacy(group: dict, uid1, uid2, amount: int) -> int:
    intimacy = group.setdefault("intimacy", {})
    key = _pair_key(uid1, uid2)
    intimacy[key] = int(intimacy.get(key, 0)) + int(amount)
    return intimacy[key]


def _get_intimacy(group: dict, uid1, uid2) -> int:
    return int(group.get("intimacy", {}).get(_pair_key(uid1, uid2), 0))


def _add_points(group: dict, user_id, amount: int) -> int:
    user = _get_user(group, user_id)
    user["points"] = int(user.get("points", 0)) + int(amount)
    return user["points"]


# ==================== 羁绊等级 / 送礼次数 ====================

def _count_key(frm, to) -> str:
    """有向送礼次数 key：送礼方 → 收礼方。"""
    return f"{frm}>{to}"


def _bump_count(group: dict, frm, to) -> int:
    counts = group.setdefault("counts", {})
    key = _count_key(frm, to)
    counts[key] = int(counts.get(key, 0)) + 1
    return counts[key]


def _get_count(group: dict, frm, to) -> int:
    return int(group.get("counts", {}).get(_count_key(frm, to), 0))


def _bond_levels() -> list[dict]:
    levels = _cfg("bond_levels", [])
    return levels if isinstance(levels, list) and levels else DEFAULT_GIFT_CONFIG["bond_levels"]


def _bond_level(value: int) -> dict:
    """累计羁绊值 → 当前等级信息。

    返回 {idx, name, cur_min, next_name, next_min, to_next}；满级时 next_* 为 None、to_next 为 0。
    """
    levels = _bond_levels()
    value = int(value)
    idx = 0
    for i, lv in enumerate(levels):
        if value >= int(lv.get("min", 0)):
            idx = i
        else:
            break
    cur = levels[idx]
    nxt = levels[idx + 1] if idx + 1 < len(levels) else None
    return {
        "idx": idx,
        "name": str(cur.get("name", "")),
        "cur_min": int(cur.get("min", 0)),
        "next_name": str(nxt.get("name", "")) if nxt else None,
        "next_min": int(nxt.get("min", 0)) if nxt else None,
        "to_next": max(0, int(nxt.get("min", 0)) - value) if nxt else 0,
    }


def _bond_card(group: dict, me: str, other: str):
    """组装「我和 ta」的羁绊文字卡片（含真 @、等级、进度、分方向次数）。"""
    value = _get_intimacy(group, me, other)
    lv = _bond_level(value)
    sent = _get_count(group, me, other)
    recv = _get_count(group, other, me)

    lines = [f"· 等级：{lv['name']}（Lv{lv['idx'] + 1}）"]
    if lv["next_name"]:
        lines.append(f"· 羁绊值 {value}，距「{lv['next_name']}」还差 {lv['to_next']}")
    else:
        lines.append(f"· 羁绊值 {value}，已达顶级「{lv['name']}」(Lv{lv['idx'] + 1})")
    if sent or recv:
        lines.append(f"· 你送出 {sent} 次，ta 回送 {recv} 次（共 {sent + recv} 次往来）")
    else:
        lines.append("· 你们还没互送过礼，快去 送礼 @ta 吧～")

    head = MessageSegment.at(me) + " 和 " + MessageSegment.at(other) + " 的同好羁绊"
    return head + "\n" + "\n".join(lines)


# ==================== 随机事件 ====================

def _weighted_choice(weights: dict, rng) -> str:
    keys = list(weights.keys())
    values = [max(0, weights[k]) for k in keys]
    if not keys or sum(values) <= 0:
        return keys[0] if keys else ""
    return rng.choices(keys, weights=values, k=1)[0]


def _roll_main_event(rng=random) -> str:
    return _weighted_choice(_cfg("event_weights", {}), rng)


def _mishaps() -> dict:
    m = _cfg("mishaps", {})
    return m if isinstance(m, dict) and m else DEFAULT_GIFT_CONFIG["mishaps"]


def _mishap_spec(key: str) -> dict:
    return _mishaps().get(key) or DEFAULT_GIFT_CONFIG["mishaps"].get(key, {})


def _roll_mishap(rng=random) -> str:
    weights = {k: int(v.get("weight", 0)) for k, v in _mishaps().items()}
    return _weighted_choice(weights, rng)


def _is_special_gift(gift: dict) -> bool:
    return gift.get("name") == _cfg("special_gift", "自己产的彰冬饭")


def _settle(group: dict, sender_id: str, target_id: str, gift: dict,
            main_event: str, mishap: str | None) -> dict:
    """按已抽定的事件结算（直接改 group：亲密度/积分），返回播报所需数据。

    本函数不含随机、不做 IO，便于单测。调用方需先扣除礼物消耗积分。
    """
    base = int(gift.get("intimacy", 0))
    cost = int(gift.get("cost", 0))
    out: dict = {
        "event": main_event,
        "mishap": mishap if main_event == "mishap" else None,
        "gift": gift.get("name", ""),
        "amount": base,
        "refund": 0,
        "return_gift": None,
    }

    if main_event == "normal":
        _add_intimacy(group, sender_id, target_id, base)
    elif main_event == "crit":
        out["amount"] = base * int(_cfg("crit_multiplier", 2))
        _add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "return":
        out["return_gift"] = _cfg("return_gift", _gift_list()[0]["name"] if _gift_list() else "")
        _add_intimacy(group, sender_id, target_id, base)
    elif main_event == "fail":
        out["amount"] = 0
    elif main_event == "special":
        # 自己产的彰冬饭：必定惊喜升级，固定结算
        out["amount"] = int(_cfg("special_intimacy", base))
        _add_intimacy(group, sender_id, target_id, out["amount"])
    elif main_event == "mishap":
        # 意外：按 mishaps 配置表结算（羁绊加成 + 按比例返还积分）
        spec = _mishap_spec(mishap)
        out["amount"] = int(spec.get("intimacy", 0))
        if out["amount"]:
            _add_intimacy(group, sender_id, target_id, out["amount"])
        out["refund"] = int(cost * float(spec.get("refund_ratio", 0)))
        if out["refund"]:
            _add_points(group, sender_id, out["refund"])

    return out


# ==================== 消息组装 ====================

_PLACEHOLDER_RE = re.compile(r"(\{[a-z_]+\})")
_AT_KEYS = {"a", "b"}


def _render_with_ats(template: str, ctx: dict):
    """把模板渲染成消息：{a}{b} → 真 @，其余占位符 → 文本。"""
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
                seg = part  # 未提供的占位符原样保留
            else:
                seg = str(value)
        else:
            seg = part
        rendered = seg if rendered is None else rendered + seg
    return rendered if rendered is not None else ""


def _outcome_copy_key(out: dict) -> str:
    if out["event"] == "mishap":
        return f"mishap_{out['mishap']}"
    return out["event"]  # normal / crit / return / fail / special


def _build_broadcast(out: dict, sender_id: str, target_id: str, rng=random):
    template = rng.choice(_copy(_outcome_copy_key(out)))
    ctx = {
        "a": sender_id,
        "b": target_id,
        "gift": out.get("gift", ""),
        "amount": out.get("amount", 0),
        "refund": out.get("refund", 0),
        "return_gift": out.get("return_gift") or "",
    }
    return _render_with_ats(template, ctx)


# ==================== 通用校验 ====================

def _resolve_group(event: Event) -> tuple[str | None, str | None]:
    """返回 (group_id, 拒绝消息)。私聊给提示；非白名单群静默忽略。"""
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None, _error("private_only")
    if int(group_id) not in ALLOWED_CHAT_GROUPS:
        return None, None
    return str(group_id), None


def _display_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        name = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if isinstance(name, str) and name.strip():
            return name.strip()
    return f"用户{event.get_user_id()}"


def _first_at_qq(original_message) -> str | None:
    """取消息里第一个 @ 的 QQ（含 'all'）；没有 @ 返回 None。对照 impression.py:78-83。"""
    for seg in original_message or []:
        if getattr(seg, "type", None) == "at":
            qq = str(seg.data.get("qq"))
            if qq:
                return qq
    return None


_GIFT_LOCK = asyncio.Lock()


# ==================== 指令：签到 ====================

sign_cmd = on_command("签到", priority=5, block=True)


@sign_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ  # 超管不限次（测试用）
    if is_sleeping() and not is_superuser:  # 0–6 点睡眠拦截（超管除外）
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _get_user(group, user_id, _display_name(event))

        if not is_superuser and user.get("last_sign_in") == today:
            return  # 重复签到静默：群里另有签到 bot 应答，避免双重刷屏

        sign_cfg = _cfg("sign_in", {})
        amount = random.randint(int(sign_cfg.get("min", 50)), int(sign_cfg.get("max", 100)))
        user["points"] = int(user.get("points", 0)) + amount
        user["last_sign_in"] = today
        _save_data(data)

        template = random.choice(_copy("sign_in"))
        msg = _render_with_ats(template, {"a": user_id, "amount": amount, "total": user["points"]})
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：送礼 ====================

gift_cmd = on_command("送礼", priority=5, block=True)


@gift_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    sender_id = event.get_user_id()
    is_superuser = sender_id == SUPERUSER_QQ  # 超管不限次（测试用）
    if is_sleeping() and not is_superuser:  # 0–6 点睡眠拦截（超管除外）
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    # 参数校验：只需 @ 一位群友（不再输入礼物名）
    if not target_qq or target_qq == "all":
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_target"))
    if target_qq == sender_id:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("self_target"))
    if target_qq == str(getattr(bot, "self_id", "")):
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("bot_target"))

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        sender = _get_user(group, sender_id, _display_name(event))
        _get_user(group, target_qq)  # 确保被送者入册（用于排行/亲密度查询）

        if not is_superuser and sender.get("last_gift") == today:
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("already_gifted")
            )

        points = int(sender.get("points", 0))
        gift = _pick_gift(points)
        if gift is None:
            cheapest = _cheapest_gift() or {"name": "", "cost": 0}
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id)
                + _error("insufficient", name=cheapest.get("name", ""), cost=int(cheapest.get("cost", 0)), total=points)
            )

        # 先扣消耗，再按事件结算（部分意外会返还）
        sender["points"] = points - int(gift["cost"])
        sender["last_gift"] = today

        if _is_special_gift(gift):
            main_event, mishap = "special", None
        else:
            main_event = _roll_main_event()
            mishap = _roll_mishap() if main_event == "mishap" else None

        out = _settle(group, sender_id, target_qq, gift, main_event, mishap)
        _bump_count(group, sender_id, target_qq)  # 记一次有向送礼（无论事件结果）
        _save_data(data)

        broadcast = _build_broadcast(out, sender_id, target_qq)
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)


# ==================== 指令：我的积分 ====================

points_cmd = on_command("我的积分", aliases={"积分"}, priority=5, block=True)


@points_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await points_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _get_user(group, user_id, _display_name(event))

    can_sign = "可签到 ✅" if user.get("last_sign_in") != today else "今日已签到"
    can_gift = "可送礼 ✅" if user.get("last_gift") != today else "今日已送礼"
    msg = (
        f"你当前有 {int(user.get('points', 0))} 积分。\n"
        f"· {can_sign}\n· {can_gift}"
    )
    await points_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：礼物列表 ====================

list_cmd = on_command("礼物列表", aliases={"送礼帮助"}, priority=5, block=True)


@list_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    lines = ["🎁 彰冬礼物档位（稀有度递增）："]
    for gift in _gift_list():
        lines.append(f"· {gift['name']}　{gift['cost']} 积分　羁绊+{gift['intimacy']}")
    lines.append("用法：送礼 @某人 —— 系统会从你买得起的礼物里随机送一份（越贵的越容易抽中）。")
    await list_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：亲密度 ====================

intimacy_cmd = on_command("亲密度", aliases={"羁绊"}, priority=5, block=True)


@intimacy_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    data = _load_data()
    group = _get_group(data, group_id)
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    if target_qq and target_qq not in ("all", user_id):
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id) + _bond_card(group, user_id, target_qq)
        )

    # 不带 @：列出自己羁绊最高的几位
    partners = _top_partners(group, user_id, limit=5)
    if not partners:
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id) + "你还没有和谁建立羁绊呢，快去送礼吧～"
        )
    lines = ["你的同好羁绊 Top："]
    for other_id, value in partners:
        lines.append(f"· {_name_of(group, other_id)}：{value}（{_bond_level(value)['name']}）")
    await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


def _top_partners(group: dict, user_id: str, limit: int = 5) -> list[tuple[str, int]]:
    """返回 user_id 的羁绊伙伴 [(other_id, value), ...]，按羁绊降序。"""
    result: list[tuple[str, int]] = []
    for key, value in group.get("intimacy", {}).items():
        ids = key.split("|||")
        if user_id in ids:
            other = ids[0] if ids[1] == user_id else ids[1]
            result.append((other, int(value)))
    result.sort(key=lambda x: x[1], reverse=True)
    return result[:limit]


def _name_of(group: dict, user_id: str) -> str:
    user = group.get("users", {}).get(str(user_id))
    if isinstance(user, dict) and user.get("display_name"):
        return user["display_name"]
    return f"用户{user_id}"


# ==================== 指令：亲密度排行 ====================

rank_cmd = on_command("亲密度排行", aliases={"羁绊排行"}, priority=5, block=True)


@rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    data = _load_data()
    group = _get_group(data, group_id)
    pairs = sorted(group.get("intimacy", {}).items(), key=lambda kv: int(kv[1]), reverse=True)[:10]
    if not pairs:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + "本群还没有羁绊数据，快去送礼吧～")

    lines = ["💞 本群同好羁绊排行："]
    for idx, (key, value) in enumerate(pairs, 1):
        a, b = key.split("|||")
        lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}（{_bond_level(value)['name']}）")
    await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：重置送礼（超管） ====================

reset_cmd = on_command("重置送礼", priority=5, block=True)


@reset_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return
    data = _load_data()
    data.setdefault("groups", {})[str(group_id)] = _new_group()
    _save_data(data)
    await reset_cmd.finish(MessageSegment.reply(event.message_id) + "已清空本群的送礼/积分/羁绊数据。")
