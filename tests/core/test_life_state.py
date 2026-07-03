"""
测试 life_state.py 的睡眠检测、节日检测、状态缓存逻辑。
"""
import datetime
import time
from unittest import mock

import pytest

# 保存真实引用，避免 mock.patch 污染
_real_datetime = datetime.datetime
_real_date = datetime.date

TZ_CN = datetime.timezone(datetime.timedelta(hours=8))
TZ_JST = datetime.timezone(datetime.timedelta(hours=9))

FAKE_DAILY_ROUTINE = {
    "late_night": [{"status": "深夜躺床上刷手机。"}],
    "morning_weekday": [{"status": "晨跑中。"}],
    "morning_weekend": [{"status": "周末赖床。"}],
    "noon_weekday": [{"status": "在学校上课。"}],
    "noon_weekend": [{"status": "在家复习。"}],
    "lunch_weekday": [{"status": "在学校食堂吃午饭。"}],
    "lunch_weekend": [{"status": "在家吃午饭。"}],
    "afternoon_weekday": [{"status": "在学校训练。"}],
    "afternoon_weekend": [{"status": "去练习室。"}],
    "evening": [{"status": "傍晚在街上闲逛。"}],
    "night_training": [{"status": "在 Vivid Street 训练。"}],
    "night_home": [{"status": "在家休息。"}],
    "sleep_buffer": [{"status": "准备睡觉。"}],
}

FAKE_SLEEP_DB = {
    "sleep_mumbles": ["……zzZ……", "……呼……"],
    "sleep_relation": ['【状态：困】\n动作：闭着眼。\n台词参考：……嗯……还行吧……'],
    "sleep_search": ['【状态：困】\n动作：查手机。\n台词参考：……给你……'],
    "sleep_toya_radar": ["（正在熟睡……）zzZ"],
    "sleep_save_img": ["……明天再存……zzZ"],
    "sleep_poke": ["正在睡觉……"],
    "sleep_inject_memory": ["……呼……zzZ"],
    "sleep_gallery_list": ["睡觉中……"],
}


def _make_dt(*args, **kwargs):
    """创建真实的 datetime，不依赖 mock 后的 datetime.datetime。"""
    return _real_datetime(*args, **kwargs)


@pytest.fixture
def patch_life_state_deps():
    """注入模拟的全局变量到 life_state 模块。"""
    import nonebot_plugin_akito.core.life_state as ls

    original_routine = ls.DAILY_ROUTINE
    original_sleep_db = ls.SLEEP_DB
    original_tz_cn = ls.TZ_CN
    original_tz_jst = ls.TZ_JST

    ls.DAILY_ROUTINE = FAKE_DAILY_ROUTINE
    ls.SLEEP_DB = FAKE_SLEEP_DB
    ls.TZ_CN = TZ_CN
    ls.TZ_JST = TZ_JST

    ls.AKITO_STATUS["current_key"] = ""
    ls.AKITO_STATUS["event_history"] = []
    ls.AKITO_STATUS["cached_content"] = ""
    ls.AKITO_STATUS["expire_time"] = 0.0

    yield ls

    ls.DAILY_ROUTINE = original_routine
    ls.SLEEP_DB = original_sleep_db
    ls.TZ_CN = original_tz_cn
    ls.TZ_JST = original_tz_jst


# ── check_sleep_status 测试 ────────────────────────────────────────────────

