"""测试 scheduled.py 中抽出的定时任务辅助函数。"""

from __future__ import annotations

from unittest import mock

import nonebot_plugin_akito.features.scheduled as scheduled


def test_clean_memory_db_prunes_expired_implants_in_place():
    memory_db = {
        "group_1": {
            "temp_implants": [
                {"content": "保留", "expire_at": 200.0},
                {"content": "过期", "expire_at": 50.0},
            ]
        },
        "group_2": {"history": []},
    }

    cleaned = scheduled._clean_memory_db(memory_db, now_ts=100.0)

    assert cleaned == 1
    assert memory_db["group_1"]["temp_implants"] == [{"content": "保留", "expire_at": 200.0}]


def test_get_scheduled_greeting_uses_defaults_and_custom_pool():
    with mock.patch.object(scheduled.random, "choice", side_effect=lambda seq: seq[0]):
        custom = scheduled._get_scheduled_greeting("morning", {"greetings": {"morning": ["起床。"]}})
        fallback = scheduled._get_scheduled_greeting("night", {"greetings": {}})

    assert custom == "起床。"
    assert fallback == "晚安。"
