"""送礼系统：彰冬同人圈主题的群友互送小游戏。

玩法闭环（完全自包含，不依赖其他模块）：
- `签到`：每天 1 次领取积分（赚取入口）。
- `送礼 @对方 [礼物名]`：每天 1 次，消耗积分，按权重抽随机事件（普通/暴击/回礼/失败/意外），
  累积两个群友之间的「亲密度（同好羁绊）」。
- `我的积分` / `礼物列表` / `亲密度` / `亲密度排行` 查询；`重置送礼`（超管）清空本群数据。

数据与套路对照 features/random_keyword.py：按群存储、每日按日期重置、原子读写、文件优先+缺省兜底配置。
"""

from __future__ import annotations

import asyncio
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
    load_json_file,
)

CONFIG_FILE = "gift_config.json"
DATA_FILE = "gift_data.json"
SCHEMA_VERSION = 1

# ==================== 默认配置（可被 data/content/gift_config.json 覆盖） ====================

DEFAULT_GIFT_CONFIG: dict = {
    # 礼物按「心意/稀有度」递增：买来的 → 自己产的。消耗积分与基础亲密度同步递增。
    "gifts": [
        {"name": "彰冬谷子", "cost": 30, "intimacy": 10},
        {"name": "彰冬豆豆眼", "cost": 60, "intimacy": 20},
        {"name": "彰冬无料", "cost": 120, "intimacy": 40},
        {"name": "彰冬同人本", "cost": 250, "intimacy": 80},
        {"name": "彰冬约稿点图", "cost": 450, "intimacy": 150},
        {"name": "自己产的彰冬饭", "cost": 800, "intimacy": 300},
    ],
    "return_gift": "彰冬谷子",  # 回礼自动回赠的礼物
    "sign_in": {"min": 30, "max": 80},
    "crit_multiplier": 2,
    "mishap_refund_ratio": 0.5,
    "mishap_damaged_bonus": 5,
    "mishap_stolen_bonus": 10,
    "mishap_allergy_bonus": 5,
    # 主事件权重
    "event_weights": {"normal": 60, "crit": 15, "return": 10, "fail": 10, "mishap": 5},
    # 意外子事件权重
    "mishap_weights": {"damaged": 1, "stolen": 1, "upgrade": 1, "allergy": 1},
    # 播报文案（同人女互动口吻，每类多条随机选用）。占位符：
    #   {a}{b}{c} → 真 @；{gift}{upgraded}{amount}{third_amount}{total}{cost}{refund}{name} → 文本
    "copy": {
        "sign_in": [
            "{a} 今天也来打卡啦～到账 {amount} 积分，攒着给彰冬囤谷！（当前 {total}）",
            "{a} 签到成功，+{amount} 积分。为爱发电也是要本钱的（当前 {total}）",
            "{a} 来报到～奖励 {amount} 积分，离下一本同人志又近了一步（当前 {total}）",
        ],
        "normal": [
            "{a} 给 {b} 投喂了一份【{gift}】，同好羁绊 +{amount}，你俩处成搭子了属于是～",
            "{a} 把【{gift}】塞给了 {b}，羁绊 +{amount}，又是一对因彰冬而结缘的姐妹",
            "{a} 给 {b} 递了份【{gift}】，+{amount} 羁绊，安插，下次一起开车（指写文）",
        ],
        "crit": [
            "{a} 甩出一份【{gift}】，{b} 当场磕到 awsl！羁绊暴击 ×2，+{amount}，这谁顶得住啊！",
            "{a} 的【{gift}】直击 {b} 的 XP 红心，上头了上头了，羁绊翻倍 +{amount}！",
            "{a} 一份【{gift}】正中 {b} 本命，狠狠心动，羁绊暴击 +{amount}！",
        ],
        "return": [
            "{a} 送了【{gift}】，{b} 太上头反手回赠一份【{return_gift}】，双向奔赴 +{amount}，好嗑！",
            "{a} 给 {b} 投喂【{gift}】，{b} 也不能白拿，回了份【{return_gift}】，礼尚往来 +{amount}",
        ],
        "fail": [
            "{a} 兴冲冲送上【{gift}】，结果 {b}：「这张……不是我本命角度。」积分照扣，羁绊没动，下次记好 XP（+0）",
            "{a} 的【{gift}】没能戳中 {b} 的点，尴尬收场，积分白花了，羁绊 +0",
            "{a} 送出【{gift}】，{b} 礼貌围笑了一下……没磕到，羁绊纹丝不动（+0）",
        ],
        "mishap_damaged": [
            "{a} 寄的【{gift}】路上被压坏了，俩人一起心疼半天反倒处出感情，各 +5，退你一半积分（{refund}）。",
            "{a} 的【{gift}】运输翻车……{b} 陪着一起骂快递，患难见真情，羁绊各 +5，返还 {refund} 积分。",
        ],
        "mishap_stolen": [
            "{a} 的【{gift}】半路被 {c} 截胡了！{c} 白嫖一波好感 +{third_amount}，同担相爱相杀（不是）",
            "{a} 给 {b} 的【{gift}】被路过的 {c} 顺走了，{c} 凭空磕到 +{third_amount}，离谱但很合理",
        ],
        "mishap_stolen_nobody": [
            "{a} 的【{gift}】半路不翼而飞……可惜群里没别人接盘，这份好感凭空蒸发了，羁绊 +0。",
        ],
        "mishap_upgrade": [
            "{a} 送的【{gift}】拆开竟是隐藏款，直接升级成【{upgraded}】！血赚，羁绊按高的 +{amount}！",
            "{a} 的【{gift}】开出了隐藏奖励——【{upgraded}】到手，{b} 狂喜，羁绊 +{amount}！",
        ],
        "mishap_allergy": [
            "{b} 一看【{gift}】这个 XP 有点雷到了……{a} 连忙道歉，羁绊 +5（慰问），退一半积分（{refund}）。",
            "{b} 对【{gift}】过敏了（雷点踩中），{a} 急忙赔不是，羁绊 +5，返还 {refund} 积分。",
        ],
    },
    # 边界/错误提示（纯文本，可含 {cost}{total}{name}）
    "errors": {
        "private_only": "送礼系统只在群里玩哦～",
        "already_signed": "今天已经签到过啦，明天再来攒积分～",
        "already_gifted": "今天的礼已经送过啦，明天再来～",
        "need_target": "要 @一位群友 并写上礼物名才行～比如：送礼 @某人 彰冬谷子",
        "need_gift": "想送啥礼物呀？写上礼物名，看看「礼物列表」吧～",
        "self_target": "给自己送礼就没意思啦，去 @个搭子吧～",
        "bot_target": "这个不能送给我啦～留着送给你的本命同好吧。",
        "gift_not_found": "没找到礼物【{name}】，瞅瞅「礼物列表」吧。",
        "insufficient": "积分不够哦，【{name}】需要 {cost}，你只有 {total}，先去签到攒攒～",
    },
}

