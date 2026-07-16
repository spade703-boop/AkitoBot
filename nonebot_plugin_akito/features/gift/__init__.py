"""送礼系统：彰冬同人圈主题的群友互送小游戏。

玩法闭环（完全自包含，不依赖其他模块）：
- `签到`：每天 1 次领取积分（赚取入口）。
- `送礼@对方`：每天 1 次，系统从「你当前积分买得起的礼物」里随机送一份给对方，按权重抽随机事件
  （普通/暴击/回礼/失败/意外），累积两个群友之间的「亲密度（同好羁绊）」。
  高档保证礼「自己产的彰冬饭」「彰冬婚礼邀请函」一旦抽中，必定触发「惊喜升级」固定结算；
  达到 1112 积分后会独立判定婚礼邀请函；送出者首次赠送且该关系尚无 1314 邀请函时带纪念加成。
- `偷@对方`：每天 2 次，小概率顺走对方少量积分（强保护 + 偷必掉羁绊，偷越亲近掉越多）。
- `我的积分` / `礼物列表` / `亲密度` / `群羁绊排行` 查询；玩家档案与羁绊跨群共享；
  `重置送礼`（超管）清空全局玩家数据。

数据与套路对照 features/random_keyword/：按 QQ 绑定玩家状态、每日按日期重置、原子读写、文件优先+缺省兜底配置。
"""

from __future__ import annotations

import os
import random
import time

from nonebot import on_command
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg

from ...core import (
    ALLOWED_CHAT_GROUPS,
    SUPERUSER_QQ,
    is_sleeping,
)
from ...core.game_store import (
    LOCK,
    SCHEMA_VERSION,  # noqa: F401  仅供 tests/test_gift.py 引用 gift.SCHEMA_VERSION
    _add_intimacy,
    _add_points,
    _display_name,
    _first_at_qq,
    _get_group,
    _get_intimacy,
    _load_data,
    _new_data,
    _new_group,
    _normalize_data,  # noqa: F401  仅供 tests/test_gift.py 引用 gift._normalize_data
    _pair_key,  # noqa: F401  仅供 tests/test_gift.py 引用 gift._pair_key
    _render_with_ats,
    _save_data,
    _today_str,
    _weighted_choice,
    get_user,
    resolve_group_id,
    run_signin_hooks,
)
from .config import (
    DEFAULT_GIFT_CONFIG,
    GIFT_CONFIG,
    _affordable_gifts,
    _bond_levels,
    _cfg,
    _cheapest_gift,
    _copy,
    _error,
    _gift_list,
    _is_special_gift,
    _mishap_spec,
    _mishaps,
    _pick_gift,
    _pick_gift_by_name,
    _return_gifts,
    _return_spec,
    _roll_main_event,
    _roll_mishap,
    _roll_return_gift,
    _steal_cfg,
    _wedding_cfg,
    reload_gift_config,
)
from .logic import (
    _apply_historical_wedding_records,
    _bond_card,
    _bond_level,
    _build_broadcast,
    _bump_count,
    _count_key,
    _get_count,
    _get_user,
    _name_of,
    _outcome_copy_key,
    _reset_today_signins,
    _reset_today_steals,
    _record_wedding_invitation,
    _resolve_group,
    _settle,
    _settle_wedding_invitation,
    _settle_steal,
    _sign_in_delay,
    _steal_bond_loss,
    _steal_outcome,
    _top_partners,
    _wedding_pair_has_1314,
)
from .pages import build_bond_page_data, build_bond_rank_page_data, build_my_bonds_page_data
from .render import render_bond_page

GIFT_USE_HTML_RENDER = os.environ.get("GIFT_USE_HTML_RENDER", "1").strip() not in {"0", "false", "False"}


# ==================== 数据持久化 / 亲密度（复用 core.game_store） ====================
# 存储原语（读写/锁/积分/亲密度/每日键/加权随机/@渲染/群校验）已抽到 core.game_store，
# 本模块直接复用，并共用同一把 LOCK —— 使送礼/签到/偷与 rpg 打野等串行写同一份
# gift_data.json，互不踩踏。下方仅保留送礼专属的用户字段封装。

_GIFT_LOCK = LOCK


# ==================== 指令：签到 ====================

sign_cmd = on_command("签到", priority=5, block=True)


