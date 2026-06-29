"""RPG 玩家模型（精简版）：经验→等级派生 + 今日装备（每日一套、一次性、战力随等级涨）。

对外只暴露「等级」；战力是今日装备的隐藏值，由 _combat_power 给打怪用。
所有读写都作用在 core.game_store 加载出来的同一个 group/user dict 上，与 gift 共享存储与锁。
"""

from __future__ import annotations

import random

from ...core.game_store import get_user, resolve_group_id
from .config import _cfg, _error


def _ensure_player(group: dict, user_id, display_name: str = "") -> dict:
    """在通用用户记录（points/display_name）上补齐 rpg 字段。"""
    user = get_user(group, user_id, display_name)
    user.setdefault("exp", 0)                 # 累计经验 → 等级
    user.setdefault("inventory", {})          # 背包：{道具名: 数量}
    # 今日装备（签到发放、打怪损坏、次日重发）
    user.setdefault("equip_date", "")         # 发放日期
    user.setdefault("equip_level", 0)         # 发放时的等级（决定战力）
    user.setdefault("equip_roll", 0)          # 发放时的随机浮动
    user.setdefault("equip_forge", 0)         # 今日已强化次数
    user.setdefault("equip_used", False)      # 是否已被打怪消耗（损坏）
    user.setdefault("equip_rebought", False)  # 是否今天购买过替换装（积分打对折）
    # 隐藏运势（签到暗掷，供打怪）
    user.setdefault("fortune", "")
    user.setdefault("fortune_date", "")
    user.setdefault("last_fortune", "")
    user.setdefault("no_lucky_streak", 0)
    # 战绩（喂排行榜/面板）与连续签到
    user.setdefault("hunt_total", 0)          # 累计打怪次数
    user.setdefault("hunt_wins", 0)           # 累计胜场
    user.setdefault("signin_streak", 0)       # 当前连签天数
    user.setdefault("signin_last_date", "")   # 上次签到日期（算连签）
    return user


# ==================== 经验 / 等级 ====================

def _level_base() -> int:
    return int(_cfg("level_curve", {}).get("base", 100))


def _cum_exp(level: int, base: int) -> int:
    """升到 level 级所需累计经验：base*(L-1)*L/2（L=1 时为 0）。"""
    level = max(1, int(level))
    return base * (level - 1) * level // 2


def _level_of(exp) -> int:
    """累计经验 → 当前等级（≥1，无上限）。"""
    base = _level_base()
    exp = max(0, int(exp))
    level = 1
    while _cum_exp(level + 1, base) <= exp:
        level += 1
    return level


def _level_progress(exp) -> dict:
    """面板进度：{level, exp, into, span, to_next}。"""
    base = _level_base()
    exp = max(0, int(exp))
    level = _level_of(exp)
    cur_floor = _cum_exp(level, base)
    next_floor = _cum_exp(level + 1, base)
    return {
        "level": level,
        "exp": exp,
        "into": exp - cur_floor,
        "span": next_floor - cur_floor,
        "to_next": next_floor - exp,
    }


# ==================== 称号（按等级派生，零存储） ====================

def _title_of(level: int) -> str:
    """等级 → 称号：取 min_level ≤ level 的最高一档（仿羁绊取档）。"""
    titles = _cfg("titles", [])
    if not isinstance(titles, list) or not titles:
        return ""
    name = ""
    for t in titles:
        if int(level) >= int(t.get("min_level", 1)):
            name = str(t.get("name", ""))
        else:
            break
    return name


# ==================== 今日装备（战力为隐藏值） ====================

def _grant_equip(user: dict, today: str, rng=random) -> None:
    """签到发放今日装备：等级取当前等级，随机浮动一次，重置损坏/强化。"""
    ecfg = _cfg("equip", {})
    user["equip_date"] = today
    user["equip_level"] = _level_of(int(user.get("exp", 0)))
    user["equip_roll"] = rng.randint(0, int(ecfg.get("var", 6)))
    user["equip_used"] = False
    user["equip_forge"] = 0
    user["equip_rebought"] = False


def _equip_power(user: dict) -> int:
    """今日装备战力（隐藏）：base + 等级*per + 随机浮动 + 强化次数*step。"""
    ecfg = _cfg("equip", {})
    fcfg = _cfg("forge", {})
    level = int(user.get("equip_level", _level_of(int(user.get("exp", 0)))))
    power = int(ecfg.get("base", 10)) + level * int(ecfg.get("per_level", 5)) + int(user.get("equip_roll", 0))
    power += int(user.get("equip_forge", 0)) * int(fcfg.get("step", 4))
    return power


def _combat_power(user: dict) -> int:
    """打怪用战力 = 今日装备战力（隐藏值）。"""
    return _equip_power(user)


def _equip_intact(user: dict, today: str) -> bool:
    """今日装备是否可用（今天发的且未损坏）。"""
    return user.get("equip_date") == today and not user.get("equip_used")


def _consume_equip(user: dict) -> None:
    user["equip_used"] = True


def _equip_status(user: dict, today: str) -> str:
    """面板用：未签到 / 已就绪(已强化×N) / 已损坏。"""
    if user.get("equip_date") != today:
        return "未签到"
    if user.get("equip_used"):
        return "已损坏"
    forge = int(user.get("equip_forge", 0))
    return f"已就绪（已强化 ×{forge}）" if forge else "已就绪"


# ==================== 群上下文校验（与 gift 同范式，用 rpg 文案） ====================

def _resolve_group(event) -> tuple[str | None, str | None]:
    """返回 (group_id, 拒绝消息)。私聊给提示；非白名单群静默忽略。"""
    group_id, is_private = resolve_group_id(event)
    if group_id is None:
        return None, (_error("private_only") if is_private else None)
    return group_id, None
