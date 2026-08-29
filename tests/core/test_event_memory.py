from unittest import mock

import pytest

from nonebot_plugin_akito.core import event_memory
from nonebot_plugin_akito.core.event_memory_scoring import should_prioritize_curated
from nonebot_plugin_akito.core.retrieval import RetrievalContext, RetrievalResult


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


@pytest.mark.asyncio
async def test_retrieve_event_memories_keeps_evidence_and_confidence_filter(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = await event_memory.retrieve_event_memories("还记得冬弥给彰人准备生日惊喜吗")

    assert result.status == "hit"
    assert result.candidates == ["akito-toya-test-001"]
    assert result.confidences == ["high"]
    assert result.hits[0].evidence[0]["dialogue"] == "彰人：谢了。"
    assert result.evidence_units == []
    assert result.top_score >= 3.0
    assert result.candidate_count == 1


@pytest.mark.asyncio
async def test_retrieve_event_memories_abstains_without_specific_event_cues(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = await event_memory.retrieve_event_memories("你还记得那次吗？")

    assert result.status == "no_hit"
    assert result.reason == "insufficient_event_cues"
    assert result.candidates == []


@pytest.mark.asyncio
async def test_retrieve_event_memories_abstains_on_weak_overlap(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    result = await event_memory.retrieve_event_memories("你是不是挺开心的？")

    assert result.status == "no_hit"
    assert result.reason in {"no_relevant_event", "low_score"}
    assert result.candidates == []


@pytest.mark.asyncio
async def test_shadow_mode_retrieves_without_injecting_prompt_text(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    text, result = await event_memory.build_event_memory_context("生日惊喜", mode="shadow")

    assert text == ""
    assert result.status == "hit"
    assert result.candidates


@pytest.mark.asyncio
async def test_disabled_mode_is_explicitly_observable(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())

    text, result = await event_memory.build_event_memory_context("生日惊喜", mode="off")

    assert text == ""
    assert result.status == "disabled"
    assert result.reason == "m2_disabled"


@pytest.mark.asyncio
async def test_format_event_memory_context_keeps_bilingual_evidence_and_grounded_style(monkeypatch):
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

    text, result = await event_memory.build_event_memory_context("生日惊喜", mode="canary")

    assert result.status == "hit"
    assert "原始台词证据：彰人：谢了。" in text
    assert "日文原文对照：冬弥: 誕生日" in text
    assert "口吻参考（只借鉴表达方式，不新增事实）：少得意忘形了。……不过，谢了。" in text
    assert "这条没有证据引用" not in text


@pytest.mark.asyncio
async def test_format_event_memory_context_renders_multiple_joint_units(monkeypatch):
    asset = _asset()
    asset["events"][0]["evidence"] = [
        {"context": "冬弥回想起两人组队。", "dialogue": "彰人：一起唱吧。"},
        {"context": "冬弥准备了新的音轨。", "dialogue": "彰人：这首歌很棒。"},
        {"context": "冬弥决定继续努力。", "dialogue": "彰人：别输给我。"},
        {"context": "不应展示的更远片段。", "dialogue": "彰人：略。"},
    ]
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    text, result = await event_memory.build_event_memory_context("生日惊喜", mode="canary")

    assert result.status == "hit"
    assert "共同经历单元 1" in text
    assert "共同经历单元 2" in text
    assert "共同经历单元 3" in text
    assert "不应展示的更远片段" not in text


def test_format_event_memory_context_distinguishes_reviewed_and_generated_evidence():
    reviewed = event_memory.EventMemoryHit(
        event_id="reviewed",
        title="",
        summary="冬弥把完成的曲子交给彰人试听。",
        category="",
        confidence="high",
        score=1.0,
        evidence=({"context": "两人确认编曲。", "dialogue": "冬弥：请听听看。"},),
        source_kind="curated_story",
        review_status="reviewed",
    )
    generated = event_memory.EventMemoryHit(
        event_id="generated",
        title="",
        summary="两人谈到了练习。",
        category="",
        confidence="high",
        score=0.8,
        evidence=({"context": "练习期间。", "dialogue": "彰人：再来一次。"},),
        source_kind="legacy_script",
        review_status="generated",
    )

    text = event_memory.format_event_memory_context(
        event_memory.EventMemoryResult(status="hit", hits=(reviewed, generated))
    )

    assert "已审核原作共同经历" in text
    assert "已审核概括：冬弥把完成的曲子交给彰人试听。" in text
    assert "原作脚本自动整理片段（未人工复核" in text
    assert "候选概括（以原始情境和台词为准）：两人谈到了练习。" in text


@pytest.mark.asyncio
async def test_retrieve_event_memories_ranks_relevant_joint_unit_first(monkeypatch):
    asset = _asset()
    asset["events"][0]["keywords"].append("音轨")
    asset["events"][0]["evidence"] = [
        {"record_index": 31, "context": "冬弥回想起两人组队。", "dialogue": "彰人：一起唱吧。"},
        {"record_index": 83, "context": "冬弥制作了新的音轨，希望让彰人听。", "dialogue": "彰人：这首音轨很棒。"},
    ]
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    result = await event_memory.retrieve_event_memories("冬弥做的音轨怎么样")

    assert result.status == "hit"
    assert "音轨" in result.hits[0].evidence[0]["context"]
    assert result.evidence_units[0] == "akito-toya-test-001:83"


@pytest.mark.asyncio
async def test_lexical_filter_does_not_pad_fever_hit_with_mushroom_scene(monkeypatch):
    asset = {
        "events": [
            {
                "event_id": "fever",
                "source_kind": "curated_story",
                "review_status": "reviewed",
                "summary": "冬弥发烧后仍坚持参加与Embers的对决，彰人约定撑不住就阻止他。",
                "category": "冬弥·彰冬",
                "topics": ["身体不适", "坚持演出"],
                "keywords": ["发烧", "Embers", "对决"],
                "confidence": "high",
                "evidence": [{"context": "冬弥发烧仍想登台。", "dialogue": "彰人：撑不住我就阻止你。"}],
            },
            {
                "event_id": "mushroom",
                "source_kind": "legacy_script",
                "review_status": "generated",
                "title": "冬弥发现对手使用道具蘑菇",
                "summary": "冬弥在游戏对决里发现对手使用蘑菇道具。",
                "category": "冬弥·彰冬",
                "topics": ["游戏"],
                "keywords": ["对决", "冬弥", "彰人"],
                "confidence": "high",
                "evidence": [{"context": "游戏比赛。", "dialogue": "彰人：那是什么？"}],
            },
        ]
    }
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    result = await event_memory.retrieve_event_memories(
        "冬弥发烧还坚持和Embers对决那次",
        top_k=3,
        retrieval_mode="lexical",
    )

    assert result.candidates == ["fever"]
    assert any(item.event_id == "mushroom" and not item.kept for item in result.diagnostics)


@pytest.mark.asyncio
async def test_lexical_does_not_let_weak_curated_match_shadow_stronger_legacy(monkeypatch):
    asset = _asset()
    asset["events"].append(
        {
            "event_id": "curated-general",
            "source_kind": "curated_story",
            "review_status": "reviewed",
            "summary": "冬弥回顾两人的信任与成长。",
            "category": "冬弥·彰冬",
            "topics": ["成长"],
            "keywords": ["信任"],
            "confidence": "high",
            "evidence": [{"context": "冬弥回忆往事。", "dialogue": "彰人：继续努力吧。"}],
        }
    )
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", asset)

    result = await event_memory.retrieve_event_memories("冬弥准备生日惊喜", retrieval_mode="lexical")

    assert result.candidates == ["akito-toya-test-001"]


@pytest.mark.asyncio
async def test_hybrid_failure_falls_back_to_safe_lexical(monkeypatch):
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", _asset())
    monkeypatch.setattr(
        event_memory,
        "retrieve_result",
        mock.AsyncMock(return_value=RetrievalResult(status="unavailable", ids=[], reason="index_unavailable")),
    )

    result = await event_memory.retrieve_event_memories("生日惊喜", retrieval_mode="hybrid")

    assert result.status == "hit"
    assert result.retrieval_strategy == "lexical_fallback"
    assert result.fallback_reason == "index_unavailable"


@pytest.mark.parametrize(
    ("curated_score", "expected_ids"),
    [
        (0.82, ["curated", "legacy"]),
        (0.86, ["curated"]),
    ],
)
@pytest.mark.asyncio
async def test_hybrid_uses_its_own_curated_priority_margin(monkeypatch, curated_score, expected_ids):
    curated = {
        "event_id": "curated",
        "source_kind": "curated_story",
        "review_status": "reviewed",
        "summary": "冬弥和彰人一起练习。",
        "confidence": "high",
        "evidence": [{"context": "共同练习。", "dialogue": "彰人：继续吧。"}],
    }
    legacy = {
        "event_id": "legacy",
        "source_kind": "legacy_script",
        "review_status": "generated",
        "summary": "冬弥和彰人一起练习。",
        "confidence": "high",
        "evidence": [{"context": "共同练习。", "dialogue": "彰人：再来。"}],
    }
    monkeypatch.setattr(event_memory, "EVENT_MEMORY_DB", {"events": [curated, legacy]})
    monkeypatch.setattr(
        event_memory,
        "_rank_lexical",
        lambda _query, _events: [(10.0, curated), (9.0, legacy)],
    )
    monkeypatch.setattr(
        event_memory,
        "retrieve_result",
        mock.AsyncMock(return_value=RetrievalResult(status="hit", ids=[], cosine_scores=[])),
    )
    rerank = mock.AsyncMock(return_value=[(0, curated_score), (1, 0.80)])
    monkeypatch.setattr("nonebot_plugin_akito.core.api.rerank_documents", rerank)
    ctx = RetrievalContext(original_query="一起练习", query="一起练习")

    result = await event_memory.retrieve_event_memories(
        "一起练习",
        top_k=3,
        retrieval_mode="hybrid",
        retrieval_ctx=ctx,
    )

    assert result.candidates == expected_ids


def test_curated_priority_margin_boundaries_are_explicit():
    curated = [(0.82, {"event_id": "curated"})]
    legacy = [(0.80, {"event_id": "legacy"})]

    assert not should_prioritize_curated(curated, legacy, margin=0.05)
    assert should_prioritize_curated([(0.86, curated[0][1])], legacy, margin=0.05)


def test_event_asset_validator_rejects_high_confidence_without_evidence():
    payload = {"events": [{"event_id": "bad", "confidence": "high", "evidence": []}]}

    errors = event_memory.validate_event_inventory(payload)

    assert errors
    assert "evidence" in errors[0]
