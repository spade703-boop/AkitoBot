"""卡面知识库的超管维护指令。"""

from __future__ import annotations

import re
import time

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg

from ...core import AKITO_STATUS, SUPERUSER_QQ, grant_safety_pass
from .catalog import (
    bind_card_alias,
    bind_card_group_alias,
    unbind_card_alias,
    unbind_card_group_alias,
)


def _stamp_trigger(event: Event) -> None:
    """保持管理指令与其他管理指令一致的安全通行证行为。"""
    user_id = str(event.get_user_id())
    group_id = str(getattr(event, "group_id", "private"))
    AKITO_STATUS["last_trigger_user"] = user_id
    grant_safety_pass(5)
    if user_id == SUPERUSER_QQ:
        AKITO_STATUS.setdefault("last_superuser_trigger_time", {})[group_id] = time.time()


def _parse_card_alias_binding(raw_text: str) -> tuple[str, str] | None:
    """解析“别称 目标”参数，目标为最后一个空白分隔字段。"""
    parts = raw_text.rsplit(maxsplit=1)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def _split_alias_note(raw_text: str) -> tuple[str, str]:
    """用半角/全角竖线分隔绑定参数与可选俗称由来。"""
    binding, separator, note = raw_text.replace("｜", "|", 1).partition("|")
    return binding.strip(), note.strip() if separator else ""


def _parse_card_group_binding(raw_text: str) -> tuple[str, list[str]] | None:
    """解析“别称 目标1,目标2...”卡组绑定参数。"""
    parsed = _parse_card_alias_binding(raw_text)
    if parsed is None:
        return None
    alias, raw_targets = parsed
    targets = [target.strip() for target in re.split(r"[,，、]", raw_targets) if target.strip()]
    return (alias, targets) if len(targets) >= 2 else None


bind_card_alias_cmd = on_command("绑定卡面别称", priority=5, block=True)


@bind_card_alias_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    binding, note = _split_alias_note(args.extract_plain_text().strip())
    parsed = _parse_card_alias_binding(binding)
    _stamp_trigger(event)
    if parsed is None:
        await bind_card_alias_cmd.finish("用法：绑定卡面别称 <别称> <卡ID或彰N/冬N/杏N/心羽N> [| 俗称由来]")
    ok, message = bind_card_alias(*parsed, note=note)
    await bind_card_alias_cmd.finish(("✅ " if ok else "⚠️ ") + message)


unbind_card_alias_cmd = on_command("解绑卡面别称", priority=5, block=True)


@unbind_card_alias_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    alias = args.extract_plain_text().strip()
    _stamp_trigger(event)
    if not alias:
        await unbind_card_alias_cmd.finish("用法：解绑卡面别称 <别称>")
    ok, message = unbind_card_alias(alias)
    await unbind_card_alias_cmd.finish(("✅ " if ok else "⚠️ ") + message)


bind_card_group_alias_cmd = on_command("绑定卡组别称", priority=5, block=True)


@bind_card_group_alias_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    binding, note = _split_alias_note(args.extract_plain_text().strip())
    parsed = _parse_card_group_binding(binding)
    _stamp_trigger(event)
    if parsed is None:
        await bind_card_group_alias_cmd.finish(
            "用法：绑定卡组别称 <别称> <卡ID/角色序号,卡ID/角色序号...> [| 俗称由来]"
        )
    ok, message = bind_card_group_alias(*parsed, note=note)
    await bind_card_group_alias_cmd.finish(("✅ " if ok else "⚠️ ") + message)


unbind_card_group_alias_cmd = on_command("解绑卡组别称", priority=5, block=True)


@unbind_card_group_alias_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    alias = args.extract_plain_text().strip()
    _stamp_trigger(event)
    if not alias:
        await unbind_card_group_alias_cmd.finish("用法：解绑卡组别称 <别称>")
    ok, message = unbind_card_group_alias(alias)
    await unbind_card_group_alias_cmd.finish(("✅ " if ok else "⚠️ ") + message)
