"""隐藏运势 + 签到钩子。

运势是隐藏值：签到时暗掷（不外显），仅在打怪时影响胜负（combat_factor）与掉落（drop_factor）。
签到钩子 on_signin：暗掷运势 + 发固定经验 + 发今日装备；通过 game_store 钩子表被 gift 的签到回调（gift.py 不动）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
import random

from ...core import TZ_CN
from ...core.game_store import _today_str, _weighted_choice, register_signin_hook
from .config import _cfg, _line
from .player import _ensure_player, _grant_equip, _level_of


def _yesterday_str() -> str:
    return (datetime.now(TZ_CN).date() - timedelta(days=1)).isoformat()

# ==================== 运势抽取（隐藏） ====================

def _fortune_levels() -> list[dict]:
    levels = _cfg("fortune", {}).get("levels", [])
    return levels if isinstance(levels, list) and levels else []


def _fortune_by_key(key: str) -> dict:
    for lv in _fortune_levels():
        if lv.get("key") == key:
            return lv
    return {}


def _roll_fortune(user: dict, rng=random) -> str:
    """按权重抽今日运势 key，叠加两条修正：连签保底 + 昨日大凶提大吉。"""
    fcfg = _cfg("fortune", {})
    weights = {lv["key"]: int(lv.get("weight", 0)) for lv in _fortune_levels()}
    lucky = set(fcfg.get("lucky_keys", []))

    if int(user.get("no_lucky_streak", 0)) >= int(fcfg.get("lucky_pity_days", 5)):
        boost = int(fcfg.get("lucky_pity_boost", 30))
        for k in lucky:
            if k in weights:
                weights[k] += boost

    if user.get("last_fortune") == fcfg.get("daxiong_key", "daxiong"):
        dk = fcfg.get("daji_key", "daji")
        if dk in weights:
            weights[dk] += int(fcfg.get("daji_after_daxiong_boost", 20))

    return _weighted_choice(weights, rng)


# ==================== 签到钩子 ====================

def _bump_streak(user: dict, today: str) -> int:
    """更新连续签到：上次签到==昨天则 +1，否则重置为 1；写当日。返回当前连签天数。"""
    user["signin_streak"] = int(user.get("signin_streak", 0)) + 1 if user.get("signin_last_date") == _yesterday_str() else 1
    user["signin_last_date"] = today
    return int(user["signin_streak"])


def on_signin(group: dict, user_id: str, rng=random) -> str:
    """签到结算：暗掷运势 + 发经验（基础 + 连签递增）+ 发今日装备。返回追加播报行（当天已签到则返回空串）。"""
    user = _ensure_player(group, user_id)
    today = _today_str()
    if user.get("fortune_date") == today:
        return ""  # 当天已签到（含超管重复签到）：不重复发放

    key = _roll_fortune(user, rng)
    lucky = set(_cfg("fortune", {}).get("lucky_keys", []))
    user["no_lucky_streak"] = 0 if key in lucky else int(user.get("no_lucky_streak", 0)) + 1
    user["last_fortune"] = key
    user["fortune"] = key
    user["fortune_date"] = today

    # 连续签到：递增额外经验 bonus = min(streak*per_day, cap)
    streak = _bump_streak(user, today)
    scfg = _cfg("signin_streak", {})
    streak_bonus = min(streak * int(scfg.get("per_day", 10)), int(scfg.get("cap", 100)))

    base_exp = int(_cfg("signin", {}).get("exp", 50))
    user["exp"] = int(user.get("exp", 0)) + base_exp + streak_bonus
    _grant_equip(user, today, rng)  # 发今日装备（按发放后的等级）

    line = _line("signin_exp", exp=base_exp, level=_level_of(user["exp"]))
    if streak_bonus > 0:
        line = line + "\n" + _line("signin_streak", streak=streak, bonus=streak_bonus)
    return line


# 注册到共享签到钩子表：gift 的「签到」会在结算时回调本函数。
register_signin_hook(on_signin)
