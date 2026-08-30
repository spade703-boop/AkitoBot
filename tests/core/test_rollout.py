from nonebot_plugin_akito.core.rollout import resolve_rollout


def test_unmapped_group_keeps_safe_defaults(monkeypatch):
    monkeypatch.setenv("AKITO_M1_CONTEXT_MODE", "shadow")
    monkeypatch.setenv("AKITO_M2_MEMORY_MODE", "off")
    monkeypatch.delenv("AKITO_EXPERIMENT_GROUPS", raising=False)

    config = resolve_rollout(1001)

    assert config.arm == "default"
    assert config.m1_context_mode == "shadow"
    assert config.m2_memory_mode == "off"


def test_group_arm_resolves_independently(monkeypatch):
    monkeypatch.setenv("AKITO_EXPERIMENT_GROUPS", '{"1001":"m1", "1002":"combined"}')

    m1 = resolve_rollout(1001)
    combined = resolve_rollout("1002")

    assert (m1.m1_context_mode, m1.m2_memory_mode) == ("canary", "off")
    assert (combined.m1_context_mode, combined.m2_memory_mode) == ("canary", "canary")


def test_invalid_arm_falls_back_to_base_modes(monkeypatch):
    monkeypatch.setenv("AKITO_EXPERIMENT_GROUPS", '{"1001":"unknown"}')
    monkeypatch.setenv("AKITO_M1_CONTEXT_MODE", "on")
    monkeypatch.setenv("AKITO_M2_MEMORY_MODE", "shadow")

    config = resolve_rollout(1001)

    assert config.arm == "default"
    assert config.m1_context_mode == "on"
    assert config.m2_memory_mode == "shadow"


def test_m3_tool_mode_is_independent_and_group_scoped(monkeypatch):
    monkeypatch.setenv("AKITO_M3_TOOL_MODE", "off")
    monkeypatch.setenv("AKITO_M3_TOOL_GROUPS", '{"1001": "shadow"}')

    scoped = resolve_rollout(1001)
    other = resolve_rollout(1002)

    assert scoped.m3_tool_mode == "shadow"
    assert other.m3_tool_mode == "off"
