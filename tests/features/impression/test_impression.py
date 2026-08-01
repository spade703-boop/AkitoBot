"""测试 impression.py 中抽出的目标判断与回复解析辅助函数。"""

from __future__ import annotations

import datetime
import sqlite3
import types

import nonebot_plugin_akito.features.impression as impression


class _Seg:
    def __init__(self, seg_type: str, data: dict):
        self.type = seg_type
        self.data = data


def _message_row(
    row_id: int,
    user_id: str,
    nickname: str,
    content: str,
    message_id: str,
    timestamp: str,
):
    return (row_id, user_id, nickname, content, message_id, timestamp)


def test_resolve_impression_target_defaults_to_sender():
    event = types.SimpleNamespace(
        user_id="123",
        sender=types.SimpleNamespace(card="", nickname="测试用户"),
        original_message=[],
    )

    target_id, target_name, is_querying_other, is_querying_bot = impression._resolve_impression_target(event, "999")

    assert target_id == "123"
    assert target_name == "测试用户"
    assert is_querying_other is False
    assert is_querying_bot is False


def test_resolve_impression_target_detects_querying_bot():
    event = types.SimpleNamespace(
        user_id="123",
        sender=types.SimpleNamespace(card="群名片", nickname="测试用户"),
        original_message=[_Seg("at", {"qq": "999"})],
    )

    target_id, target_name, is_querying_other, is_querying_bot = impression._resolve_impression_target(event, "999")

    assert target_id == "999"
    assert target_name == "群名片"
    assert is_querying_other is True
    assert is_querying_bot is True


def test_exact_impression_request_rejects_command_prefix_sentences():
    exact_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象",
        original_message=[_Seg("text", {"text": "我的印象"})],
    )
    querying_other_event = types.SimpleNamespace(
        get_plaintext=lambda: "群印象 ",
        original_message=[_Seg("text", {"text": "群印象 "}), _Seg("at", {"qq": "123"})],
    )
    sentence_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象他是一个什么样的人",
        original_message=[_Seg("text", {"text": "我的印象他是一个什么样的人"})],
    )
    image_event = types.SimpleNamespace(
        get_plaintext=lambda: "我的印象",
        original_message=[_Seg("text", {"text": "我的印象"}), _Seg("image", {"file": "x"})],
    )

    assert impression._is_exact_impression_request_message(exact_event) is True
    assert impression._is_exact_impression_request_message(querying_other_event) is True
    assert impression._is_exact_impression_request_message(sentence_event) is False
    assert impression._is_exact_impression_request_message(image_event) is False


def test_build_impression_history_text_reverses_rows_into_prompt_order():
    rows = [
        _message_row(2, "123", "小明", "第二句", "m2", "2026-08-01 01:00:00"),
        _message_row(1, "123", "小明", "第一句", "m1", "2026-08-01 00:00:00"),
    ]

    result = impression._build_impression_history_text(rows, "小明")

    assert result == "[08-01 08:00]【小明】: 第一句\n[08-01 09:00]【小明】: 第二句"


