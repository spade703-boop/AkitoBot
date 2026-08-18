from __future__ import annotations

from nonebot_plugin_akito.core.api import rescue_field
import nonebot_plugin_akito.features.impression as impression


def test_rescue_impression_reply_handles_eof_truncation():
    raw = '{\n  "inner_os": "x",\n  "reply": "rescued at eof'

    assert rescue_field(raw, "reply") == "rescued at eof"


def test_parse_impression_candidates_uses_eof_rescue():
    raw = '{\n  "inner_os": "x",\n  "reply": "rescued at eof'

    inner_os, candidates = impression._parse_impression_candidates(raw)

    assert candidates == ["rescued at eof"]
    assert inner_os == ""