GIFT_CONFIG: dict = load_json_file(CONFIG_FILE, DEFAULT_GIFT_CONFIG)


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
    GIFT_CONFIG.update(load_json_file(CONFIG_FILE, DEFAULT_GIFT_CONFIG))
    logger.info("🔄 送礼配置已热重载")


# ==================== 礼物档位 ====================

def _gift_list() -> list[dict]:
    gifts = _cfg("gifts", [])
    return gifts if isinstance(gifts, list) else []


def _find_gift(name: str) -> dict | None:
    """精确匹配礼物名；找不到时退化为唯一子串匹配。"""
    name = (name or "").strip()
    if not name:
        return None
    for gift in _gift_list():
        if gift.get("name") == name:
            return gift
    matches = [gift for gift in _gift_list() if name in gift.get("name", "")]
    return matches[0] if len(matches) == 1 else None


def _next_gift(gift: dict) -> dict | None:
    """返回高一档礼物；已是最高档则返回 None。"""
    gifts = _gift_list()
    for idx, cur in enumerate(gifts):
        if cur.get("name") == gift.get("name"):
            return gifts[idx + 1] if idx + 1 < len(gifts) else None
    return None


# ==================== 数据持久化 ====================

def _today_str() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def _new_data() -> dict:
    return {"schema_version": SCHEMA_VERSION, "groups": {}}