def test_load_impression_material_builds_history_context_away_from_command():
    connection = sqlite3.connect(":memory:")
    connection.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY,
            group_id TEXT,
            user_id TEXT,
            nickname TEXT,
            content TEXT,
            message_id TEXT,
            timestamp DATETIME
        )
        """
    )
    rows = [
        (1, "1", "other", "群友A", "明天不是要演出吗", "m1", "2026-08-01 12:00:00"),
        (2, "1", "target", "小明", "还有两首没练完", "m2", "2026-08-01 12:01:00"),
        (3, "1", "other", "群友A", "你今天又不睡？", "m3", "2026-08-01 12:02:00"),
        (4, "1", "target", "小明", "六点起来继续", "m4", "2026-08-01 12:03:00"),
        (5, "1", "other", "群友A", "行吧，加油", "m5", "2026-08-01 12:04:00"),
        (6, "1", "target", "小明", "群印象", "cmd-current", "2026-08-02 00:00:00"),
        (7, "1", "target", "小明", "较早的完整发言", "old", "2026-07-01 00:00:00"),
    ]
    connection.executemany(
        "INSERT INTO messages (id, group_id, user_id, nickname, content, message_id, timestamp) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        rows,
    )

    history_rows, blocks = impression._load_impression_material(
        connection,
        group_id="1",
        target_id="target",
        current_message_id="cmd-current",
        now=datetime.datetime(2026, 8, 2, tzinfo=datetime.timezone.utc),
    )

    assert "群印象" not in [row[3] for row in history_rows]
    assert "较早的完整发言" in [row[3] for row in history_rows]
    assert len(blocks) == 1
    assert [row[3] for row in blocks[0]] == [
        "明天不是要演出吗",
        "还有两首没练完",
        "你今天又不睡？",
        "六点起来继续",
        "行吧，加油",
    ]
    connection.close()


def test_merge_context_windows_keeps_latest_six_blocks():
    windows = [
        [_message_row(index, "target", "小明", f"发言{index}", f"m{index}", f"2026-08-01 {index:02d}:00:00")]
        for index in range(1, 8)
    ]

    blocks = impression._merge_context_windows(windows)

    assert len(blocks) == 6
    assert blocks[0][0][3] == "发言2"
    assert blocks[-1][0][3] == "发言7"


def test_resolve_wl2_overlay_uses_active_stored_content():
    memory = {
        "temp_implants": [
            {"id": "WL2", "content": "过期世界线", "expire_at": 50.0},
            {"id": "WL2", "content": "当前世界线", "expire_at": 200.0},
        ]
    }

    is_active, overlay = impression._resolve_wl2_overlay(memory, now_ts=100.0)

    assert is_active is True
    assert overlay == "当前世界线"


def test_validate_impression_result_requires_target_evidence():
    valid, reason = impression._validate_impression_result(
        reply="对小明的印象是挺能熬的，明明还没练完，倒是已经把明早的安排想好了。",
        evidence=["还有两首没练完", "六点起来继续"],
        focus="对练习的执着",
        target_name="小明",
        target_evidence_source="还有两首没练完\n六点起来继续",
    )
    invalid, invalid_reason = impression._validate_impression_result(
        reply="对小明的印象是挺能熬的。",
        evidence=["还有两首没练完", "明天不是要演出吗"],
        focus="",
        target_name="小明",
        target_evidence_source="还有两首没练完\n六点起来继续",
    )

    assert valid is True
    assert reason == ""
    assert invalid is False
    assert "evidence 不在目标发言中" in invalid_reason


def test_parse_impression_result_reads_evidence_and_focus():
    raw = (
        '{"inner_os":"确实挺拼","evidence":["还有两首没练完","六点起来继续"],'
        '"focus":"对练习的执着","reply":"对小明的印象是对练习还挺认真的。"}'
    )

    reply, inner_os, evidence, focus = impression._parse_impression_result(raw)

    assert reply == "对小明的印象是对练习还挺认真的。"
    assert inner_os == "确实挺拼"
    assert evidence == ["还有两首没练完", "六点起来继续"]
    assert focus == "对练习的执着"


def test_parse_impression_reply_rescues_broken_reply_field():
    raw = '{"inner_os":"有点熟","reply":"对小明的印象是还算活跃","bad":"没关上}'

    reply, inner_os = impression._parse_impression_reply(raw)

    assert reply == "对小明的印象是还算活跃"
    assert inner_os == ""


def test_should_skip_random_chat_blocks_prefix_and_keywords():
    assert impression._should_skip_random_chat("/help") is True
    assert impression._should_skip_random_chat("开始进货 表情") is True
    assert impression._should_skip_random_chat("你好") is False


def test_grounded_random_reply_requires_anchor_from_current_message():
    assert impression._is_grounded_random_reply("今天又下雨了", "下雨", "出门记得带伞。") is True
    assert impression._is_grounded_random_reply("今天又下雨了", "昨天考试", "考得怎么样？") is False
    assert impression._is_grounded_random_reply("今天又下雨了", "雨", "带伞。") is False


def test_grounded_random_reply_allows_silence_without_anchor():
    assert impression._is_grounded_random_reply("哈哈哈哈", "", "") is True
