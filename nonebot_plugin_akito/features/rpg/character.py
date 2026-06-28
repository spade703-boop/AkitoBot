"""角色面板与帮助（精简版）：对外只显示等级 + 今日装备状态 + 积分 + 背包（战力为隐藏值，不外显）。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from ...core import ALLOWED_CHAT_GROUPS, is_sleeping
from ...core.game_store import _display_name, _get_group, _load_data, _today_str
from .config import _error
from .player import _ensure_player, _equip_status, _level_progress, _resolve_group

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

    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, event.get_user_id(), _display_name(event))

    prog = _level_progress(user.get("exp", 0))
    bag = sum(int(v) for v in (user.get("inventory") or {}).values())
    lines = [
        f"🗡️ 角色面板 · {_display_name(event)}",
        f"· 等级：Lv{prog['level']}（经验 {prog['into']}/{prog['span']}）",
        f"· 今日装备：{_equip_status(user, today)}",
        f"· 积分：{int(user.get('points', 0))}",
        f"· 背包：{bag} 件道具",
    ]
    await status_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


help_cmd = on_command("冒险帮助", aliases={"打怪帮助", "冒险说明"}, priority=5, block=True)


@help_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    msg = (
        "🗺️ 冒险系统\n"
        "━━━━━━━━━━━━━━\n"
        "· 签到 — 领积分、经验和今日装备\n"
        "· 打怪 / 挑战 — 用今日装备挑战野怪，赢取经验、积分与掉落（装备打完即损坏，每日一次）\n"
        "· 组队 @某人 — 拉群友合力打怪，羁绊越深越容易拉动；拉不动就自己上\n"
        "· 强化 — 花积分强化今日装备，提高胜率\n"
        "· 我的角色 / 状态 — 查看等级与今日装备\n"
        "· 背包 / 使用 [道具] — 查看与使用道具\n"
        "\n"
        "💡 每天就两件事：签到领装备、选择打不打怪。"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