def test_sleep_not_applicable_daytime(patch_life_state_deps):
    """白天（10 点）不适用睡眠逻辑。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 10, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
            should_block, instruction = ls.check_sleep_status("随便聊聊")
    assert should_block is False
    assert instruction == ""


def test_sleep_midnight_ignore_high_probability(patch_life_state_deps):
    """凌晨 3 点非搜索/评价类消息，大概率被忽略。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    ignore_found = False
    for _ in range(80):
        with mock.patch("datetime.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz: fake_now
            with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
                should_block, instruction = ls.check_sleep_status("在吗")
                if should_block and instruction == "ignore":
                    ignore_found = True
                    break
    assert ignore_found, "凌晨普通消息应有一定概率被忽略"


def test_sleep_mumble_comes_from_sleep_db(patch_life_state_deps):
    """凌晨梦话应从 SLEEP_DB["sleep_mumbles"] 随机选取（不再硬编码）。"""
    ls = patch_life_state_deps
    ls.SLEEP_DB["sleep_mumbles"] = ["TEST_MUMBLE_ONLY"]
    fake_now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    # 多轮采样确保 20% 概率的 mumble 路径被触发
    for _ in range(50):
        with mock.patch("datetime.datetime") as mock_dt:
            mock_dt.now.side_effect = lambda tz: fake_now
            with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
                should_block, instruction = ls.check_sleep_status("在吗")
                if instruction == "TEST_MUMBLE_ONLY":
                    break
    else:
        pytest.fail("未从 SLEEP_DB 读取梦话")


def test_sleep_woken_up_by_search(patch_life_state_deps):
    """凌晨搜索类消息触发被叫醒营业。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
            should_block, instruction = ls.check_sleep_status("帮我搜一下天气")
    assert should_block is False
    assert "sleep_search" in instruction or "被迫营业" in instruction


def test_sleep_woken_up_by_relation_query(patch_life_state_deps):
    """凌晨评价类消息（含唤醒词+评价词）触发被叫醒问话。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
            should_block, instruction = ls.check_sleep_status("我想知道你怎么看冬弥")
    assert should_block is False
    assert "sleep_relation" in instruction or "被叫醒问话" in instruction


def test_sleep_edge_hour_0(patch_life_state_deps):
    """hour=0 仍在睡眠范围内。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 0, 30, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
            should_block, instruction = ls.check_sleep_status("晚上好")
    assert should_block is True


def test_sleep_edge_hour_6(patch_life_state_deps):
    """hour=6 不在睡眠范围内。"""
    ls = patch_life_state_deps
    fake_now = _make_dt(2026, 5, 30, 6, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.side_effect = lambda tz: fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN), mock.patch.object(ls, "TZ_JST", TZ_JST):
            should_block, instruction = ls.check_sleep_status("早上好")
    assert should_block is False


# ── is_sleeping 测试 ───────────────────────────────────────────────────────

def test_is_sleeping_during_night():
    """凌晨 3 点 is_sleeping() 返回 True。"""
    import nonebot_plugin_akito.core.life_state as ls
    fake_now = _make_dt(2026, 5, 30, 3, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN):
            assert ls.is_sleeping() is True


def test_is_sleeping_during_day():
    """上午 10 点 is_sleeping() 返回 False。"""
    import nonebot_plugin_akito.core.life_state as ls
    fake_now = _make_dt(2026, 5, 30, 10, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN):
            assert ls.is_sleeping() is False


def test_is_sleeping_edge_hour_0():
    """0:30 仍在睡眠范围内。"""
    import nonebot_plugin_akito.core.life_state as ls
    fake_now = _make_dt(2026, 5, 30, 0, 30, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN):
            assert ls.is_sleeping() is True


def test_is_sleeping_edge_hour_6():
    """6:00 不在睡眠范围内。"""
    import nonebot_plugin_akito.core.life_state as ls
    fake_now = _make_dt(2026, 5, 30, 6, 0, tzinfo=TZ_CN)
    with mock.patch("datetime.datetime") as mock_dt:
        mock_dt.now.return_value = fake_now
        with mock.patch.object(ls, "TZ_CN", TZ_CN):
            assert ls.is_sleeping() is False


# ── sleep_block 测试 ───────────────────────────────────────────────────────

def test_sleep_block_daytime_returns_empty():
    """白天 sleep_block 返回空字符串（放行）。"""
    import nonebot_plugin_akito.core.life_state as ls
    with mock.patch.object(ls, "is_sleeping", return_value=False):
        result = ls.sleep_block("sleep_poke", silent_chance=0.5)
        assert result == ""


def test_sleep_block_silent_returns_none():
    """silent_chance=1.0 时，夜间 sleep_block 返回 None（静默丢弃）。"""
    import nonebot_plugin_akito.core.life_state as ls
    with mock.patch.object(ls, "is_sleeping", return_value=True):
        with mock.patch("random.random", return_value=0.01):
            result = ls.sleep_block("sleep_poke", silent_chance=0.8)
            assert result is None


def test_sleep_block_reply_from_pool():
    """silent_chance=0.0 时，夜间 sleep_block 从 SLEEP_DB 池中返回文案。"""
    import nonebot_plugin_akito.core.life_state as ls
    pool = ["回复A", "回复B"]
    original_sleep = dict(ls.SLEEP_DB)
    ls.SLEEP_DB["test_pool"] = pool
    try:
        with mock.patch.object(ls, "is_sleeping", return_value=True):
            with mock.patch("random.random", return_value=0.9):
                with mock.patch("random.choice", return_value="回复A"):
                    result = ls.sleep_block("test_pool", silent_chance=0.0)
                    assert result == "回复A"
    finally:
        ls.SLEEP_DB.clear()
        ls.SLEEP_DB.update(original_sleep)


def test_sleep_block_fallback_when_pool_missing():
    """pool_key 不存在时使用 fallback。"""
    import nonebot_plugin_akito.core.life_state as ls
    with mock.patch.object(ls, "is_sleeping", return_value=True):
        with mock.patch("random.random", return_value=0.9):
            result = ls.sleep_block("nonexistent_key", silent_chance=0.0, fallback="兜底文案")
            assert result == "兜底文案"


def test_sleep_block_zero_silent_never_returns_none():
    """silent_chance=0.0 时永远不返回 None。"""
    import nonebot_plugin_akito.core.life_state as ls
    with mock.patch.object(ls, "is_sleeping", return_value=True):
        results = set()
        for _ in range(20):
            result = ls.sleep_block("sleep_poke", silent_chance=0.0)
            results.add(result)
        assert None not in results


# ── get_festival_buff 测试 ─────────────────────────────────────────────────

def test_festival_birthday(patch_life_state_deps):
    """11 月 12 日是东云彰人生日。"""
    ls = patch_life_state_deps
    date = _make_dt(2026, 11, 12, 10, 0)
    result = ls.get_festival_buff(date)
    assert "东云彰人生日" in result


def test_festival_regular_day_returns_empty(patch_life_state_deps):
    """普通日期返回空字符串。"""
    ls = patch_life_state_deps
    date = _make_dt(2026, 6, 15, 10, 0)
    result = ls.get_festival_buff(date)
    assert result == ""


def test_festival_evening_time_correction(patch_life_state_deps):
    """节日晚间（18-23 点）包含时间修正。"""
    ls = patch_life_state_deps
    date = _make_dt(2026, 12, 24, 20, 0)
    result = ls.get_festival_buff(date)
    assert "时间修正" in result
    assert "平安夜" in result


def test_festival_morning_no_correction(patch_life_state_deps):
    """节日早晨不包含时间修正。"""
    ls = patch_life_state_deps
    date = _make_dt(2026, 12, 24, 10, 0)
    result = ls.get_festival_buff(date)
    assert "平安夜" in result
    assert "时间修正" not in result


# ── get_daily_activity 测试 ────────────────────────────────────────────────

def test_daily_activity_returns_status(patch_life_state_deps):
    """正常返回带状态前缀的文本。"""
    ls = patch_life_state_deps
    result = ls.get_daily_activity(hour=10, weekday=2)
    assert "【当前状态】" in result


def test_daily_activity_caching_same_period(patch_life_state_deps):
    """同一时段第二次调用返回缓存结果。"""
    ls = patch_life_state_deps
    first = ls.get_daily_activity(hour=10, weekday=2)
    second = ls.get_daily_activity(hour=10, weekday=2)
    assert first == second


def test_daily_activity_period_change_clears_cache(patch_life_state_deps):
    """时段切换后缓存被刷新。"""
    ls = patch_life_state_deps
    first = ls.get_daily_activity(hour=10, weekday=2)
    ls.AKITO_STATUS["expire_time"] = 0.0
    second = ls.get_daily_activity(hour=14, weekday=2)
    assert first != second


def test_daily_activity_sleep_buffer(patch_life_state_deps):
    """23:45 进入 sleep_buffer 时段。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["current_key"] = ""
    result = ls.get_daily_activity(hour=23, weekday=2, minute=45)
    assert "【当前状态】" in result


def test_daily_activity_late_night(patch_life_state_deps):
    """凌晨 3 点是 late_night 时段。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["current_key"] = ""
    result = ls.get_daily_activity(hour=3, weekday=2)
    assert "【当前状态】" in result


# ── get_morning_run_buff 测试 ───────────────────────────────────────────────

def test_morning_run_buff_hour_6(patch_life_state_deps):
    """6 点有晨跑 buff。"""
    ls = patch_life_state_deps
    result = ls.get_morning_run_buff(6)
    assert "晨跑" in result


def test_morning_run_buff_not_morning(patch_life_state_deps):
    """非 6 点无晨跑 buff。"""
    ls = patch_life_state_deps
    assert ls.get_morning_run_buff(10) == ""
    assert ls.get_morning_run_buff(0) == ""


# ── get_sleep_buffer_buff 测试 ─────────────────────────────────────────────

def test_sleep_buffer_buff_trigger(patch_life_state_deps):
    """23:45 触发睡前准备 buff。"""
    ls = patch_life_state_deps
    result = ls.get_sleep_buffer_buff(23, 45)
    assert "睡前准备" in result


def test_sleep_buffer_buff_not_trigger(patch_life_state_deps):
    """23:44 和 22 点不触发。"""
    ls = patch_life_state_deps
    assert ls.get_sleep_buffer_buff(23, 44) == ""
    assert ls.get_sleep_buffer_buff(22, 50) == ""


# ── parse_duration_and_content 测试 ────────────────────────────────────────

def test_parse_duration_minutes(patch_life_state_deps):
    """解析分钟格式 '10m text'。"""
    ls = patch_life_state_deps
    seconds, content = ls.parse_duration_and_content("10m 这是植入记忆")
    assert seconds == 600
    assert content == "这是植入记忆"


def test_parse_duration_hours(patch_life_state_deps):
    """解析小时格式。"""
    ls = patch_life_state_deps
    seconds, content = ls.parse_duration_and_content("2h 记忆内容")
    assert seconds == 7200


def test_parse_duration_days(patch_life_state_deps):
    """解析天格式。"""
    ls = patch_life_state_deps
    seconds, content = ls.parse_duration_and_content("1d 长期记忆")
    assert seconds == 86400


def test_parse_duration_no_unit_defaults_minutes(patch_life_state_deps):
    """无单位时默认按分钟处理。"""
    ls = patch_life_state_deps
    seconds, content = ls.parse_duration_and_content("5 无单位文本")
    assert seconds == 300


def test_parse_duration_no_number(patch_life_state_deps):
    """无数字前缀时整段为内容，默认 600 秒。"""
    ls = patch_life_state_deps
    seconds, content = ls.parse_duration_and_content("纯文本记忆")
    assert seconds == 600
    assert content == "纯文本记忆"


# ── grant_safety_pass 测试 ─────────────────────────────────────────────────

def test_safety_pass_sets_future_timestamp(patch_life_state_deps):
    """grant_safety_pass 将安全期设置为未来时间。"""
    ls = patch_life_state_deps
    now = time.time()
    with mock.patch("time.time", return_value=now):
        ls.grant_safety_pass(10)
        assert ls.get_safe_until() == now + 10


# ── get_toya_anchor 测试 ───────────────────────────────────────────────────

def test_toya_anchor_copresent_period(patch_life_state_deps):
    """同框时段（night_training）+ 状态不含「冬弥」→ 声明 VBS 团队活动同框 + 禁咖啡 + 连贯锁。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["current_key"] = "night_training"
    ls.AKITO_STATUS["cached_content"] = {"status": "正在做核心力量训练。"}
    result = ls.get_toya_anchor()
    assert "VBS 团队活动" in result
    assert "正在做核心力量训练" in result
    assert "喝咖啡" in result        # 禁无关支线规则在场
    assert "连贯锁" in result


def test_toya_anchor_status_mentions_toya(patch_life_state_deps):
    """routine 文本已含「冬弥」→ 明确同框（优先于时段判断）。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["current_key"] = "noon_weekday"   # 非同框时段，但文本含冬弥应优先
    ls.AKITO_STATUS["cached_content"] = {"status": "在楼梯口遇到了冬弥。"}
    result = ls.get_toya_anchor()
    assert "此刻冬弥就和你在一起" in result
    assert "连贯锁" in result


def test_toya_anchor_generic_period(patch_life_state_deps):
    """普通时段 + 状态不含「冬弥」→ 走自洽推断规则，不声明同框在场。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["current_key"] = "noon_weekday"
    ls.AKITO_STATUS["cached_content"] = {"status": "在课堂上发呆。"}
    result = ls.get_toya_anchor()
    assert "与当前情境自洽" in result
    assert "VBS 团队活动" not in result   # 非同框时段不应声明在场
    assert "连贯锁" in result


def test_toya_anchor_empty_cache_returns_empty(patch_life_state_deps):
    """无缓存 routine → 返回空串，不污染 prompt。"""
    ls = patch_life_state_deps
    ls.AKITO_STATUS["cached_content"] = ""
    assert ls.get_toya_anchor() == ""
