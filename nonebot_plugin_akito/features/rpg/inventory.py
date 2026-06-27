"""背包与道具：查看背包、使用消耗品（效果接现有系统），并向打野提供掉落写入。

道具按**名称**入背包（`user["inventory"] = {道具名: 数量}`，已被 game_store 的 normalize 原样保留）。
消耗品效果集中在 `_apply_item_effect` 分发；纯逻辑（掉落/效果）拆出便于单测，指令 handler 只管加载/扣除/落库。
"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core import is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _save_data,
    _today_str,
)
from .config import _cfg, _error, _line
from .fortune import _roll_fortune
from .player import _ensure_player, _refill_stamina, _resolve_group, _stamina_max

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

def _roll_drops(monster: dict, rng=random) -> list[str]:
    """按野怪 drops 概率掷出掉落道具名列表（可空）。"""
    out: list[str] = []
    for d in monster.get("drops", []) or []:
        name = d.get("item")
        if name and rng.random() < float(d.get("chance", 0)):
            out.append(name)
    return out


# ==================== 道具效果分发 ====================

def _apply_item_effect(user: dict, item: dict, today: str, rng=random) -> tuple[bool, str]:
    """应用消耗品效果，返回 (是否消耗成功, 播报文案)。失败时不改动可消耗状态。"""
    eff = item.get("effect", {}) if isinstance(item.get("effect"), dict) else {}
    etype = eff.get("type")
    name = item.get("name", "")

    if etype == "stamina":
        _refill_stamina(user, today)
        mx = _stamina_max()
        cur = int(user.get("stamina", 0))
        if cur >= mx:
            return False, _error("stamina_full", name=name)
        user["stamina"] = min(mx, cur + int(eff.get("amount", 0)))
        return True, _line("use_stamina", name=name, amount=user["stamina"] - cur,
                           stamina=user["stamina"], max=mx)

    if etype == "exp_buff":
        user["exp_buff_uses"] = int(user.get("exp_buff_uses", 0)) + int(eff.get("uses", 1))
        user["exp_buff_mult"] = int(eff.get("mult", 2))
        return True, _line("use_exp_buff", name=name, mult=user["exp_buff_mult"])

    if etype == "reroll_fortune":
        if user.get("fortune_date") != today:
            return False, _error("need_signin")
        user["fortune"] = _roll_fortune(user, rng)  # 重掷隐藏运势（不外显结果）
        return True, _line("use_reroll_fortune", name=name)

    return False, _error("item_unknown", name=name)


# ==================== 指令：背包 ====================

bag_cmd = on_command("背包", aliases={"我的背包", "道具"}, priority=5, block=True)


@bag_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await bag_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, event.get_user_id(), _display_name(event))  # 只读展示，不落库
    inv = _inv(user)
    if not inv:
        await bag_cmd.finish(MessageSegment.reply(event.message_id) + _error("bag_empty"))

    lines = ["🎒 你的背包："]
    for name, cnt in inv.items():
        it = _item_by_name(name)
        desc = f"　{it.get('desc', '')}" if it else ""
        lines.append(f"· {name} ×{cnt}{desc}")
    lines.append("用法：使用 [道具名]")
    await bag_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：使用 ====================

use_cmd = on_command("使用", priority=5, block=True)


@use_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
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

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        if _item_count(user, name) <= 0:
            result = _error("item_none", name=name)
        else:
            ok, result = _apply_item_effect(user, item, today)
            if ok:
                _remove_item(user, name, 1)
                _save_data(data)
    await use_cmd.finish(MessageSegment.reply(event.message_id) + result)
