from nonebot_plugin_akito.core import event_memory


def _asset():
    return {
        "events": [
            {
                "event_id": "akito-toya-test-001",
                "title": "冬弥和伙伴们为彰人策划生日惊喜",
                "summary": "冬弥参与准备彰人的生日惊喜，彰人嘴上抱怨但接受了好意。",
                "category": "冬弥·彰冬",
                "topics": ["生日", "惊喜"],
                "confidence": "high",
                "keywords": ["生日", "惊喜", "冬弥", "彰人"],
                "evidence": [{"context": "生日当天策划惊喜", "dialogue": "彰人：谢了。"}],
            },
            {
                "event_id": "akito-toya-test-002",
                "title": "低置信度旁支",
                "summary": "只有零散提及。",
                "category": "其他",
                "topics": [],
                "confidence": "low",
                "keywords": ["生日"],
                "evidence": [],
            },
        ]
    }


def test_retrieve_event_memories_keeps_evidence_and_confidence_filter(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = event_memory.retrieve_event_memories("还记得冬弥给彰人准备生日惊喜吗")

    assert result.status == "hit"
    assert result.candidates == ["akito-toya-test-001"]
    assert result.confidences == ["high"]
    assert result.hits[0].evidence[0]["dialogue"] == "彰人：谢了。"
    assert result.top_score >= 3.0
    assert result.candidate_count == 1


def test_retrieve_event_memories_abstains_without_specific_event_cues(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = event_memory.retrieve_event_memories("你还记得那次吗？")

    assert result.status == "no_hit"
    assert result.reason == "insufficient_event_cues"
    assert result.candidates == []


def test_retrieve_event_memories_abstains_on_weak_overlap(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = event_memory.retrieve_event_memories("你是不是挺开心的？")

    assert result.status == "no_hit"
    assert result.reason in {"no_relevant_event", "low_score"}
    assert result.candidates == []


def test_shadow_mode_retrieves_without_injecting_prompt_text(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    text, result = event_memory.build_event_memory_context("生日惊喜", mode="shadow")

    assert text == ""
    assert result.status == "hit"
    assert result.candidates


def test_disabled_mode_is_explicitly_observable(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    text, result = event_memory.build_event_memory_context("生日惊喜", mode="off")

    assert text == ""
    assert result.status == "disabled"
    assert result.reason == "m2_disabled"


def test_event_asset_validator_rejects_high_confidence_without_evidence():
    payload = {"events": [{"event_id": "bad", "confidence": "high", "evidence": []}]}

    errors = event_memory.validate_event_inventory(payload)

    assert errors
    assert "evidence" in errors[0]
