"""测试 commands.py 中抽出的临时记忆辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.handlers.commands as commands


def test_prune_temp_implants_removes_expired_entries():
    mem = {
        "temp_implants": [
            {"content": "还有效", "expire_at": 200.0},
            {"content": "过期了", "expire_at": 50.0},
        ]
    }

    valid = commands._prune_temp_implants(mem, now_ts=100.0)

    assert len(valid) == 1
    assert valid[0][0]["content"] == "还有效"
    assert mem["temp_implants"] == [{"content": "还有效", "expire_at": 200.0}]


def test_build_temp_memory_view_formats_remaining_time():
    result = commands._build_temp_memory_view(
        valid_implants=[({"content": "外面下雨", "expire_at": 190.0}, 190.0)],
        now_ts=100.0,
    )

    assert "当前生效的设定" in result
    assert "外面下雨" in result
    assert "1分 30秒" in result


def test_inject_temp_memory_clamps_duration_to_two_hours():
    mem = {"temp_implants": []}

    expire_time, limited = commands._inject_temp_memory(
        mem,
        content="今天要装作刚醒",
        duration=999999,
        now_ts=100.0,
    )

    assert limited is True
    assert expire_time == 7300.0
    assert mem["temp_implants"][0]["content"] == "今天要装作刚醒"
    assert mem["temp_implants"][0]["expire_at"] == 7300.0