@sign_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ  # 超管不限次（测试用）
    if is_sleeping() and not is_superuser:  # 0–6 点睡眠拦截（超管除外）
        await sign_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        user = _get_user(group, user_id, _display_name(event))

        if not is_superuser and user.get("last_sign_in") == today:
            _save_data(data)  # 全局已签过仍记录当前群成员索引，保持群榜/活跃统计完整
            return  # 重复签到静默：群里另有签到 bot 应答，避免双重刷屏

        sign_cfg = _cfg("sign_in", {})
        amount = random.randint(int(sign_cfg.get("min", 50)), int(sign_cfg.get("max", 100)))
        user["points"] = int(user.get("points", 0)) + amount
        user["last_sign_in"] = today
        user["protect_until"] = time.time() + int(_steal_cfg().get("protect_minutes", 60)) * 60
        # 签到搭车钩子（rpg 运势/经验等）：持锁内纯内存改 group，收集追加播报行
        extra_lines = run_signin_hooks(group, user_id, random)
        _save_data(data)
        total = int(user["points"])

    # 出锁后再延迟 3–5s 发送（不占锁），错开另一个签到 bot 的消息
    await _sign_in_delay()
    template = random.choice(_copy("sign_in"))
    msg = _render_with_ats(template, {"a": user_id, "amount": amount, "total": total})
    for line in extra_lines:
        msg = msg + "\n" + line
    await sign_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：送礼 ====================

gift_cmd = on_command("送礼", priority=5, block=True)


@gift_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    target_qq = _first_at_qq(getattr(event, "original_message", None))

    # 没 @ 任何人或格式不对 → 静默
    if not target_qq or target_qq == "all":
        return
    if args and args.extract_plain_text().strip():
        return

    sender_id = event.get_user_id()
    is_superuser = sender_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    if target_qq == sender_id:
        return
    if target_qq == str(getattr(bot, "self_id", "")):
        return

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        _apply_historical_wedding_records(group)
        sender = _get_user(group, sender_id, _display_name(event))
        _get_user(group, target_qq)  # 确保被送者入册（用于排行/亲密度查询）

        if not is_superuser and sender.get("last_gift") == today:
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id) + _error("already_gifted")
            )

        points = int(sender.get("points", 0))
        gift = _pick_gift(points)
        if gift is None:
            cheapest = _cheapest_gift() or {"name": "", "cost": 0}
            await gift_cmd.finish(
                MessageSegment.reply(event.message_id)
                + _error("insufficient", name=cheapest.get("name", ""), cost=int(cheapest.get("cost", 0)), total=points)
            )

        # 先扣消耗，再按事件结算（部分意外会返还）
        sender["points"] = points - int(gift["cost"])
        sender["last_gift"] = today

        if _is_special_gift(gift):
            main_event, mishap, return_key = "special", None, None
        else:
            main_event = _roll_main_event()
            mishap = _roll_mishap() if main_event == "mishap" else None
            return_key = _roll_return_gift() if main_event == "return" else None

        out = _settle(group, sender_id, target_qq, gift, main_event, mishap, return_key)
        if str(gift.get("name", "")) == str(_wedding_cfg().get("gift_name", "彰冬婚礼邀请函")):
            out = _settle_wedding_invitation(group, sender_id, target_qq, out, today)
        _bump_count(group, sender_id, target_qq)  # 记一次有向送礼（无论事件结果）
        _save_data(data)

        broadcast = _build_broadcast(out, sender_id, target_qq)
        await gift_cmd.finish(MessageSegment.reply(event.message_id) + broadcast)


# ==================== 指令：偷 ====================

steal_cmd = on_command("偷", aliases={"偷积分"}, priority=5, block=True)


