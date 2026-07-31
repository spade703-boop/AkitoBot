"""查询/展示指令：我的角色（等级 + 称号 + 战绩 + 今日装备状态 + 积分 + 背包）、排行榜（等级榜）、冒险帮助。

战力为隐藏值，对外不显示数字。"""

from __future__ import annotations

from datetime import datetime

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from ...core import ALLOWED_CHAT_GROUPS
from ...core.game_store import (
    LOCK,
    _display_name,
    _get_group,
    _load_data,
    _save_data,
    _today_str,
    _weekly_investment,
)
from ..gift.pages import FOOTER_RPG_BRAND, qq_avatar_uri
from ..gift.render import render_bond_page
from .boss import _active_world_boss, _cleanup_stale_world_boss, _ensure_boss_participant
from .config import _error, _line
from .inventory import _active_battle_supply
from .player import (
    _ensure_player,
    _equip_status,
    _level_of,
    _level_progress,
    _resolve_group,
    _title_of,
)

status_cmd = on_command("我的角色", priority=5, block=True)


def _weekly_tendency(adventure_spent: int, gift_spent: int) -> str:
    if adventure_spent <= 0 and gift_spent <= 0:
        return "尚未决定"
    if gift_spent <= 0:
        return "偏向冒险"
    if adventure_spent <= 0:
        return "偏向羁绊"
    if adventure_spent > gift_spent * 1.2:
        return "偏向冒险"
    if gift_spent > adventure_spent * 1.2:
        return "偏向羁绊"
    return "均衡发展"


def _battle_status(user: dict) -> str:
    parts: list[str] = []
    regular = _active_battle_supply(user)
    if regular:
        parts.append(f"{regular['name']}（剩余 {regular['uses']} 场）")
    guard = _active_battle_supply(user, guard=True)
    if guard:
        parts.append(f"{guard['name']}（待触发）")
    return " / ".join(parts) if parts else "暂无"


@status_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await status_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    if args and args.extract_plain_text().strip():
        return

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
        weekly = _weekly_investment(user, today)
        supply_count = int(weekly.get("supply_count", 0))
        supply_spent = int(weekly.get("supply_spent", 0))
        gift_spent = int(weekly.get("gift_spent", 0))
        trophies = user.get("world_boss_trophies")
        if isinstance(trophies, list) and trophies:
            trophy_line = "· 世界BOSS收藏：" + "、".join(str(item) for item in trophies if str(item))
        else:
            trophy_line = "· 世界BOSS收藏：暂无"
        lines = [
            *settlement_lines,
            f"🗡️ 角色档案 · {_display_name(event)}",
            f"· 等级：Lv{prog['level']} {title}（经验 {prog['into']}/{prog['span']}）",
            f"· 战绩：{wins} 胜 / 共 {total} 场",
            f"· 今日装备：{_equip_status(user, today)}",
            boss_line,
            f"· 积分：{int(user.get('points', 0))}",
            f"· 背包：{bag} 件道具",
            f"· 本周投入：冒险补给 {supply_count}/7（已花费 {supply_spent} 积分） / 送礼 {gift_spent} 积分",
            f"· 本周倾向：{_weekly_tendency(supply_spent, gift_spent)}",
            f"· 当前战备：{_battle_status(user)}",
        ]
        if _active_battle_supply(user) or _active_battle_supply(user, guard=True):
            lines.append("· 战备范围：普通个人挑战 / 普通组队挑战（世界BOSS不生效）")
        lines.append(trophy_line)
    await status_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：排行榜（等级榜） ====================

rank_cmd = on_command("群排行榜", priority=5, block=True)


def _ranked_players(users: dict, limit: int = 10) -> list[tuple[str, dict]]:
    return sorted(
        (
            (str(uid), rec)
            for uid, rec in users.items()
            if isinstance(rec, dict) and int(rec.get("exp", 0)) > 0
        ),
        key=lambda item: int(item[1].get("exp", 0)),
        reverse=True,
    )[:limit]


