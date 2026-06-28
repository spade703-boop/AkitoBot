"""查询/展示指令：我的角色（等级 + 称号 + 战绩 + 今日装备状态 + 积分 + 背包）、排行榜（等级榜）、冒险帮助。

战力为隐藏值，对外不显示数字。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment

from ...core import ALLOWED_CHAT_GROUPS, is_sleeping
from ...core.game_store import _display_name, _get_group, _load_data, _today_str
from .config import _error, _line
from .player import (
    _ensure_player,
    _equip_status,
    _level_of,
    _level_progress,
    _resolve_group,
    _title_of,
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

    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, event.get_user_id(), _display_name(event))

    prog = _level_progress(user.get("exp", 0))
    title = _title_of(prog["level"])
    bag = sum(int(v) for v in (user.get("inventory") or {}).values())
    wins, total = int(user.get("hunt_wins", 0)), int(user.get("hunt_total", 0))
    lines = [
        f"🗡️ 角色面板 · {_display_name(event)}",
        f"· 等级：Lv{prog['level']} {title}（经验 {prog['into']}/{prog['span']}）",
        f"· 战绩：{wins} 胜 / 共 {total} 场",
        f"· 今日装备：{_equip_status(user, today)}",
        f"· 积分：{int(user.get('points', 0))}",
        f"· 背包：{bag} 件道具",
    ]
    await status_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：排行榜（等级榜，纯文字、不 @、不出图） ====================

rank_cmd = on_command("排行榜", aliases={"等级榜", "冒险排行"}, priority=5, block=True)


@rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    data = _load_data()
    group = _get_group(data, group_id)
    users = group.get("users", {})
    # 只收已开始冒险（exp>0）的人，按经验降序 Top 10；纯查询、不落库
    ranked = sorted(
        ((uid, rec) for uid, rec in users.items() if isinstance(rec, dict) and int(rec.get("exp", 0)) > 0),
        key=lambda kv: int(kv[1].get("exp", 0)),
        reverse=True,
    )[:10]
    if not ranked:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + _error("rank_empty"))

    lines = [_line("rank_title")]
    for idx, (uid, rec) in enumerate(ranked, 1):
        lvl = _level_of(rec.get("exp", 0))
        name = rec.get("display_name") or f"用户{uid}"
        wins = int(rec.get("hunt_wins", 0))
        lines.append(f"{idx}. {name}　Lv{lvl} {_title_of(lvl)}　胜{wins}场")
    await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


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
        "· 我的角色 / 状态 — 查看等级、称号、战绩与今日装备\n"
        "· 排行榜 / 等级榜 — 看本群冒险者等级排行\n"
        "· 背包 / 使用 [道具] — 查看与使用道具\n"
        "\n"
        "💡 每天就两件事：签到领装备、选择打不打怪。连续签到额外有奖～"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