@steal_cmd.handle()
async def _(bot: Bot, event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    target_qq = _first_at_qq(getattr(event, "original_message", None))

    # 没 @ 任何人或格式不对 → 静默
    if not target_qq or target_qq == "all":
        return
    if args and args.extract_plain_text().strip():
        return

    thief_id = event.get_user_id()
    is_superuser = thief_id == SUPERUSER_QQ
    if is_sleeping() and not is_superuser:
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))
    if target_qq == thief_id:
        return
    if target_qq == str(getattr(bot, "self_id", "")):
        return

    cfg = _steal_cfg()
    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        thief = _get_user(group, thief_id, _display_name(event))
        victim = _get_user(group, target_qq)

        if not is_superuser:
            if thief.get("steal_date") != today:
                thief["steal_date"], thief["steal_used"] = today, 0
            if int(thief.get("steal_used", 0)) >= int(cfg.get("daily_limit", 2)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_limit"))
            if int(victim.get("points", 0)) < int(cfg.get("min_target_points", 50)):
                await steal_cmd.finish(
                    MessageSegment.reply(event.message_id)
                    + _error("steal_too_poor", min=int(cfg.get("min_target_points", 50)))
                )
            if time.time() < float(victim.get("protect_until", 0)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_protected"))
            if victim.get("robbed_date") != today:
                victim["robbed_date"], victim["robbed_count"] = today, 0
            if int(victim.get("robbed_count", 0)) >= int(cfg.get("victim_daily_limit", 3)):
                await steal_cmd.finish(MessageSegment.reply(event.message_id) + _error("steal_protected"))

        outcome = _steal_outcome()
        out = _settle_steal(group, thief_id, target_qq, outcome)

        if not is_superuser:
            thief["steal_date"], thief["steal_used"] = today, int(thief.get("steal_used", 0)) + 1
            victim["robbed_date"], victim["robbed_count"] = today, int(victim.get("robbed_count", 0)) + 1
        _save_data(data)

        template = random.choice(_copy(f"steal_{outcome}"))
        msg = _render_with_ats(template, {
            "a": thief_id, "b": target_qq, "amount": out["amount"], "bond": out["bond"],
        })
        await steal_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：我的积分 ====================

points_cmd = on_command("我的积分", priority=5, block=True)


@points_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await points_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    today = _today_str()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _get_user(group, user_id, _display_name(event))

    can_sign = "可签到 ✅" if user.get("last_sign_in") != today else "今日已签到"
    can_gift = "可送礼 ✅" if user.get("last_gift") != today else "今日已送礼"
    msg = (
        f"你当前有 {int(user.get('points', 0))} 积分。\n"
        f"· {can_sign}\n· {can_gift}"
    )
    await points_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：礼物列表 ====================

list_cmd = on_command("礼物列表", priority=5, block=True)


@list_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    lines = ["🎁 彰冬礼物档位（稀有度递增）："]
    for gift in _gift_list():
        lines.append(f"· {gift['name']}　{gift['cost']} 积分　羁绊+{gift['intimacy']}")
    lines.append("用法：送礼@某人 —— 系统会从你买得起的礼物里随机送一份（越贵的越容易抽中）。")
    await list_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：亲密度 ====================

intimacy_cmd = on_command("我的羁绊", priority=5, block=True)


@intimacy_cmd.handle()
async def _(bot: Bot, event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    data = _load_data()
    group = _get_group(data, group_id)
    target_qq = _first_at_qq(getattr(event, "original_message", None))

    if target_qq and target_qq not in ("all", user_id):
        # 尝试获取对方群名片（bot API → card → nickname → 兜底 QQ）
        target_name = _name_of(group, target_qq)
        if target_name.startswith("用户"):
            try:
                member_info = await bot.get_group_member_info(
                    group_id=int(group_id), user_id=int(target_qq)
                )
                target_name = member_info.get("card") or member_info.get("nickname") or target_qq
            except Exception:
                target_name = target_qq

        if GIFT_USE_HTML_RENDER:
            left = {"qq": user_id, "name": _display_name(event)}
            right = {"qq": target_qq, "name": target_name}
            intimacy = _get_intimacy(group, user_id, target_qq)
            img_bytes = None
            try:
                page_data = build_bond_page_data(left, right, intimacy, levels=_bond_levels())
                img_bytes = await render_bond_page("bond.html", page_data)
            except Exception as e:
                logger.warning(f"bond render failed ({e}), falling back to text")
            if img_bytes is not None:
                await intimacy_cmd.finish(
                    MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
                )
            else:
                await intimacy_cmd.finish(
                    MessageSegment.reply(event.message_id) + _bond_card(group, user_id, target_qq)
                )
        else:
            await intimacy_cmd.finish(
                MessageSegment.reply(event.message_id) + _bond_card(group, user_id, target_qq)
            )
        return  # @某人分支结束，不继续往下走

    # 不带 @：列出自己所有羁绊
    partners = _top_partners(group, user_id, limit=999)
    if not partners:
        await intimacy_cmd.finish(
            MessageSegment.reply(event.message_id) + "你还没有和谁建立羁绊呢，快去送礼吧～"
        )

    if GIFT_USE_HTML_RENDER:
        partner_dicts: list[dict] = []
        for other_id, value in partners:
            name = _name_of(group, other_id)
            partner_dicts.append({"qq": other_id, "name": name, "intimacy": value})
        owner = {"qq": user_id, "name": _display_name(event)}
        img_bytes = None
        try:
            page_data = build_my_bonds_page_data(owner, partner_dicts, levels=_bond_levels())
            img_bytes = await render_bond_page("my_bonds.html", page_data)
        except Exception as e:
            logger.warning(f"my bonds render failed ({e}), falling back to text")
        if img_bytes is not None:
            await intimacy_cmd.finish(
                MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
            )
        else:
            lines = ["你的同好羁绊 Top："]
            for other_id, value in partners[:5]:
                lines.append(f"· {_name_of(group, other_id)}：{value}（{_bond_level(value)['name']}）")
            await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    else:
        lines = ["你的同好羁绊 Top："]
        for other_id, value in partners[:5]:
            lines.append(f"· {_name_of(group, other_id)}：{value}（{_bond_level(value)['name']}）")
        await intimacy_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


_TEST_MY_BOND_PARTNERS: list[dict] = [
    {"qq": "test01", "name": "测试同好01", "avatar": "", "intimacy": 7200},
    {"qq": "test02", "name": "测试同好02", "avatar": "", "intimacy": 4200},
    {"qq": "test03", "name": "测试同好03", "avatar": "", "intimacy": 2600},
    {"qq": "test04", "name": "测试同好04", "avatar": "", "intimacy": 1314},
    {"qq": "test05", "name": "测试同好05", "avatar": "", "intimacy": 980},
    {"qq": "test06", "name": "测试同好06", "avatar": "", "intimacy": 520},
    {"qq": "test07", "name": "测试同好07", "avatar": "", "intimacy": 260},
    {"qq": "test08", "name": "测试同好08", "avatar": "", "intimacy": 120},
    {"qq": "test09", "name": "测试同好09", "avatar": "", "intimacy": 45},
    {"qq": "test10", "name": "测试同好10", "avatar": "", "intimacy": 5},
    {"qq": "test11", "name": "测试同好11", "avatar": "", "intimacy": -10},
    {"qq": "test12", "name": "测试同好12", "avatar": "", "intimacy": -50},
    {"qq": "test13", "name": "测试同好13", "avatar": "", "intimacy": -120},
    {"qq": "test14", "name": "测试同好14", "avatar": "", "intimacy": -300},
    {"qq": "test15", "name": "测试同好15", "avatar": "", "intimacy": -650},
    {"qq": "test16", "name": "测试同好16", "avatar": "", "intimacy": -1000},
]


# ==================== 指令：测试我的羁绊界面 ====================

test_my_bonds_cmd = on_command("test我的羁绊", aliases={"测试我的羁绊"}, priority=5, block=True)


@test_my_bonds_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if str(event.get_user_id()) != SUPERUSER_QQ:
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + "这个测试指令仅限超管使用。")

    owner = {"qq": event.get_user_id(), "name": f"{_display_name(event)}（测试）", "avatar": ""}
    try:
        page_data = build_my_bonds_page_data(
            owner,
            [p.copy() for p in _TEST_MY_BOND_PARTNERS],
            levels=_bond_levels(),
            title="我的羁绊 · 测试",
        )
        img_bytes = await render_bond_page("my_bonds.html", page_data)
    except Exception as e:
        logger.warning(f"test my bonds render failed ({e})")
        await test_my_bonds_cmd.finish(MessageSegment.reply(event.message_id) + f"测试羁绊图渲染失败：{e}")

    await test_my_bonds_cmd.finish(
        MessageSegment.reply(event.message_id)
        + "测试数据：16 段羁绊，不写入真实数据。\n"
        + MessageSegment.image(img_bytes)
    )
# ==================== 指令：群羁绊排行 ====================

rank_cmd = on_command("群羁绊排行", aliases={"群羁绊排行", "羁绊排行"}, priority=5, block=True)


@rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    data = _load_data()
    group = _get_group(data, group_id)
    pairs = sorted(group.get("intimacy", {}).items(), key=lambda kv: int(kv[1]), reverse=True)[:10]
    if not pairs:
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + "目前还没有全局羁绊数据，快去送礼吧～")

    if GIFT_USE_HTML_RENDER:
        entries: list[dict] = []
        for key, value in pairs:
            a, b = key.split("|||")
            entries.append({
                "left": {"qq": a, "name": _name_of(group, a)},
                "right": {"qq": b, "name": _name_of(group, b)},
                "intimacy": int(value),
            })
        img_bytes = None
        try:
            rank_data = build_bond_rank_page_data(entries, levels=_bond_levels())
            img_bytes = await render_bond_page("bond_rank.html", rank_data)
        except Exception:
            logger.warning("bond rank render failed, falling back to text")
        if img_bytes is not None:
            await rank_cmd.finish(
                MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes)
            )
        else:
            lines = ["\U0001f49e 全局同好羁绊排行："]
            for idx, (key, value) in enumerate(pairs, 1):
                a, b = key.split("|||")
                lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}（{_bond_level(value)['name']}）")
            await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))
    else:
        lines = ["💞 全局同好羁绊排行："]
        for idx, (key, value) in enumerate(pairs, 1):
            a, b = key.split("|||")
            lines.append(f"{idx}. {_name_of(group, a)} × {_name_of(group, b)}：{value}（{_bond_level(value)['name']}）")
        await rank_cmd.finish(MessageSegment.reply(event.message_id) + "\n".join(lines))


