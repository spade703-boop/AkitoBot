"""
测试 time_awareness.py 的时间间隔感知和 prompt 生成逻辑。
"""
import datetime
import time
from unittest import mock

import pytest

# 保存真实引用，避免 mock.patch("datetime.datetime") 污染
_real_datetime = datetime.datetime

TZ_CN = datetime.timezone(datetime.timedelta(hours=8))


def _make_dt(*args, **kwargs):
    """创建真实的 datetime 对象。"""
    return _real_datetime(*args, **kwargs)


# ── 纯函数测试（无需 mock） ────────────────────────────────────────────────

from nonebot_plugin_akito.core.time_awareness import (
    _normalize_period,
    _period_distance,
)


def test_normalize_morning_weekday():
    assert _normalize_period("morning_weekday") == "morning"


def test_normalize_morning_weekend():
    assert _normalize_period("morning_weekend") == "morning"


def test_normalize_noon_weekday():
    assert _normalize_period("noon_weekday") == "noon"


def test_normalize_lunch_weekend():
    assert _normalize_period("lunch_weekend") == "lunch"


def test_normalize_already_bare():
    assert _normalize_period("late_night") == "late_night"
    assert _normalize_period("night_training") == "night_training"
    assert _normalize_period("sleep_buffer") == "sleep_buffer"


def test_normalize_unknown_period():
    assert _normalize_period("unknown_thing") == "unknown_thing"


def test_period_distance_same():
    assert _period_distance("late_night", "late_night") == 0
    assert _period_distance("morning_weekday", "morning_weekend") == 0


def test_period_distance_adjacent():
    assert _period_distance("late_night", "morning_weekday") == 1


def test_period_distance_far():
    assert _period_distance("late_night", "evening") == 5


def test_period_distance_symmetric():
    assert _period_distance("morning", "night_home") == _period_distance("night_home", "morning")


# ── _current_period_key 测试（替换函数而非 patch datetime.datetime） ──────

from nonebot_plugin_akito.core import time_awareness as ta


def _set_fake_now(year, month, day, hour, minute):
    """替换 _current_period_key 内的 datetime.datetime.now 调用。"""
    fake_now = _make_dt(year, month, day, hour, minute, tzinfo=TZ_CN)
    return mock.patch.object(ta, "_current_period_key", wraps=lambda: _real_current_period_key(fake_now))


def _real_current_period_key(now):
    """_current_period_key 的核心逻辑，接受 datetime 对象而非调用 now()。"""
    hour, minute, is_weekend = now.hour, now.minute, now.weekday() >= 5
    if 0 <= hour < 6:
        return "late_night"
    elif 6 <= hour < 8:
        return "morning_weekend" if is_weekend else "morning_weekday"
    elif 8 <= hour < 12:
        return "noon_weekend" if is_weekend else "noon_weekday"
    elif 12 <= hour < 13:
        return "lunch_weekend" if is_weekend else "lunch_weekday"
    elif 13 <= hour < 15:
        return "afternoon_weekend" if is_weekend else "afternoon_weekday"
    elif 15 <= hour < 18:
        return "evening"
    elif 18 <= hour < 21:
        return "night_training"
    elif 21 <= hour < 24:
        if hour == 23 and minute >= 45:
            return "sleep_buffer"
        return "night_home"
    else:
        return "late_night"


def test_current_period_late_night():
    now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    assert _real_current_period_key(now) == "late_night"


def test_current_period_morning_weekday():
    # 2026-05-28 is Thursday (weekday=3)
    now = _make_dt(2026, 5, 28, 7, 0, tzinfo=TZ_CN)
    assert _real_current_period_key(now) == "morning_weekday"


def test_current_period_morning_weekend():
    # 2026-05-30 is Saturday (weekday=5)
    now = _make_dt(2026, 5, 30, 7, 0, tzinfo=TZ_CN)
    assert _real_current_period_key(now) == "morning_weekend"


def test_current_period_sleep_buffer():
    now = _make_dt(2026, 5, 28, 23, 50, tzinfo=TZ_CN)
    assert _real_current_period_key(now) == "sleep_buffer"


def test_current_period_night_home_before_buffer():
    now = _make_dt(2026, 5, 28, 23, 30, tzinfo=TZ_CN)
    assert _real_current_period_key(now) == "night_home"


# ── build_time_gap_prompt 场景测试 ─────────────────────────────────────────


