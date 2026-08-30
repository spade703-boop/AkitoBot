from nonebot_plugin_akito.core.ambiguity_guard import (
    detect_ambiguity_signals,
    evaluate_ambiguity_guard,
    is_ambiguity_guard_enabled,
)


def test_high_confidence_reference_patterns_trigger_guard():
    for message in ("他当时怎么说的？", "那次后来怎么样？", "后来呢", "你还记得那次吗？"):
        decision = evaluate_ambiguity_guard(message, enabled=True)

        assert decision.triggered is True
        assert decision.reason == "ambiguous_event_reference"
        assert decision.clarification
        assert decision.signals.trace_names()


def test_explicit_context_and_state_bypass_guard():
    cases = (
        ("你们以前发生过什么", {}),
        ("文化祭那次发生了什么", {}),
        ("那次后来怎么样？", {"has_history": True}),
        ("那次后来怎么样？", {"has_image": True}),
        ("那次后来怎么样？", {"has_valid_temporary_state": True}),
        ("那次后来怎么样？", {"explicit_web_intent": True}),
    )
    for message, kwargs in cases:
        assert evaluate_ambiguity_guard(message, enabled=True, **kwargs).triggered is False


def test_guard_switch_defaults_on_and_supports_off(monkeypatch):
    monkeypatch.delenv("AKITO_AMBIGUITY_GUARD", raising=False)
    assert is_ambiguity_guard_enabled() is True
    monkeypatch.setenv("AKITO_AMBIGUITY_GUARD", "off")
    assert is_ambiguity_guard_enabled() is False
    assert evaluate_ambiguity_guard("后来呢").triggered is False


def test_signal_detection_does_not_retain_original_text():
    signals = detect_ambiguity_signals("那次后来怎么样？我的秘密是123")

    assert signals.event_references
    assert signals.is_follow_up
    assert "秘密" not in repr(signals)
    assert "123" not in repr(signals)