# ==================== 指令：重置送礼（超管） ====================

reset_cmd = on_command("重置送礼", priority=5, block=True)


@reset_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return
    async with _GIFT_LOCK:
        _save_data(_new_data())
    await reset_cmd.finish(MessageSegment.reply(event.message_id) + "已清空全局送礼/积分/羁绊/RPG数据。")


# ==================== 指令：重置本群签到（超管） ====================

reset_signin_cmd = on_command(
    "重置本群签到",
    aliases={"重置全群签到", "重置签到次数"},
    priority=5,
    block=True,
)


@reset_signin_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await reset_signin_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        cleared = _reset_today_signins(group, today)
        _save_data(data)

    if cleared:
        msg = f"已清掉当前群 {cleared} 名成员的全局签到闸门。RPG 连签和今日装备没动。"
    else:
        msg = "当前群今天还没人被全局签到闸门卡住。"
    await reset_signin_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：重置偷群友（超管） ====================

reset_steal_cmd = on_command("重置偷群友", priority=5, block=True)


@reset_steal_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return

    group_id, rejection = _resolve_group(event)
    if rejection:
        await reset_steal_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    today = _today_str()
    async with _GIFT_LOCK:
        data = _load_data()
        group = _get_group(data, group_id)
        cleared = _reset_today_steals(group, today)
        _save_data(data)

    msg = (
        f"已重置当前群 {cleared} 名成员的全局偷取/被偷闸门。"
        if cleared
        else "当前群今天还没人被全局偷取次数卡住。"
    )
    await reset_steal_cmd.finish(MessageSegment.reply(event.message_id) + msg)


