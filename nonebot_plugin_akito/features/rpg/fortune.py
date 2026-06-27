"""签到运势：搭车 gift 的「签到」给当日运势 + 签到经验，并提供「运势」查询。

通过 core.game_store 的签到钩子注册表接入 —— gift 的签到在持锁结算时回调 on_signin，
本模块只在传入的 group/user dict 上做纯内存改动并返回追加播报行，不另加锁/读写文件。
"""

from __future__ import annotations

import random

from nonebot import on_command
from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import MessageSegment

from ...core import is_sleeping
from ...core.game_store import (
    _display_name,
    _get_group,
    _load_data,
    _render_with_ats,
    _today_str,
    _weighted_choice,
    register_signin_hook,
)
from .config import _cfg, _copy, _error
from .player import _ensure_player, _resolve_group

# ==================== 运势抽取 ====================

def _fortune_levels() -> list[dict]:
    levels = _cfg("fortune", {}).get("levels", [])
    return levels if isinstance(levels, list) and levels else []


def _fortune_by_key(key: str) -> dict:
    for lv in _fortune_levels():
        if lv.get("key") == key:
            return lv
    return {}


def _roll_fortune(user: dict, rng=random) -> str:
    """按权重抽今日运势 key，并叠加两条修正：连签保底 + 昨日大凶提大吉。"""
    fcfg = _cfg("fortune", {})
    weights = {lv["key"]: int(lv.get("weight", 0)) for lv in _fortune_levels()}
    lucky = set(fcfg.get("lucky_keys", []))

    # 保底：连续未出「吉以上」达到天数 → 给每个「吉以上」档加权重
    if int(user.get("no_lucky_streak", 0)) >= int(fcfg.get("lucky_pity_days", 5)):
        boost = int(fcfg.get("lucky_pity_boost", 30))
        for k in lucky:
            if k in weights:
                weights[k] += boost

    # 昨日大凶 → 今日大吉额外加权（塞翁失马）
    if user.get("last_fortune") == fcfg.get("daxiong_key", "daxiong"):
        dk = fcfg.get("daji_key", "daji")
        if dk in weights:
            weights[dk] += int(fcfg.get("daji_after_daxiong_boost", 20))

    return _weighted_choice(weights, rng)


def _line(copy_key: str, **ctx) -> str:
    """随机取一条文案并安全格式化（缺占位符不抛错），用于签到追加行（纯文本）。"""
    pool = _copy(copy_key)
    template = random.choice(pool) if pool else ""
    try:
        return template.format(**ctx)
    except (KeyError, IndexError):
        return template


def on_signin(group: dict, user_id: str, rng=random) -> str:
    """签到钩子：静默掷当日运势（隐藏值，供打野/运势指令），按运势系数发签到经验。

    返回的追加播报行**只报经验、不外显运势**（运势播报交给群里另一个签到 bot）；当天已掷过则返回空串。
    """
    user = _ensure_player(group, user_id)
    today = _today_str()
    if user.get("fortune_date") == today:
        return ""  # 当天已掷过（超管重复签到等）：不重复发放、不重复播报

    key = _roll_fortune(user, rng)
    lv = _fortune_by_key(key)
    lucky = set(_cfg("fortune", {}).get("lucky_keys", []))

    # 更新连签保底计数与「最近运势」（均为隐藏值，不外显）
    user["no_lucky_streak"] = 0 if key in lucky else int(user.get("no_lucky_streak", 0)) + 1
    user["last_fortune"] = key
    user["fortune"] = key
    user["fortune_date"] = today

    # 经验仍受运势隐藏加成（exp_mult），但播报只报经验数、不点名运势
    mult = float(lv.get("exp_mult", 1.0))
    exp_gain = int(int(_cfg("fortune", {}).get("signin_exp_base", 50)) * mult)
    user["exp"] = int(user.get("exp", 0)) + exp_gain

    if exp_gain <= 0:
        return _line("signin_exp_zero")
    return _line("signin_exp", exp=exp_gain)


# 注册到共享签到钩子表：gift 的「签到」会在结算时回调本函数。
register_signin_hook(on_signin)


# ==================== 指令：运势 ====================

fortune_cmd = on_command("运势", aliases={"今日运势"}, priority=5, block=True)


@fortune_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group(event)
    if rejection:
        await fortune_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    if is_sleeping():
        await fortune_cmd.finish(MessageSegment.reply(event.message_id) + _error("sleeping"))

    user_id = event.get_user_id()
    data = _load_data()
    group = _get_group(data, group_id)
    user = _ensure_player(group, user_id, _display_name(event))  # 只读展示，不落库

    if user.get("fortune_date") != _today_str():
        await fortune_cmd.finish(MessageSegment.reply(event.message_id) + _error("need_signin"))

    name = _fortune_by_key(user.get("fortune", "")).get("name", "未知")
    msg = _render_with_ats(random.choice(_copy("fortune_query")), {"a": user_id, "fortune": name})
    await fortune_cmd.finish(MessageSegment.reply(event.message_id) + msg)
