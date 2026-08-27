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
    assert result.evidence_units == []
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


def test_format_event_memory_context_keeps_bilingual_evidence_and_grounded_style(monkeypatch):
    asset = _asset()
    asset["events"][0]["evidence"][0].update(
        {
            "context_zh": "冬弥准备生日惊喜，彰人发现了礼物。",
            "dialogue_zh": "彰人：谢了。",
            "original_ja": "冬弥: 誕生日のサプライズを準備した。\n彰人: ……ありがとな。",
        }
    )
    asset["events"][0]["style_examples"] = [
        {"text_zh": "少得意忘形了。……不过，谢了。", "evidence_refs": [0]},
        {"text_zh": "这条没有证据引用，不应注入。"},
    ]
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    text, result = event_memory.build_event_memory_context("生日惊喜", mode="canary")

    assert result.status == "hit"
    assert "原始台词证据：彰人：谢了。" in text
    assert "日文原文对照：冬弥: 誕生日" in text
    assert "口吻参考（只借鉴表达方式，不新增事实）：少得意忘形了。……不过，谢了。" in text
    assert "这条没有证据引用" not in text


def test_format_event_memory_context_renders_multiple_joint_units(monkeypatch):
    asset = _asset()
    asset["events"][0]["evidence"] = [
        {"context": "冬弥回想起两人组队。", "dialogue": "彰人：一起唱吧。"},
        {"context": "冬弥准备了新的音轨。", "dialogue": "彰人：这首歌很棒。"},
        {"context": "冬弥决定继续努力。", "dialogue": "彰人：别输给我。"},
        {"context": "不应展示的更远片段。", "dialogue": "彰人：略。"},
    ]
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    text, result = event_memory.build_event_memory_context("生日惊喜", mode="canary")

    assert result.status == "hit"
    assert "共同经历单元 1" in text
    assert "共同经历单元 2" in text
    assert "共同经历单元 3" in text
    assert "不应展示的更远片段" not in text


def test_retrieve_event_memories_ranks_relevant_joint_unit_first(monkeypatch):
    asset = _asset()
    asset["events"][0]["keywords"].append("音轨")
    asset["events"][0]["evidence"] = [
        {"record_index": 31, "context": "冬弥回想起两人组队。", "dialogue": "彰人：一起唱吧。"},
        {"record_index": 83, "context": "冬弥制作了新的音轨，希望让彰人听。", "dialogue": "彰人：这首音轨很棒。"},
    ]
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    result = event_memory.retrieve_event_memories("冬弥做的音轨怎么样")

    assert result.status == "hit"
    assert "音轨" in result.hits[0].evidence[0]["context"]
    assert result.evidence_units[0] == "akito-toya-test-001:83"


def test_event_asset_validator_rejects_high_confidence_without_evidence():
    payload = {"events": [{"event_id": "bad", "confidence": "high", "evidence": []}]}

    errors = event_memory.validate_event_inventory(payload)

    assert errors
    assert "evidence" in errors[0]
