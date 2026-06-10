"""测试 verify.py 中抽出的名单辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.features.verify as verify


def test_format_wait_time_covers_common_ranges():
    assert verify._format_wait_time(5) == "刚刚"
    assert verify._format_wait_time(60) == "1分钟前"
    assert verify._format_wait_time(3660) == "1小时1分钟前"
    assert verify._format_wait_time(90000) == "1天1小时前"


def test_split_qq_tokens_separates_valid_and_invalid_parts():
    valid, invalid = verify._split_qq_tokens("12345 abc 67890 12x")

    assert valid == ["12345", "67890"]
    assert invalid == ["abc", "12x"]


def test_append_unique_ids_adds_new_entries_and_marks_duplicates():
    queue = [{"uid": "10001", "join_time": 1.0}]

    success, duplicates = verify._append_unique_ids(queue, ["10001", "10002", "10002"], now_ts=50.0)

    assert success == ["10002"]
    assert duplicates == ["10001", "10002"]
    assert queue == [
        {"uid": "10001", "join_time": 1.0},
        {"uid": "10002", "join_time": 50.0},
    ]


def test_remove_ids_from_queue_updates_list_in_place():
    queue = [
        {"uid": "10001", "join_time": 1.0},
        {"uid": "10002", "join_time": 2.0},
    ]

    removed = verify._remove_ids_from_queue(queue, ["10002", "99999"])

    assert removed == 1
    assert queue == [{"uid": "10001", "join_time": 1.0}]


def test_append_hold_entries_keeps_reason_and_marks_duplicates():
    queue = [{"uid": "10001", "join_time": 1.0, "reason": "旧原因"}]

    success, duplicates = verify._append_hold_entries(
        queue,
        ["10002", "10001", "10002"],
        reason="请假中",
        now_ts=88.0,
    )

    assert success == ["10002"]
    assert duplicates == ["10001", "10002"]
    assert queue == [
        {"uid": "10001", "join_time": 1.0, "reason": "旧原因"},
        {"uid": "10002", "join_time": 88.0, "reason": "请假中"},
    ]