def _new_group() -> dict:
    return {"users": {}, "intimacy": {}}


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
            data["groups"][str(gid)] = {
                "users": {str(uid): rec for uid, rec in users.items() if isinstance(rec, dict)},
                "intimacy": {str(k): int(v) for k, v in intimacy.items() if isinstance(v, (int, float))},
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


# ==================== 随机事件 ====================

def _weighted_choice(weights: dict, rng) -> str:
    keys = list(weights.keys())
    values = [max(0, weights[k]) for k in keys]
    if not keys or sum(values) <= 0:
        return keys[0] if keys else ""
    return rng.choices(keys, weights=values, k=1)[0]


def _roll_main_event(rng=random) -> str:
    return _weighted_choice(_cfg("event_weights", {}), rng)


def _roll_mishap(rng=random) -> str:
    return _weighted_choice(_cfg("mishap_weights", {}), rng)


def _pick_third_party(group: dict, exclude: set, rng=random) -> str | None:
    candidates = [uid for uid in group.get("users", {}) if uid not in exclude]
    return rng.choice(candidates) if candidates else None


def _settle(group: dict, sender_id: str, target_id: str, gift: dict, main_event: str,
            mishap: str | None, third_party_id: str | None) -> dict:
    """按已抽定的事件结算（直接改 group：亲密度/积分），返回播报所需数据。

    本函数不含随机、不做 IO，便于单测。调用方需先扣除礼物消耗积分。
    """
    base = int(gift.get("intimacy", 0))
    cost = int(gift.get("cost", 0))
    ratio = float(_cfg("mishap_refund_ratio", 0.5))
    out: dict = {
        "event": main_event,
        "mishap": mishap if main_event == "mishap" else None,
        "gift": gift.get("name", ""),
        "amount": base,
        "refund": 0,
        "upgraded": None,
        "third_party": None,
        "third_amount": 0,
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
    elif main_event == "mishap":
        if mishap == "damaged":
            out["amount"] = int(_cfg("mishap_damaged_bonus", 5))
            _add_intimacy(group, sender_id, target_id, out["amount"])
            out["refund"] = int(cost * ratio)
            _add_points(group, sender_id, out["refund"])
        elif mishap == "stolen":
            out["amount"] = 0
            if third_party_id:
                out["third_party"] = third_party_id
                out["third_amount"] = int(_cfg("mishap_stolen_bonus", 10))
                _add_intimacy(group, sender_id, third_party_id, out["third_amount"])
        elif mishap == "upgrade":
            upgraded = _next_gift(gift) or gift
            out["upgraded"] = upgraded.get("name", "")
            out["amount"] = int(upgraded.get("intimacy", base))
            _add_intimacy(group, sender_id, target_id, out["amount"])
        elif mishap == "allergy":
            out["amount"] = int(_cfg("mishap_allergy_bonus", 5))
            _add_intimacy(group, sender_id, target_id, out["amount"])
            out["refund"] = int(cost * ratio)
            _add_points(group, sender_id, out["refund"])

    return out


# ==================== 消息组装 ====================

_PLACEHOLDER_RE = re.compile(r"(\{[a-z_]+\})")
_AT_KEYS = {"a", "b", "c"}


def _render_with_ats(template: str, ctx: dict):
    """把模板渲染成消息：{a}{b}{c} → 真 @，其余占位符 → 文本。"""
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
    if out["event"] != "mishap":
        return out["event"]
    if out["mishap"] == "stolen" and not out.get("third_party"):
        return "mishap_stolen_nobody"
    return f"mishap_{out['mishap']}"


def _build_broadcast(out: dict, sender_id: str, target_id: str, rng=random):
    template = rng.choice(_copy(_outcome_copy_key(out)))
    ctx = {
        "a": sender_id,
        "b": target_id,
        "c": out.get("third_party"),
        "gift": out.get("gift", ""),
        "upgraded": out.get("upgraded") or out.get("gift", ""),
        "amount": out.get("amount", 0),
        "third_amount": out.get("third_amount", 0),
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
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _get_user(group, user_id, _display_name(event))

        if user.get("last_sign_in") == today:
            await sign_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("already_signed")
            )

        sign_cfg = _cfg("sign_in", {})
        amount = random.randint(int(sign_cfg.get("min", 30)), int(sign_cfg.get("max", 80)))
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
    target_qq = _first_at_qq(getattr(event, "original_message", None))
    gift_name = (args.extract_plain_text() if args else "").strip()

    # 参数校验
    if not target_qq:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_target"))
    if target_qq == "all":
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("self_target"))
    if target_qq == sender_id:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("self_target"))
    if target_qq == str(getattr(bot, "self_id", "")):
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("bot_target"))
    if not gift_name:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_gift"))

    gift = _find_gift(gift_name)
    if not gift:
        await gift_cmd.finish(
            MessageSegment.reply(event.message_id) + _error("gift_not_found", name=gift_name)
        )

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        sender = _get_user(group, sender_id, _display_name(event))
        _get_user(group, target_qq)  # 确保被送者入册（用于排行/亲密度查询）

        if sender.get("last_gift") == today:
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("already_gifted")
            )

        cost = int(gift.get("cost", 0))
        if int(sender.get("points", 0)) < cost:
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id)
                + _error("insufficient", name=gift["name"], cost=cost, total=int(sender.get("points", 0)))
            )

        # 先扣消耗，再按事件结算（部分意外会返还）
        sender["points"] = int(sender["points"]) - cost
        sender["last_gift"] = today

        main_event = _roll_main_event()
        mishap = _roll_mishap() if main_event == "mishap" else None
        third_party = None
        if mishap == "stolen":
            third_party = _pick_third_party(group, {sender_id, target_qq})

        out = _settle(group, sender_id, target_qq, gift, main_event, mishap, third_party)
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
    lines.append("用法：送礼 @某人 礼物名")
    await list_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：亲密度 ====================

intimacy_cmd = on_command("亲密度", priority=5, block=True)


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
        value = _get_intimacy(group, user_id, target_qq)
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id)
            + MessageSegment.at(user_id) + " 和 " + MessageSegment.at(target_qq)
            + f" 的同好羁绊：{value}"
        )

    # 不带 @：列出自己羁绊最高的几位
    partners = _top_partners(group, user_id, limit=5)
    if not partners:
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id) + "你还没有和谁建立羁绊呢，快去送礼吧～"
        )
    lines = ["你的同好羁绊 Top："]
    for other_id, value in partners:
        lines.append(f"· {_name_of(group, other_id)}：{value}")
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

rank_cmd = on_command("亲密度排行", priority=5, block=True)


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
        lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}")
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
