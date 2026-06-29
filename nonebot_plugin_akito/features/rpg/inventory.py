"""背包与道具（精简版）：查看背包、使用消耗品（经验/礼物券），并向打怪提供掉落写入。

道具按名称入背包（`user["inventory"] = {道具名: 数量}`，已被 game_store 的 normalize 原样保留）。
消耗品三种：双倍经验卡（下次打怪经验×2）、经验书（即得经验）、礼物券（使用后触发完整送礼流程）。
纯逻辑（掉落/效果）拆出便于单测。
"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _first_at_qq,
    _get_group,
    _load_data,
    _save_data,
)
from ..gift import (
    _build_broadcast,
    _bump_count,
    _is_special_gift,
    _pick_gift_by_name,
    _roll_main_event,
    _roll_mishap,
    _roll_return_gift,
    _settle,
)
from .config import _cfg, _error, _line
from .player import _ensure_player, _resolve_group

# ==================== 道具定义 ====================

def _items() -> list[dict]:
    items = _cfg("items", [])
    return items if isinstance(items, list) else []


def _item_by_name(name: str) -> dict | None:
    for it in _items():
        if it.get("name") == name:
            return it
    return None


# ==================== 背包存取（作用于传入的 user dict） ====================

def _inv(user: dict) -> dict:
    inv = user.get("inventory")
    if not isinstance(inv, dict):
        inv = {}
        user["inventory"] = inv
    return inv


def _item_count(user: dict, name: str) -> int:
    return int(_inv(user).get(name, 0))


def _add_item(user: dict, name: str, n: int = 1) -> int:
    inv = _inv(user)
    inv[name] = int(inv.get(name, 0)) + int(n)
    return inv[name]


def _remove_item(user: dict, name: str, n: int = 1) -> bool:
    """扣除 n 个道具；不足返回 False（不改动）。扣空则移除该 key。"""
    inv = _inv(user)
    have = int(inv.get(name, 0))
    if have < n:
        return False
    rest = have - n
    if rest > 0:
        inv[name] = rest
    else:
        inv.pop(name, None)
    return True


# ==================== 掉落（纯函数，便于单测） ====================

def _roll_drops(monster: dict, rng=random, mult: float = 1.0) -> list[str]:
    """按野怪 drops 概率（× mult，受胜负/运势影响）掷出掉落道具名列表（可空）。"""
    out: list[str] = []
    for d in monster.get("drops", []) or []:
        name = d.get("item")
        if name and rng.random() < float(d.get("chance", 0)) * float(mult):
            out.append(name)
    return out


# ==================== 道具效果分发 ====================

def _apply_item_effect(user: dict, item: dict) -> tuple[bool, str]:
    """应用消耗品效果，返回 (是否消耗成功, 文案)。"""
    eff = item.get("effect", {}) if isinstance(item.get("effect"), dict) else {}
    etype = eff.get("type")
    name = item.get("name", "")
    if etype == "exp_buff":
        user["exp_buff_uses"] = int(user.get("exp_buff_uses", 0)) + int(eff.get("uses", 1))
        user["exp_buff_mult"] = int(eff.get("mult", 2))
        return True, _line("use_exp_buff", name=name, mult=user["exp_buff_mult"])
    if etype == "exp_grant":
        amount = int(eff.get("amount", 0))
        user["exp"] = int(user.get("exp", 0)) + amount
        return True, _line("use_exp_grant", name=name, amount=amount)
    return False, _error("item_unknown", name=name)


# ==================== 指令：背包 ====================

bag_cmd = on_command("我的背包", priority=5, block=True)


@bag_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await bag_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        await bag_cmd.finish(
            MessageSegment.reply(event.message_id) + "格式是「我的背包」，不用带其他字。"
        )

    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, event.get_user_id(), _display_name(event))  # 只读展示，不落库
    inv = _inv(user)
    if not inv:
        await bag_cmd.finish(MessageSegment.reply(event.message_id) + _error("bag_empty"))

    lines = ["🎒 你包里现在有："]
    for name, cnt in inv.items():
        it = _item_by_name(name)
        desc = f"　{it.get('desc', '')}" if it else ""
        lines.append(f"· {name} ×{cnt}{desc}")
    lines.append("要用就发：使用 [道具名]")
    await bag_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：使用 ====================

use_cmd = on_command("使用", priority=5, block=True)


def _is_gift_item(item: dict) -> bool:
    return item.get("effect", {}).get("type") == "gift"


@use_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await use_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if is_sleeping():
        await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    parts = args.extract_plain_text().strip().split()
    if not parts:
        await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("use_need_name"))
    name = parts[0]
    item = _item_by_name(name)
    if not item:
        await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("item_unknown", name=name))

    # 礼物券分支：需要 @ 目标，走完整送礼结算
    if _is_gift_item(item):
        target = _first_at_qq(getattr(event, "original_message", None))
        if not target or target == "all":
            await use_cmd.finish(
                MessageSegment.reply(event.message_id) + "使用礼物券要 @ 对方，比如：使用 彰冬无料券 @某人。"
            )
        if target == event.get_user_id():
            await use_cmd.finish(
                MessageSegment.reply(event.message_id) + "礼物券送给自己就没意思了，@ 个群友吧。"
            )
        if target == str(getattr(bot, "self_id", "")):
            await use_cmd.finish(MessageSegment.reply(event.message_id) + "小彰不收礼物券，去 @ 个群友吧。")

        gift_name = item.get("effect", {}).get("gift_name", "")
        gift = _pick_gift_by_name(gift_name)
        if not gift:
            await use_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("item_unknown", name=name)
            )

        sender_id = event.get_user_id()
        async with LOCK:
            data = _load_data()
            group = _get_group(data, group_id)
            user = _ensure_player(group, sender_id, _display_name(event))
            if _item_count(user, name) <= 0:
                await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("item_none", name=name))
            _ensure_player(group, target)  # 确保目标入册
            _remove_item(user, name, 1)

            if _is_special_gift(gift):
                main_event, mishap, return_key = "special", None, None
            else:
                main_event = _roll_main_event()
                mishap = _roll_mishap() if main_event == "mishap" else None
                return_key = _roll_return_gift() if main_event == "return" else None

            out = _settle(group, sender_id, target, gift, main_event, mishap, return_key)
            _bump_count(group, sender_id, target)
            _save_data(data)

        msg = _build_broadcast(out, sender_id, target)
        await use_cmd.finish(MessageSegment.reply(event.message_id) + msg)

    # 经验向道具：走原有效果分发
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        if _item_count(user, name) <= 0:
            result = _error("item_none", name=name)
        else:
            ok, result = _apply_item_effect(user, item)
            if ok:
                _remove_item(user, name, 1)
                _save_data(data)
    await use_cmd.finish(MessageSegment.reply(event.message_id) + result)
