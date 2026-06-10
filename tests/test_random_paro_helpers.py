"""测试 random_paro.py 中抽出的参数与限频辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.features.random_paro as random_paro


def test_parse_draw_request_extracts_count_and_directional_part():
    count, directional = random_paro._parse_draw_request("3 彰人 黑百合")

    assert count == 3
    assert directional == "彰人 黑百合"


def test_parse_draw_request_defaults_to_single_draw():
    count, directional = random_paro._parse_draw_request("冬弥 王子冬")

    assert count == 1
    assert directional == "冬弥 王子冬"


def test_resolve_directional_draw_handles_success_and_ambiguity():
    fixed_a, fixed_b, error = random_paro._resolve_directional_draw(
        "彰人 黑百",
        ["黑百合", "白骑"],
        ["王子冬"],
    )
    _fa2, _fb2, error2 = random_paro._resolve_directional_draw(
        "冬弥 王",
        ["黑百合"],
        ["王子冬", "王者冬"],
    )

    assert fixed_a == "黑百合"
    assert fixed_b is None
    assert error is None
    assert "匹配到多个条目" in error2


def test_resolve_directional_draw_rejects_unknown_prefix():
    _fixed_a, _fixed_b, error = random_paro._resolve_directional_draw(
        "别的角色 测试",
        ["黑百合"],
        ["王子冬"],
    )

    assert "请指定要固定哪一方" in error


def test_prune_draw_history_and_limit_message():
    history = random_paro._prune_draw_history([0.0, 100.0, 1700.0], now_ts=1800.0, window=1800)
    exhausted = random_paro._build_draw_limit_message(
        remaining_before=0,
        requested_count=1,
        history=[100.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )
    partial = random_paro._build_draw_limit_message(
        remaining_before=1,
        requested_count=3,
        history=[100.0, 200.0],
        now_ts=1800.0,
        draw_limit=3,
        draw_window=1800,
    )

    assert history == [100.0, 1700.0]
    assert "你已用完次数" in exhausted
    assert "仅剩 1 次" in partial