def _rank_page_data(ranked: list[tuple[str, dict]]) -> dict:
    rows = []
    for rank, (uid, rec) in enumerate(ranked, 1):
        progress = _level_progress(rec.get("exp", 0))
        name = str(rec.get("display_name") or f"用户{uid}")
        rows.append(
            {
                "rank": rank,
                "player": {
                    "qq": uid,
                    "name": name,
                    "avatar": qq_avatar_uri(uid),
                    "initial": name[:1] if name else "?",
                },
                "level": progress["level"],
                "title": _title_of(progress["level"]),
                "exp": progress["exp"],
                "progress_pct": round(progress["into"] / progress["span"] * 100),
                "progress_text": f"{progress['into']}/{progress['span']}",
                "wins": int(rec.get("hunt_wins", 0)),
                "battles": int(rec.get("hunt_total", 0)),
            }
        )
    return {
        "page_width": 680,
        "title": "冒险者等级排行",
        "eyebrow_tail": "ADVENTURER RANKING",
        "pill": f"本群冒险者 TOP {len(rows)}",
        "rows": rows,
        "footer_left": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "footer_right": FOOTER_RPG_BRAND,
    }


def _rank_text(ranked: list[tuple[str, dict]]) -> str:
    lines = [_line("rank_title")]
    for idx, (uid, rec) in enumerate(ranked, 1):
        level = _level_of(rec.get("exp", 0))
        name = rec.get("display_name") or f"用户{uid}"
        wins = int(rec.get("hunt_wins", 0))
        lines.append(f"{idx}. {name}　Lv{level} {_title_of(level)}　胜{wins}场")
    return "\n".join(lines)


async def _render_rank_image(ranked: list[tuple[str, dict]]) -> bytes | None:
    try:
        return await render_bond_page("rpg_rank.html", _rank_page_data(ranked))
    except Exception as error:
        logger.warning(f"rpg rank render failed ({error}), falling back to text")
        return None


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
    ranked = _ranked_players(users)
    if not ranked:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + _error("rank_empty"))

    img_bytes = await _render_rank_image(ranked)
    if img_bytes is not None:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))
    await rank_cmd.finish(MessageSegment.reply(event.message_id) + _rank_text(ranked))


help_cmd = on_command("冒险帮助", aliases={"打怪帮助", "冒险说明"}, priority=5, block=True)

