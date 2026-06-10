"""测试 impression.py 中抽出的目标判断与回复解析辅助函数。"""

from __future__ import annotations

import types

import nonebot_plugin_akito.features.impression as impression


class _Seg:
    def __init__(self, seg_type: str, data: dict):
        self.type = seg_type
        self.data = data


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


def test_build_impression_history_text_reverses_rows_into_prompt_order():
    rows = [("第二句",), ("第一句",)]

    result = impression._build_impression_history_text(rows, "小明")

    assert result == "【小明】: 第一句\n【小明】: 第二句"


def test_parse_impression_reply_rescues_broken_reply_field():
    raw = '{"inner_os":"有点熟","reply":"对小明的印象是还算活跃","bad":"没关上}'

    reply, inner_os = impression._parse_impression_reply(raw)

    assert reply == "对小明的印象是还算活跃"
    assert inner_os == ""


def test_should_skip_random_chat_blocks_prefix_and_keywords():
    assert impression._should_skip_random_chat("/help") is True
    assert impression._should_skip_random_chat("开始进货 表情") is True
    assert impression._should_skip_random_chat("你好") is False
