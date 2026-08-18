"""背包与道具：查看背包、使用消耗品/战备，并向打怪提供掉落写入。

道具按名称入背包（`user["inventory"] = {道具名: 数量}`，已被 game_store 的 normalize 原样保留）。
战备需要主动使用；常规战备互斥，神官护符使用独立保护位，可与常规战备同时生效。
纯逻辑（掉落/效果）拆出便于单测。
"""

from __future__ import annotations

from collections.abc import Mapping
import random
from typing import Any, cast

from nonebot import on_command
from nonebot.adapters import Bot, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from ...core import is_sleeping
from ...core.game_store import (
    LOCK,
    _display_name,
    _first_at_qq,
    _get_group,
    _load_data,
    _render_with_ats,
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
from .config import _cfg, _copy, _error, _line
from .player import _ensure_player, _resolve_group
from .types import ActiveBattleView, RpgUserRecord

# ==================== 道具定义 ====================

def _items() -> list[dict]:
    items = _cfg("items", [])
    return items if isinstance(items, list) else []


def _item_by_name(name: str) -> dict | None:
    for it in _items():
        if it.get("name") == name:
            return it
    return None


def _battle_effect(item: dict | None) -> dict:
    effect = item.get("effect", {}) if isinstance(item, dict) else {}
    return effect if isinstance(effect, dict) else {}


def _active_battle_supply(user: RpgUserRecord, *, guard: bool = False) -> ActiveBattleView | None:
    """返回当前已启用且仍有效的战备快照；配置删项时自动视为失效。"""
    key = "active_battle_guard" if guard else "active_battle_supply"
    record = user.get(key)
    if not isinstance(record, dict):
        return None
    name = str(record.get("name", ""))
    uses = int(record.get("uses", 0))
    if not name or uses <= 0:
        return None
    item = _item_by_name(name)
    effect = _battle_effect(item)
    expected_type = "battle_guard" if guard else "battle_supply"
    if effect.get("type") != expected_type:
        return None
    return {"name": name, "uses": uses, "effect": dict(effect)}


def _activate_battle_supply(user: RpgUserRecord, item: dict) -> tuple[bool, str]:
    """主动装备一件战备，返回是否成功及提示文案。"""
    name = str(item.get("name", ""))
    effect = _battle_effect(item)
    etype = effect.get("type")
    if etype not in {"battle_supply", "battle_guard"}:
        return False, _error("item_unknown", name=name)
    is_guard = etype == "battle_guard"
    slot = "active_battle_guard" if is_guard else "active_battle_supply"
    active = _active_battle_supply(user, guard=is_guard)
    if active:
        error_key = "supply_guard_busy" if is_guard else "supply_slot_busy"
        return False, _error(error_key, name=active["name"])
    user_values = cast(dict[str, Any], user)
    user_values[slot] = {"name": name, "uses": max(1, int(effect.get("uses", 1)))}
    line_key = "use_battle_guard" if is_guard else "use_battle_supply"
    parts = " / ".join(_battle_supply_parts({"effect": effect}))
    return True, _line(line_key, name=name, uses=user_values[slot]["uses"], parts=parts)


def _consume_battle_supply(user: RpgUserRecord, *, guard: bool = False) -> int:
    """消耗一次已启用战备并返回剩余次数。"""
    key = "active_battle_guard" if guard else "active_battle_supply"
    active = _active_battle_supply(user, guard=guard)
    if not active:
        cast(dict[str, Any], user).pop(key, None)
        return 0
    rest = active["uses"] - 1
    if rest > 0:
        cast(dict[str, Any], user)[key] = {"name": active["name"], "uses": rest}
    else:
        cast(dict[str, Any], user).pop(key, None)
    return rest


def _active_battle_debuff(user: RpgUserRecord) -> ActiveBattleView | None:
    """返回下一场普通挑战会生效的减益；配置删项时自动视为失效。"""
    record = user.get("active_battle_debuff")
    if not isinstance(record, dict):
        return None
    name = str(record.get("name", ""))
    uses = int(record.get("uses", 0))
    if not name or uses <= 0:
        return None
    item = _item_by_name(name)
    effect = _battle_effect(item)
    if effect.get("type") != "battle_debuff_gift":
        return None
    return {"name": name, "uses": uses, "effect": dict(effect)}


def _queue_battle_debuff(user: RpgUserRecord, item: dict) -> int:
    """将赠送道具的减益按场次排队，同名道具不会叠加在同一场。"""
    name = str(item.get("name", ""))
    effect = _battle_effect(item)
    uses = max(1, int(effect.get("uses", 1)))
    active = _active_battle_debuff(user)
    if active and active["name"] == name:
        uses += int(active["uses"])
    user["active_battle_debuff"] = {"name": name, "uses": uses}
    return uses


def _consume_battle_debuff(user: RpgUserRecord) -> int:
    """消耗一场普通挑战减益并返回剩余场数。"""
    active = _active_battle_debuff(user)
    if not active:
        user.pop("active_battle_debuff", None)
        return 0
    rest = active["uses"] - 1
    if rest > 0:
        user["active_battle_debuff"] = {"name": active["name"], "uses": rest}
    else:
        user.pop("active_battle_debuff", None)
    return rest


def _battle_supply_parts(active: Mapping[str, Any] | None) -> list[str]:
    if not active:
        return []
    effect = active.get("effect", {})
    parts: list[str] = []
    if effect.get("type") == "battle_guard":
        parts.append("挑战转为成功")
        rescue_exp_mult = float(effect.get("rescue_exp_mult", 1.0))
        if rescue_exp_mult != 1.0:
            parts.append(f"护符持有者经验 +{int(round((rescue_exp_mult - 1.0) * 100))}%")
    if effect.get("full_forge"):
        parts.append("装备视为强化满")
    power_mult = float(effect.get("power_mult", 1.0))
    if power_mult != 1.0:
        parts.append(f"战力 +{int(round((power_mult - 1.0) * 100))}%")
    exp_mult = float(effect.get("exp_mult", 1.0))
    if exp_mult != 1.0:
        if exp_mult.is_integer():
            parts.append(f"经验 ×{int(exp_mult)}")
        else:
            parts.append(f"经验 +{int(round((exp_mult - 1.0) * 100))}%")
    drop_mult = float(effect.get("drop_mult", 1.0))
    if drop_mult != 1.0:
        parts.append(f"掉落率 ×{drop_mult:g}")
    return parts


# ==================== 背包存取（作用于传入的 user dict） ====================

def _inv(user: RpgUserRecord) -> dict[str, int]:
    inv = user.get("inventory")
    if not isinstance(inv, dict):
        inv = {}
        user["inventory"] = inv
    return inv


def _item_count(user: RpgUserRecord, name: str) -> int:
    return int(_inv(user).get(name, 0))


def _add_item(user: RpgUserRecord, name: str, n: int = 1) -> int:
    inv = _inv(user)
    inv[name] = int(inv.get(name, 0)) + int(n)
    return inv[name]


def _remove_item(user: RpgUserRecord, name: str, n: int = 1) -> bool:
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

def _apply_item_effect(user: RpgUserRecord, item: dict) -> tuple[bool, str]:
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
    if etype in {"battle_supply", "battle_guard"}:
        return _activate_battle_supply(user, item)
    return False, _error("item_unknown", name=name)


# ==================== 指令：背包 ====================

bag_cmd = on_command("我的背包", priority=5, block=True)


@bag_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await bag_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

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


def _is_battle_debuff_gift(item: dict) -> bool:
    return item.get("effect", {}).get("type") == "battle_debuff_gift"


@use_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent, args: Message = CommandArg()):
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
    if not item:
        return

    if _is_battle_debuff_gift(item):
        target = _first_at_qq(getattr(event, "original_message", None))
        if not target or target == "all" or target == event.get_user_id():
            return
        if target == str(getattr(bot, "self_id", "")):
            await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("debuff_gift_bot"))

        sender_id = event.get_user_id()
        async with LOCK:
            data = _load_data()
            group = _get_group(data, group_id)
            sender = _ensure_player(group, sender_id, _display_name(event))
            if _item_count(sender, name) <= 0:
                await use_cmd.finish(MessageSegment.reply(event.message_id) + _error("item_none", name=name))
            recipient = _ensure_player(group, target)
            _remove_item(sender, name, 1)
            queued_uses = _queue_battle_debuff(recipient, item)
            _save_data(data)

        template = random.choice(_copy("gift_battle_debuff"))
        msg = _render_with_ats(
            template,
            {"a": sender_id, "b": target, "name": name, "uses": queued_uses},
        )
        await use_cmd.finish(MessageSegment.reply(event.message_id) + msg)

    # 礼物券分支：需要 @ 目标，走完整送礼结算
    if _is_gift_item(item):
        target = _first_at_qq(getattr(event, "original_message", None))
        if not target or target == "all":
            return
        if target == event.get_user_id():
            return
        if target == str(getattr(bot, "self_id", "")):
            await use_cmd.finish(MessageSegment.reply(event.message_id) + "小彰不收礼物券，去 @ 个群友吧。")

        gift_name = item.get("effect", {}).get("gift_name", "")
        gift = _pick_gift_by_name(gift_name)
        if not gift:
            await use_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("item_unknown", name=name)
            )
        if not gift:
            return

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