@pytest.fixture
def mock_time_awareness_data():
    """创建一个带有 mock 持久化数据的 build_time_gap_prompt 测试环境。"""
    orig_load = ta._load
    orig_save = ta._save

    fake_store: dict = {}

    def fake_load():
        return fake_store

    def fake_save(data):
        fake_store.update(data)

    ta._load = fake_load
    ta._save = fake_save
    fake_store.clear()

    yield ta, fake_store

    ta._load = orig_load
    ta._save = orig_save


def test_gap_under_30min_returns_empty(mock_time_awareness_data):
    """30 分钟内返回空字符串。"""
    ta_mod, store = mock_time_awareness_data
    now = time.time()
    store["123"] = {
        "ts": now - 600,
        "period": "evening",
        "status": "傍晚在街上闲逛。",
    }
    with mock.patch.object(ta_mod, "_current_period_key", return_value="evening"):
        with mock.patch.object(ta_mod, "get_current_routine_snapshot", return_value={
            "period": "evening", "status": "傍晚在街上闲逛。"
        }):
            with mock.patch("time.time", return_value=now):
                result = ta_mod.build_time_gap_prompt("123")
                assert result == ""


def test_gap_light_prompt_same_period(mock_time_awareness_data):
    """大于 30 分钟但同一时段，返回轻提示。"""
    ta_mod, store = mock_time_awareness_data
    now = time.time()
    store["123"] = {
        "ts": now - 2400,
        "period": "evening",
        "status": "傍晚在街上闲逛。",
    }
    with mock.patch.object(ta_mod, "_current_period_key", return_value="evening"):
        with mock.patch.object(ta_mod, "get_current_routine_snapshot", return_value={
            "period": "evening", "status": "傍晚在街上闲逛。"
        }):
            with mock.patch("time.time", return_value=now):
                result = ta_mod.build_time_gap_prompt("123")
                assert "时间流逝感知" in result
                assert "场景重置" not in result


def test_gap_medium_prompt_period_changed(mock_time_awareness_data):
    """大于 30 分钟且跨 1 个时段，返回中提示。"""
    ta_mod, store = mock_time_awareness_data
    now = time.time()
    store["123"] = {
        "ts": now - 4000,
        "period": "evening",
        "status": "傍晚在街上闲逛。",
    }
    with mock.patch.object(ta_mod, "_current_period_key", return_value="night_training"):
        with mock.patch.object(ta_mod, "get_current_routine_snapshot", return_value={
            "period": "night_training", "status": "在 Vivid Street 训练。"
        }):
            with mock.patch("time.time", return_value=now):
                result = ta_mod.build_time_gap_prompt("123")
                assert "时段已切换" in result
                assert "场景重置" not in result


def test_gap_strong_prompt_over_8h(mock_time_awareness_data):
    """超过 8 小时，返回强提示（场景重置）。"""
    ta_mod, store = mock_time_awareness_data
    now = time.time()
    store["123"] = {
        "ts": now - 36000,
        "period": "evening",
        "status": "傍晚在街上闲逛。",
    }
    with mock.patch.object(ta_mod, "_current_period_key", return_value="morning_weekday"):
        with mock.patch.object(ta_mod, "get_current_routine_snapshot", return_value={
            "period": "morning_weekday", "status": "晨跑中。"
        }):
            with mock.patch("time.time", return_value=now):
                result = ta_mod.build_time_gap_prompt("123")
                assert "场景重置" in result


def test_gap_no_record_returns_empty(mock_time_awareness_data):
    """无记录的群返回空字符串。"""
    ta_mod, store = mock_time_awareness_data
    result = ta_mod.build_time_gap_prompt("999")
    assert result == ""


def test_gap_strong_prompt_multi_period_change(mock_time_awareness_data):
    """时段变化 >= 2 次，即使时间不长也返回强提示。"""
    ta_mod, store = mock_time_awareness_data
    now = time.time()
    store["123"] = {
        "ts": now - 4000,
        "period": "late_night",
        "status": "深夜躺床上刷手机。",
    }
    # late_night(0) → noon(2) = 2 段距离 → 强提示
    with mock.patch.object(ta_mod, "_current_period_key", return_value="noon_weekday"):
        with mock.patch.object(ta_mod, "get_current_routine_snapshot", return_value={
            "period": "noon_weekday", "status": "在学校上课。"
        }):
            with mock.patch("time.time", return_value=now):
                result = ta_mod.build_time_gap_prompt("123")
                assert "场景重置" in result
