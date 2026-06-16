from __future__ import annotations

import nonebot_plugin_akito.features.impression as impression
from nonebot_plugin_akito.core.api import rescue_field


def test_rescue_impression_reply_handles_eof_truncation():
    raw = '{\n  "inner_os": "x",\n  "reply": "rescued at eof'

    assert rescue_field(raw, "reply") == "rescued at eof"


def test_parse_impression_reply_uses_eof_rescue():
    raw = '{\n  "inner_os": "x",\n  "reply": "rescued at eof'

    reply, inner_os = impression._parse_impression_reply(raw)

    assert reply == "rescued at eof"
    assert inner_os == ""
