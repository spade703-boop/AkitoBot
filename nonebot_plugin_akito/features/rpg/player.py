"""RPG 玩家模型：在共享用户记录上补齐 rpg 字段，并提供经验/等级/战力派生与精力日常重置。

经验是唯一落库的成长值；**等级与战力都从经验实时派生**（不落库，避免不一致）。
所有读写都作用在 core.game_store 加载出来的同一个 group/user dict 上，与 gift 共享存储与锁。
"""

from __future__ import annotations

from ...core.game_store import get_user, resolve_group_id
from .config import _cfg, _error


def _ensure_player(group: dict, user_id, display_name: str = "") -> dict:
    """在通用用户记录（points/display_name）上补齐 rpg 专属字段。"""
    user = get_user(group, user_id, display_name)
    user.setdefault("exp", 0)                 # 累计经验（唯一成长落库值）
    user.setdefault("stamina", _stamina_max())  # 当前精力
    user.setdefault("stamina_date", "")       # 上次精力回满日期
    user.setdefault("fortune", "")            # 今日运势 key
    user.setdefault("fortune_date", "")       # 今日运势抽取日期
    user.setdefault("last_fortune", "")       # 最近一次运势 key（大凶→大吉修正用）
    user.setdefault("no_lucky_streak", 0)     # 连续未出「吉以上」天数（保底用）
    user.setdefault("inventory", {})          # 背包：{道具名: 数量}
    return user


# ==================== 精力 ====================

def _stamina_max() -> int:
    return int(_cfg("stamina", {}).get("max", 100))


def _stamina_cost() -> int:
    return int(_cfg("stamina", {}).get("cost_per_hunt", 20))


def _refill_stamina(user: dict, today: str) -> None:
    """每日懒回满：跨天则把精力重置为上限（仿 gift 的 steal_date 重置范式）。"""
    if user.get("stamina_date") != today:
        user["stamina"] = _stamina_max()
        user["stamina_date"] = today


# ==================== 经验 / 等级 / 战力（全部从 exp 派生） ====================

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
    """返回面板所需进度信息：{level, exp, into, span, to_next}。"""
    base = _level_base()
    exp = max(0, int(exp))
    level = _level_of(exp)
    cur_floor = _cum_exp(level, base)
    next_floor = _cum_exp(level + 1, base)
    return {
        "level": level,
        "exp": exp,
        "into": exp - cur_floor,          # 当前等级内已积累
        "span": next_floor - cur_floor,   # 当前等级跨度（= base*level）
        "to_next": next_floor - exp,      # 距升级还差
    }


def _power_for_level(level: int) -> int:
    p = _cfg("power", {})
    return int(p.get("base_power", 10)) + int(level) * int(p.get("power_per_level", 5))


def _combat_power(user: dict) -> int:
    """战力：由经验推等级，再由等级推战力（后续叠加装备加成）。"""
    return _power_for_level(_level_of(int(user.get("exp", 0))))


# ==================== 群上下文校验（与 gift 同范式，用 rpg 文案） ====================

def _resolve_group(event) -> tuple[str | None, str | None]:
    """返回 (group_id, 拒绝消息)。私聊给提示；非白名单群静默忽略。"""
    group_id, is_private = resolve_group_id(event)
    if group_id is None:
        return None, (_error("private_only") if is_private else None)
    return group_id, None
