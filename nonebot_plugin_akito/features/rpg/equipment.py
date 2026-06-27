"""装备：穿戴 / 卸下，已穿戴装备给战力加成。

装备就是 `items` 里 kind="equipment" 的条目（含 slot/power），与消耗品共用同一份背包；
穿戴态单独存在 `user["equipped"] = {部位: 装备名}`。穿戴 = 从背包移到 equipped，换下的旧件退回背包。
战力加成由 player._combat_power 统一汇总（读 config power），打野/未来 PK 自动吃到。
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.params import CommandArg

from ...core.game_store import LOCK, _display_name, _get_group, _load_data, _save_data
from .config import _error, _line
from .inventory import _add_item, _item_by_name, _item_count, _remove_item
from .player import _combat_power, _ensure_player, _resolve_group

# ==================== 纯逻辑：穿戴 / 卸下（作用于传入的 user dict） ====================

def _is_equipment(item: dict | None) -> bool:
    return bool(item) and item.get("kind") == "equipment"


def _equipped(user: dict) -> dict:
    eq = user.get("equipped")
    if not isinstance(eq, dict):
        eq = {}
        user["equipped"] = eq
    return eq


def _equip(user: dict, name: str) -> tuple[bool, str]:
    """穿戴背包里的装备到对应槽位；占用槽位的旧装备退回背包。返回 (是否成功, 文案)。"""
    item = _item_by_name(name)
    if not _is_equipment(item):
        return False, _error("not_equipment", name=name)
    if _item_count(user, name) <= 0:
        return False, _error("item_none", name=name)
    slot = item.get("slot", "")
    eq = _equipped(user)
    old = eq.get(slot)
    _remove_item(user, name, 1)
    if old:
        _add_item(user, old, 1)  # 同槽旧装备退回背包
    eq[slot] = name
    return True, _line("equip_ok", name=name, slot=slot, power=int(item.get("power", 0)), cp=_combat_power(user))


def _unequip(user: dict, key: str) -> tuple[bool, str]:
    """按部位名或装备名卸下，装备退回背包。返回 (是否成功, 文案)。"""
    eq = _equipped(user)
    slot = key if key in eq else next((s for s, n in eq.items() if n == key), None)
    if not slot or slot not in eq:
        return False, _error("not_equipped", name=key)
    name = eq.pop(slot)
    _add_item(user, name, 1)
    return True, _line("unequip_ok", name=name, slot=slot, cp=_combat_power(user))


# ==================== 指令：装备 / 卸下 ====================

equip_cmd = on_command("装备", aliases={"穿戴"}, priority=5, block=True)


@equip_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await equip_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    parts = args.extract_plain_text().strip().split()
    if not parts:
        await equip_cmd.finish(MessageSegment.reply(event.message_id) + _error("equip_need_name"))

    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        ok, result = _equip(user, parts[0])
        if ok:
            _save_data(data)
    await equip_cmd.finish(MessageSegment.reply(event.message_id) + result)


unequip_cmd = on_command("卸下", aliases={"卸载"}, priority=5, block=True)


@unequip_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await unequip_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    parts = args.extract_plain_text().strip().split()
    if not parts:
        await unequip_cmd.finish(MessageSegment.reply(event.message_id) + _error("unequip_need_name"))

    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))
        ok, result = _unequip(user, parts[0])
        if ok:
            _save_data(data)
    await unequip_cmd.finish(MessageSegment.reply(event.message_id) + result)
