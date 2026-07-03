"""测试 reactions.py 中抽出的被动反应辅助函数。"""

from __future__ import annotations

import nonebot_plugin_akito.handlers.reactions as reactions


def test_has_wl2_implant_detects_mode_flag():
    assert reactions._has_wl2_implant({"temp_implants": [{"id": "WL2"}]}) is True
    assert reactions._has_wl2_implant({"temp_implants": [{"id": "OTHER"}]}) is False


def test_resolve_poke_reactions_prefers_state_then_fallback():
    from_state = reactions._resolve_poke_reactions({"poke": ["别闹。"]}, {"fallback_poke": ["默认"]})
    fallback = reactions._resolve_poke_reactions({}, {"fallback_poke": ["默认"]})

    assert from_state == ["别闹。"]
    assert fallback == ["默认"]


def test_should_skip_self_complaint_respects_sleep_safety_and_superuser_window():
    assert reactions._should_skip_self_complaint(
        sleeping=False,
        now_ts=100.0,
        safe_until=0.0,
        last_complaint=0.0,
        group_key="1001",
        superuser_times={},
    ) is True

    assert reactions._should_skip_self_complaint(
        sleeping=True,
        now_ts=100.0,
        safe_until=90.0,
        last_complaint=80.0,
        group_key="1001",
        superuser_times={"1001": 75.0},
    ) is True

    assert reactions._should_skip_self_complaint(
        sleeping=True,
        now_ts=100.0,
        safe_until=0.0,
        last_complaint=0.0,
        group_key="1001",
        superuser_times={},
    ) is False
