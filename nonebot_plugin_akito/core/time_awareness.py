"""
时间流逝感知模块
---------------
追踪每个群的"最后一次 bot 回复时间 + routine 快照"，
在下次回复时按 gap 大小和时段变化数量注入时间感知文本，
营造出对话之间时间自然流逝的效果。

外部接口：
  record_bot_response(group_id)      — 发完回复后调用
  build_time_gap_prompt(group_id)    — 构建 system prompt 注入文本（gap < 30min 返回空字符串）
"""

import json
import time
import datetime
from pathlib import Path

from nonebot.log import logger

from . import TZ_CN
from .data import DAILY_ROUTINE, _DATA_SEARCH_DIRS
from .life_state import AKITO_STATUS

# ── 持久化 ──────────────────────────────────────────────────────────────
_FILENAME = "last_interactions.json"


def _get_write_path() -> Path:
    """找到第一个存在的数据目录，用于读写持久化文件。"""
    for base in _DATA_SEARCH_DIRS:
        p = Path(base)
        if p.exists():
            return p / _FILENAME
    return Path("data") / _FILENAME


def _load() -> dict:
    path = _get_write_path()
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"⚠️ [TimeAwareness] 读取 {_FILENAME} 失败: {e}")
    return {}


def _save(data: dict):
    path = _get_write_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"⚠️ [TimeAwareness] 写入 {_FILENAME} 失败: {e}")


# ── 时段工具 ─────────────────────────────────────────────────────────────
# 与 life_state.get_daily_activity 保持一致的时段顺序（去掉 weekday/weekend 区分）
_PERIOD_ORDER = [
    "late_night",       # 0–6
    "morning",          # 6–8
    "noon",             # 8–12
    "lunch",            # 12–13
    "afternoon",        # 13–15
    "evening",          # 15–18
    "night_training",   # 18–21
    "night_home",       # 21–24
]


def _normalize_period(key: str) -> str:
    """将带 _weekday/_weekend 后缀的 key 归一化为基础时段名。"""
    for base in _PERIOD_ORDER:
        if key.startswith(base):
            return base
    return key


def _period_distance(past: str, current: str) -> int:
    """计算两个时段之间隔了几格（0 = 相同时段）。"""
    p = _normalize_period(past)
    c = _normalize_period(current)
    if p == c:
        return 0
    try:
        return abs(_PERIOD_ORDER.index(c) - _PERIOD_ORDER.index(p))
    except ValueError:
        return 1  # 未知时段，保守算作变化了一次


def _current_period_key() -> str:
    """根据当前时间返回 routine key（与 get_daily_activity 逻辑完全一致）。"""
    now = datetime.datetime.now(TZ_CN)
    hour, is_weekend = now.hour, now.weekday() >= 5
    if 0 <= hour < 6:     return "late_night"
    elif 6 <= hour < 8:   return "morning_weekend" if is_weekend else "morning_weekday"
    elif 8 <= hour < 12:  return "noon_weekend" if is_weekend else "noon_weekday"
    elif 12 <= hour < 13: return "lunch_weekend" if is_weekend else "lunch_weekday"
    elif 13 <= hour < 15: return "afternoon_weekend" if is_weekend else "afternoon_weekday"
    elif 15 <= hour < 18: return "evening"
    elif 18 <= hour < 21: return "night_training"
    else:                 return "night_home"


# ── 公共接口 ─────────────────────────────────────────────────────────────

def get_current_routine_snapshot() -> dict:
    """
    轻量快照：返回 {"period": key, "status": "..."}。
    优先读 AKITO_STATUS 缓存，避免重复触发随机抽取逻辑。
    """
    key = _current_period_key()
    cached = AKITO_STATUS.get("cached_content", "")
    if AKITO_STATUS.get("current_key") == key and cached:
        status = cached.get("status", str(cached)) if isinstance(cached, dict) else str(cached)
    else:
        routine_list = DAILY_ROUTINE.get(key, [{"status": "正在发呆。"}])
        first = routine_list[0] if routine_list else {"status": "正在发呆。"}
        status = first.get("status", str(first)) if isinstance(first, dict) else str(first)
    return {"period": key, "status": status}


def record_bot_response(group_id) -> None:
    """
    bot 在某个群发完回复后调用。
    将当前时间戳和 routine 快照写入持久化文件，供下次对话计算 gap。
    """
    data = _load()
    snap = get_current_routine_snapshot()
    data[str(group_id)] = {
        "ts": time.time(),
        "period": snap["period"],
        "status": snap["status"],
    }
    _save(data)
    logger.debug(f"⏱️ [TimeAwareness] 群 {group_id} 时间戳已记录 (period={snap['period']})")


def build_time_gap_prompt(group_id) -> str:
    """
    构建时间流逝感知注入文本。

    规则：
      gap < 30min                       → 不注入（空字符串）
      gap >= 30min，同一时段              → 轻提示
      gap >= 30min，时段变化 1 次         → 中提示（场景切换）
      gap >= 8h 或时段变化 >= 2 次        → 强提示（场景重置）
    """
    data = _load()
    rec = data.get(str(group_id))
    if not rec:
        return ""

    gap = time.time() - rec.get("ts", 0)
    if gap < 1800:  # 30 分钟内，正常接话
        return ""

    past_period = rec.get("period", "")
    past_status = rec.get("status", "做某事")
    past_dt = datetime.datetime.fromtimestamp(rec["ts"], tz=TZ_CN)
    past_time_str = f"{past_dt.hour}:{past_dt.minute:02d}"

    snap = get_current_routine_snapshot()
    curr_period = snap["period"]
    curr_status = snap["status"]

    dist = _period_distance(past_period, curr_period)

    # 格式化 gap 描述
    if gap < 3600:
        gap_desc = f"约{int(gap / 60)}分钟"
    elif gap < 86400:
        gap_desc = f"约{int(gap / 3600)}小时"
    else:
        gap_desc = f"约{int(gap / 86400)}天"

    if gap >= 28800 or dist >= 2:
        # 强提示：8h+ 或跨 2+ 个时段
        return (
            "\n⏱️【时间流逝感知 · 场景重置】\n"
            f"距上次与本群互动已过去{gap_desc}"
            f"（上次 {past_time_str}，你当时{past_status}）。\n"
            f"现在你{curr_status}\n"
            "上次的对话场景已成为很久之前的事——若有人提起，以「那会儿」「之前」自然带过，"
            "不要接续上次话题，也不要主动重提。\n"
        )
    elif dist >= 1:
        # 中提示：30min+ 且跨了 1 个时段
        return (
            "\n⏱️【时间流逝感知】\n"
            f"距上次与本群互动已过去{gap_desc}，时段已切换"
            f"（上次 {past_time_str} 你在{past_status}，现在你{curr_status}）。\n"
            "上次的话题已经结束，这是新的时间段，自然地开启新话题即可。\n"
        )
    else:
        # 轻提示：30min+ 但仍在同一时段
        return (
            "\n⏱️【时间流逝感知】\n"
            f"距上次与本群互动已过去{gap_desc}（同一时段内）。"
            f"你仍然{curr_status}，"
            "但上次那轮对话已经结束，不必接续上次的话题。\n"
        )
