"""测试 event_mode.py 中抽出的 WL2 辅助函数。"""

from __future__ import annotations

from nonebot.adapters import Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent

import nonebot_plugin_akito.features.event_mode as event_mode


def test_is_allowed_wl2_event_accepts_private_and_whitelisted_group():
    private_event = Event()
    group_event_ok = GroupMessageEvent(group_id=1001)
    group_event_bad = GroupMessageEvent(group_id=9999)

    assert event_mode._is_allowed_wl2_event(private_event) is True
    assert event_mode._is_allowed_wl2_event(group_event_ok) is True
    assert event_mode._is_allowed_wl2_event(group_event_bad) is False


def test_upsert_wl2_implant_replaces_old_entry():
    mem = {
        "temp_implants": [
            {"id": "OTHER", "content": "保留"},
            {"id": "WL2", "content": "旧内容", "expire_at": 1.0},
        ]
    }

    event_mode._upsert_wl2_implant(mem, "新内容", expire_at=2.0)

    assert mem["temp_implants"] == [
        {"id": "OTHER", "content": "保留"},
        {"id": "WL2", "content": "新内容", "expire_at": 2.0},
    ]


def test_remove_wl2_implant_returns_removed_count():
    mem = {
        "temp_implants": [
            {"id": "WL2", "content": "旧内容"},
            {"id": "OTHER", "content": "保留"},
            {"id": "WL2", "content": "重复"},
        ]
    }

    removed = event_mode._remove_wl2_implant(mem)

    assert removed == 2
    assert mem["temp_implants"] == [{"id": "OTHER", "content": "保留"}]

