"""Tests for the non-invasive M1 context shadow planner."""

from nonebot_plugin_akito.core.context_orchestrator import (
    ContextOrchestrator,
    estimate_token_count,
)


def test_normalize_adds_metadata_without_changing_content():
    blocks = [{"source": "persona", "content": "彰人", "priority": "900"}]

    normalized = ContextOrchestrator().normalize(blocks)

    assert normalized[0]["source"] == "persona"
    assert normalized[0]["content"] == "彰人"
    assert normalized[0]["priority"] == 900
    assert normalized[0]["token_estimate"] == estimate_token_count("彰人")
    assert "token_estimate" not in blocks[0]


def test_shadow_selects_by_priority_and_keeps_omitted_sources_private():
    orchestrator = ContextOrchestrator(budget_tokens=3)
    report = orchestrator.shadow(
        [
            {"source": "low", "content": "1234567890", "priority": 100, "token_estimate": 3},
            {"source": "high", "content": "12", "priority": 900, "token_estimate": 1},
            {"source": "current", "content": "34", "priority": 1000, "token_estimate": 1},
        ],
        stage="main_chat",
    )

    assert report.stage == "main_chat"
    assert report.total_blocks == 3
    assert report.estimated_tokens == 5
    assert report.selected_sources == ("high", "current")
    assert report.omitted_sources == ("low",)
    assert all("content" not in item for item in [report.as_dict()])
