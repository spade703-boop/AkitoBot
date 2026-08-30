from __future__ import annotations

from datetime import date, datetime

from nonebot_plugin_akito.core import TZ_CN
from nonebot_plugin_akito.features.daily_wordcloud import analysis


def _simple_cutter(text: str):
    return text.split()


def test_build_report_counts_repeated_words_and_contributors():
    rows = [
        ("u1", "阿一", "akito akito coffee", 1),
        ("u2", "阿二", "akito coffee coffee coffee", 2),
        ("u1", "新名片", "coffee", 3),
    ]

    report = analysis.build_report(
        "1001",
        date(2026, 8, 29),
        rows,
        blocked_words=set(),
        cutter=_simple_cutter,
        stopwords=set(),
    )

    assert report["message_count"] == 3
    assert report["participant_count"] == 2
    assert report["frequencies"][:2] == [["coffee", 5], ["akito", 3]]
    assert report["top_words"][0]["contributors"] == [
        {"user_id": "u2", "nickname": "阿二", "count": 3},
        {"user_id": "u1", "nickname": "新名片", "count": 2},
    ]
    assert report["message_volume"] == [
        {"user_id": "u1", "nickname": "新名片", "count": 2},
        {"user_id": "u2", "nickname": "阿二", "count": 1},
    ]


def test_build_report_message_volume_keeps_all_users_for_rendering_top_seven_plus_other():
    rows = [(f"u{index}", f"用户{index}", "hello", index) for index in range(1, 9)]
    rows.extend([("u8", "用户8", "hello", 100), ("u8", "用户8", "hello", 101)])

    report = analysis.build_report(
        "1001",
        date(2026, 8, 29),
        rows,
        blocked_words=set(),
        cutter=_simple_cutter,
        stopwords=set(),
    )

    assert report["message_count"] == 10
    assert report["message_volume"][0] == {"user_id": "u8", "nickname": "用户8", "count": 3}
    assert len(report["message_volume"]) == 8


def test_build_report_applies_blocked_words_before_all_rankings():
    report = analysis.build_report(
        "1001",
        date(2026, 8, 29),
        [("u1", "阿一", "akito akito coffee", 1)],
        blocked_words={"akito"},
        cutter=_simple_cutter,
        stopwords=set(),
    )

    assert report["frequencies"] == [["coffee", 1]]
    assert [item["word"] for item in report["top_words"]] == ["coffee"]


def test_report_message_stats_exclude_rows_with_no_remaining_tokens():
    report = analysis.build_report(
        "1001",
        date(2026, 8, 29),
        [("u1", "阿一", "blocked", 1), ("u2", "阿二", "coffee", 2)],
        blocked_words={"blocked"},
        cutter=_simple_cutter,
        stopwords=set(),
    )

    assert report["message_count"] == 1
    assert report["participant_count"] == 1


def test_token_filtering_and_blocked_argument_normalization():
    tokens = analysis.extract_tokens(
        "Hello HELLO 123 https://example.com [CQ:image,file=x] 啊 😀",
        cutter=_simple_cutter,
        stopwords={"啊"},
        blocked_words={"hello"},
    )

    assert tokens == []
    assert analysis.parse_blocked_word_arguments(" AKITO，coffee akito 123 😀 ") == ["akito", "coffee"]


def test_excluded_user_argument_normalization():
    assert analysis.parse_excluded_user_arguments(" 834285229，123456789 834285229 abc 123 ") == [
        "834285229",
        "123456789",
    ]


def test_registered_bot_commands_are_filtered_with_arguments(monkeypatch):
    monkeypatch.setattr(analysis, "registered_command_prefixes", lambda: ("签到", "今日打怪"))

    assert analysis.is_bot_command_text("签到") is True
    assert analysis.is_bot_command_text("签到 现在") is True
    assert analysis.is_bot_command_text("/任意指令") is True
    assert analysis.is_bot_command_text("签到啦") is False


def test_recordable_text_rejects_commands_links_and_non_text_noise():
    assert analysis.is_recordable_text("普通聊天") is True
    assert analysis.is_recordable_text("hello") is True
    assert analysis.is_recordable_text("/群聊词云") is False
    assert analysis.is_recordable_text("https://example.com") is False
    assert analysis.is_recordable_text("12345 😀") is False


def test_date_bounds_use_china_timezone_and_half_open_day():
    start, end = analysis.date_bounds(date(2026, 8, 29))

    assert datetime.fromtimestamp(start, TZ_CN).isoformat() == "2026-08-29T00:00:00+08:00"
    assert end - start == 86400


def test_tied_words_and_users_have_deterministic_order():
    report = analysis.build_report(
        "1001",
        date(2026, 8, 29),
        [
            ("u2", "乙", "beta alpha", 1),
            ("u1", "甲", "alpha beta", 2),
        ],
        blocked_words=set(),
        cutter=_simple_cutter,
        stopwords=set(),
    )

    assert report["frequencies"] == [["alpha", 2], ["beta", 2]]
    assert [person["user_id"] for person in report["top_words"][0]["contributors"]] == ["u1", "u2"]