# ==================== 指令：送礼功能帮助 ====================

help_cmd = on_command("送礼功能帮助", aliases={"送礼帮助", "送礼说明"}, priority=5, block=True)


@help_cmd.handle()
async def _(event: Event):
    msg = (
        "🎁 彰冬送礼系统\n"
        "━━━━━━━━━━━━━━\n"
        "· 签到 — 每天领一次积分（50~100）\n"
        "· 送礼@某人 — 每天一次，随机送礼物给对方，累积羁绊值\n"
        "· 偷@某人 — 每天两次，冒险顺走对方积分（会掉羁绊）\n"
        "· 我的积分 — 查看当前积分和今日状态\n"
        "· 礼物列表 — 查看全部礼物档位和花费\n"
        "· 我的羁绊@某人 — 查看你与 ta 的羁绊详情图\n"
        "· 群羁绊排行 — 查看全局羁绊排行榜\n"
        "\n"
        "💡 礼物越贵羁绊加得越多；送礼有概率暴击/回礼/意外事件。\n"
        "💡 偷人需谨慎：偷越亲近的人掉羁绊越多，还可能被反杀。"
    )
    if isinstance(event, GroupMessageEvent):
        await help_cmd.finish(MessageSegment.reply(event.message_id) + msg)
    else:
        await help_cmd.finish(msg)