_HELP_TITLE = "🗺️ 冒险系统"
_HELP_HINT = "普通挑战与世界BOSS挑战相互独立；冒险补给战备仅对普通个人及组队挑战生效。"
_HELP_ITEMS = [
    {"command": "签到", "description": "每日领取积分、签到经验与今日装备，并记录当日隐藏运势。"},
    {"command": "今日打怪", "description": "进行一次普通个人挑战，结算经验、积分与随机掉落；每日装备默认可使用一次。"},
    {"command": "组队@某人", "description": "邀请已签到的群友进行普通组队挑战；组队结果受双方羁绊影响，并可能获得协作加成与羁绊成长。"},
    {"command": "战后小奇遇", "description": "个人挑战：普通个人挑战结算后有 6% 概率触发。", "details": [
        "组队挑战：成功成立的普通组队挑战结算后有 4% 概率触发。",
        "组队失败：退化后的单人挑战不触发小奇遇。",
    ]},
    {"command": "小奇遇奖励", "description": "补给袋：提供少量经验与积分。", "details": [
        "营火：战败后提供少量经验。",
        "遗失钱袋：提供少量积分。",
        "破旧宝箱：随机获得以下一种道具。",
        "破旧积分卡：使用后获得 5 积分。",
        "破旧经验券：使用后获得 10 经验。",
        "彰冬无料券：使用后可向一名群友赠送彰冬无料。",
        "组队奖励：数值奖励由双方对半获得，道具奖励双方各获得一份。",
    ]},
    {"command": "强化今日装备", "description": "消耗 30 / 60 / 90 积分依次强化今日装备，每日最多强化三次。"},
    {"command": "购买装备", "description": "今日装备损耗后消耗 100 积分购买一套替换装备；每日限一次，战斗经验与积分减半。"},
    {"command": "开启冒险补给", "description": "每周最多开启七次：前五次各 140 积分，第六次 200 积分，第七次 300 积分；每次获得 30 经验与一件战备。"},
    {"command": "战备使用规则", "description": "战备进入背包后需主动使用。常规战备共享一个槽位，效果不能叠加，并优先于双倍经验卡结算；常规战备生效期间，双倍经验卡暂缓且不消耗，战备用完后继续生效。神官的护符使用独立槽位，可与一种常规战备同时启用，触发时共同结算；护符本身不压制双倍经验卡。所有战备均不作用于世界BOSS。"},
    {"command": "旅人的行囊（35%）", "description": "接下来两次普通个人或组队挑战：战力 +10%，经验 +25%。"},
    {"command": "龙骑士的地图（30%）", "description": "接下来两次普通个人或组队挑战：经验 +20%，掉落率 ×2。"},
    {"command": "厨子的美食（20%）", "description": "接下来两次普通个人或组队挑战：战力 +15%，经验 +40%。"},
    {"command": "神官的护符（12%）", "description": "下一次普通个人或组队挑战在其他援护判定后仍然失败时，将结果转为成功，并使护符持有者本次经验额外 +50%。"},
    {"command": "勇者的远征套装（3%）", "description": "接下来三次普通个人或组队挑战：装备视为强化满，经验 ×2，掉落率 ×2。"},
    {"command": "我的背包", "description": "查看当前持有的消耗品、礼物券与冒险补给战备。"},
    {"command": "使用 [道具名]", "description": "使用消耗品或启用战备；礼物券需使用“使用 [礼物券名] @某人”。"},
    {"command": "我的角色", "description": "查看等级、称号、战绩、装备、积分、背包、世界BOSS收藏、本周投入与当前战备。"},
    {"command": "群排行榜", "description": "查看本群角色经验前十名的等级、称号、升级进度与战绩。"},
    {"command": "世界BOSS", "description": "查看当前世界BOSS的生命值、参与规模与贡献排行；未击败的BOSS会在隔日按贡献结算补偿。"},
    {"command": "攻击世界BOSS", "description": "使用独立的世界BOSS装备进行一次个人攻击，并返回本次造成的精确伤害。"},
    {"command": "组队世界BOSS@某人", "description": "邀请一名已签到的群友共同攻击世界BOSS；双方分别计算伤害，并获得协作伤害加成。"},
    {"command": "强化世界BOSS装备", "description": "世界BOSS存在时强化独立的临时装备，费用为 30 / 60 / 90 积分，最多强化三次。"},
    {"command": "强制开启世界BOSS", "description": "超管指令。立即在本群生成世界BOSS；当前已有世界BOSS时不重复生成。"},
    {"command": "RPG数据", "description": "超管指令。生成本群近 7 日及 30 日的RPG运行数据看板。"},
    {"command": "重置RPG功能", "description": "超管指令。为本群今日已签到玩家重新发放普通装备，不重置签到、运势、连签或世界BOSS状态。"},
    {"command": "冒险帮助", "description": "显示当前RPG功能与指令说明。"},
]


def _help_text() -> str:
    entries = []
    for item in _HELP_ITEMS:
        descriptions = [item["description"], *item.get("details", [])]
        detail_text = "\n".join(f"——{description}" for description in descriptions)
        entries.append(f"· {item['command']}\n{detail_text}")
    body = "\n".join(entries)
    return f"{_HELP_TITLE}\n━━━━━━━━━━━━━━\n{body}\n\n{_HELP_HINT}"


async def _render_help_image() -> bytes | None:
    try:
        return await render_bond_page(
            "rpg_help.html",
            {
                "title": _HELP_TITLE,
                "items": _HELP_ITEMS,
                "hint": _HELP_HINT,
                "page_width": 900,
            },
            viewport_width=900,
        )
    except Exception as e:
        logger.warning(f"rpg help render failed ({e}), falling back to text")
        return None


@help_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if args and args.extract_plain_text().strip():
        return
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    img_bytes = await _render_help_image()
    if img_bytes is not None:
        if isinstance(event, GroupMessageEvent):
            await help_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))
        await help_cmd.finish(MessageSegment.image(img_bytes))
    msg = _help_text()
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    await help_cmd.finish(msg)
