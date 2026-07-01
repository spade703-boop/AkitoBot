"""查询/展示指令：我的角色（等级 + 称号 + 战绩 + 今日装备状态 + 积分 + 背包）、排行榜（等级榜）、冒险帮助。

战力为隐藏值，对外不显示数字。"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.params import CommandArg

from ...core import ALLOWED_CHAT_GROUPS, is_sleeping
from ...core.game_store import LOCK, _display_name, _get_group, _load_data, _save_data, _today_str
from .boss import _active_world_boss, _cleanup_stale_world_boss, _ensure_boss_participant
from .config import _error, _line
from .player import (
    _ensure_player,
    _equip_status,
    _level_of,
    _level_progress,
    _resolve_group,
    _title_of,
)

status_cmd = on_command("我的角色", priority=5, block=True)


@status_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await status_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

    if is_sleeping():
        await status_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    today = _today_str()
    async with LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        settlement_lines, changed = _cleanup_stale_world_boss(group, today)
        user = _ensure_player(group, event.get_user_id(), _display_name(event))

        world_boss = _active_world_boss(group, today)
        if not world_boss:
            boss_line = "· 世界BOSS：当前无世界BOSS"
        else:
            participants = world_boss.get("participants")
            had_record = isinstance(participants, dict) and event.get_user_id() in participants
            participant = _ensure_boss_participant(world_boss, event.get_user_id(), user, today)
            if participant is not None and not had_record:
                changed = True
            boss_equip = _equip_status(participant or {"equip_date": ""}, today)
            boss_line = f"· 世界BOSS：{world_boss.get('name', '世界BOSS')}（装备：{boss_equip}）"

        if changed:
            _save_data(data)

        prog = _level_progress(user.get("exp", 0))
        title = _title_of(prog["level"])
        bag = sum(int(v) for v in (user.get("inventory") or {}).values())
        wins, total = int(user.get("hunt_wins", 0)), int(user.get("hunt_total", 0))
        lines = [
            *settlement_lines,
            f"🗡️ 角色档案 · {_display_name(event)}",
            f"· 等级：Lv{prog['level']} {title}（经验 {prog['into']}/{prog['span']}）",
            f"· 战绩：{wins} 胜 / 共 {total} 场",
            f"· 今日装备：{_equip_status(user, today)}",
            boss_line,
            f"· 积分：{int(user.get('points', 0))}",
            f"· 背包：{bag} 件道具",
        ]
    await status_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：排行榜（等级榜，纯文字、不 @、不出图） ====================

rank_cmd = on_command("群排行榜", priority=5, block=True)


@rank_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
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
async def _(event: Event, args: Message = CommandArg()):
    if args and args.extract_plain_text().strip():
        return
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    msg = (
        "🗺️ 冒险系统\n"
        "━━━━━━━━━━━━━━\n"
        "· 签到 — 领积分、经验和今天这套装备\n"
        "· 今日打怪 — 用今天的装备出去打一趟，赚经验、积分和掉落\n"
        "· 组队@某人 — 邀请群友一起作战；羁绊越深越容易组队成功，结队后会有协作加成并小幅增长羁绊；负羁绊会更难磨合，也可能触发额外事件（对方需已签到）\n"
        "· 世界BOSS — 查看当前世界BOSS状态；它会在普通打怪后以极低概率出现，隔天没打完会按贡献补发一笔收尾奖励\n"
        "· 攻击世界BOSS / 组队世界BOSS@某人 — 世界BOSS是独立的群挑战线，签到过的人都能各打一次，击败后按贡献结算\n"
        "· 强化世界BOSS装备 — 世界BOSS出现后，可单独强化这套临时装备，最多3次\n"
        "· 强化今日装备 — 花积分提战力：第1次30分 / 第2次60分 / 第3次90分，每日限3次\n"
        "· 购买装备 — 装备损坏后花100积分买一套替换装（每天限1次，打怪经验和积分减半）\n"
        "· 我的角色 — 看等级、称号、战绩和装备状态（含世界BOSS）\n"
        "· 群排行榜 — 看本群谁练得最快\n"
        "· 我的背包 / 使用 [道具] — 看道具 / 用掉它；礼物券需 @ 对方\n"
        "\n"
        "💡 平时走个人挑战线，世界BOSS出现时再一起打群挑战。"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
