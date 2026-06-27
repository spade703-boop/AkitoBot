"""角色面板与帮助：查看等级/经验/战力/精力/运势/积分（本期文字面板）。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from ...core import ALLOWED_CHAT_GROUPS, is_sleeping
from ...core.game_store import _display_name, _get_group, _load_data, _today_str
from .config import _error
from .fortune import _fortune_by_key
from .inventory import _item_by_name
from .player import (
    _combat_power,
    _ensure_player,
    _equip_slots,
    _level_progress,
    _refill_stamina,
    _resolve_group,
    _stamina_max,
)

status_cmd = on_command("我的角色", aliases={"角色", "状态", "角色面板"}, priority=5, block=True)


@status_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await status_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if is_sleeping():
        await status_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    user_id = event.get_user_id()
    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, user_id, _display_name(event))
    _refill_stamina(user, today)  # 只为展示算出当日精力，不落库

    prog = _level_progress(user.get("exp", 0))
    cp = _combat_power(user)
    fortune = _fortune_by_key(user.get("fortune", "")).get("name", "—") if user.get("fortune_date") == today else "未签到"

    equipped = user.get("equipped") or {}
    equip_parts = []
    for slot in _equip_slots():
        worn = equipped.get(slot)
        if worn:
            it = _item_by_name(worn)
            equip_parts.append(f"{slot} {worn}(+{int(it.get('power', 0)) if it else 0})")
        else:
            equip_parts.append(f"{slot} —")

    lines = [
        f"🗡️ 角色面板 · {_display_name(event)}",
        f"· 等级：Lv{prog['level']}（经验 {prog['into']}/{prog['span']}，距升级 {prog['to_next']}）",
        f"· 战力：{cp}",
        f"· 装备：{' / '.join(equip_parts)}",
        f"· 精力：{int(user.get('stamina', 0))}/{_stamina_max()}（每日 0 点回满）",
        f"· 今日运势：{fortune}",
        f"· 积分：{int(user.get('points', 0))}",
        f"· 背包：{sum(int(v) for v in (user.get('inventory') or {}).values())} 件道具",
    ]
    await status_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


help_cmd = on_command("冒险帮助", aliases={"打野帮助", "冒险说明"}, priority=5, block=True)


@help_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    msg = (
        "🗺️ 冒险系统\n"
        "━━━━━━━━━━━━━━\n"
        "· 签到 — 领积分与探索经验\n"
        "· 打野 / 打野怪 — 消耗精力挑战野怪，获取经验、积分与掉落\n"
        "· 我的角色 / 状态 — 查看等级、战力、精力、积分、背包\n"
        "· 运势 — 查看今日运势\n"
        "· 背包 — 查看道具；使用 [道具] — 使用消耗品\n"
        "· 商店 — 查看在售；购买 [道具] [数量] — 用积分购买\n"
        "· 装备 [装备] / 卸下 [部位] — 穿脱装备，加战力\n"
        "\n"
        "💡 精力每天 0 点回满；经验涨级提升战力，去挑战更强的野怪吧！"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
