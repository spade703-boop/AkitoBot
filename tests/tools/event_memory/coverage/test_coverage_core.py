from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.event_memory.coverage.core import (
    PARTICIPANT_SCOPE_DESCRIPTIONS,
    TIMELINE_STAGE_DESCRIPTIONS,
    CoverageError,
    CoverageStore,
    canonicalize_story_url,
    find_adjacent_events,
)


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _store(tmp_path: Path) -> CoverageStore:
    data_dir = tmp_path / "data"
    _write(data_dir / "content" / "akito_event_memories.json", {"events": []})
    return CoverageStore(
        data_dir=data_dir,
        catalog_path=tmp_path / "coverage" / "catalog.json",
        eval_drafts_path=tmp_path / "coverage" / "eval_drafts.json",
        eval_set_path=tmp_path / "retrieval" / "eval_set.json",
        report_path=tmp_path / "docs" / "COVERAGE_REPORT.md",
    )


def _published_source(store: CoverageStore) -> dict:
    url = "https://pjsk.moe/zh-cn/story/event/193/6/"
    _write(
        store.data_dir / "content" / "akito_event_memories.json",
        {
            "events": [
                {
                    "event_id": "event-target",
                    "source_kind": "curated_story",
                    "source": {"url": url},
                    "summary": "冬弥发烧时仍想参加对决，彰人担心并约定及时阻止。",
                    "topics": ["发烧", "对决", "关心"],
                    "relationship_tags": ["搭档信任"],
                },
                {
                    "event_id": "event-adjacent",
                    "source_kind": "legacy_script",
                    "topics": ["对决", "关心"],
                    "relationship_tags": ["搭档信任"],
                },
            ]
        },
    )
    store.sync()
    return store.load_catalog()["sources"][0]


def _valid_cases() -> list[dict]:
    return [
        {"case_type": "positive", "query": "冬弥发烧还坚持对决那次？"},
        {"case_type": "positive", "query": "你当时为什么没有直接让冬弥回去？"},
        {
            "case_type": "adjacent",
            "query": "你担心冬弥却还是让他上台的那回？",
            "forbidden_event_ids": ["event-adjacent"],
        },
        {"case_type": "negative", "query": "冬弥发烧时你完全没发现吧？"},
        {"case_type": "negative", "query": "你因为冬弥发烧把对手的舞台拆了？"},
    ]


def test_canonicalize_story_url_removes_query_and_keeps_locale_route() -> None:
    canonical, route_type = canonicalize_story_url("https://www.pjsk.moe/zh-cn/story/event/193/6?from=test#x")

    assert canonical == "https://www.pjsk.moe/zh-cn/story/event/193/6/"
    assert route_type == "event"


@pytest.mark.parametrize("url", ["http://pjsk.moe/zh-cn/story/event/1/1/", "https://example.com/story/event/1/1/"])
def test_canonicalize_story_url_rejects_unsafe_sources(url: str) -> None:
    with pytest.raises(CoverageError):
        canonicalize_story_url(url)


def test_sync_tracks_known_sources_without_copying_dialogue(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)

    assert entry["workflow_status"] == "published"
    assert entry["event_ids"] == ["event-target"]
    assert entry["origins"] == ["published"]
    assert store.summary()["processed_sources"] == 1
    assert store.summary()["published_sources"] == 1
    assert "冬弥发烧时仍想参加" not in store.catalog_path.read_text(encoding="utf-8")
    assert "不代表全游戏剧情覆盖率" in store.report_path.read_text(encoding="utf-8")


def test_sync_distinguishes_full_scene_and_target_segment_speakers(tmp_path: Path) -> None:
    store = _store(tmp_path)
    url = "https://pjsk.moe/zh-cn/story/event/71/8/"
    _write(
        store.data_dir / "event_memory" / "story_import" / "drafts" / "story-0123456789abcdef.json",
        {
            "draft_id": "story-0123456789abcdef",
            "source": {"canonical_url": url},
            "story": {"episode_title": "场景"},
            "actions": [
                {"index": 0, "speaker_zh": "杏"},
                {"index": 1, "speaker_zh": "彰人"},
                {"index": 2, "speaker_zh": "冬弥"},
            ],
            "target_segments": [{"target_speakers": ["akito", "toya"]}],
            "review": {"status": "draft"},
        },
    )

    store.sync()
    entry = store.load_catalog()["sources"][0]

    assert entry["source_speakers"] == ["冬弥", "彰人", "杏"]
    assert entry["target_speakers"] == ["akito", "toya"]


def test_classification_suggestion_requires_manual_confirmation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)
    suggestion = {"timeline_stage": "早期搭档", "event_types": ["创作练习"], "participant_scope": "彰冬+VBS成员"}

    suggested = store.save_suggestion(entry["source_id"], suggestion)
    assert suggested["classification_status"] == "suggested"
    assert suggested["classification"]["timeline_stage"] == ""

    confirmed = store.update_source(entry["source_id"], confirm_classification=True)
    assert confirmed["classification_status"] == "confirmed"
    assert confirmed["classification"] == suggestion


def test_classification_accepts_legacy_participant_scope_alias(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)

    confirmed = store.update_source(
        entry["source_id"],
        classification={"timeline_stage": "RUSH BEATS目标确立", "event_types": ["关系回顾"], "participant_scope": "有其他角色"},
        confirm_classification=True,
    )

    assert confirmed["classification"]["participant_scope"] == "彰冬+多方角色"


def test_stage_and_scope_descriptions_cover_every_selectable_value() -> None:
    from tools.event_memory.coverage.core import PARTICIPANT_SCOPES, TIMELINE_STAGES

    assert set(TIMELINE_STAGES) == set(TIMELINE_STAGE_DESCRIPTIONS)
    assert set(PARTICIPANT_SCOPES) == set(PARTICIPANT_SCOPE_DESCRIPTIONS)
    assert "美国" in TIMELINE_STAGE_DESCRIPTIONS["赴美/美国筹备（RUSH BEATS）"]
    assert "目标片段" in PARTICIPANT_SCOPE_DESCRIPTIONS["彰冬+多方角色"]


def test_rejected_sources_are_archived_and_not_maintained(tmp_path: Path) -> None:
    store = _store(tmp_path)
    url = "https://pjsk.moe/zh-cn/story/card/1388/"
    _write(
        store.data_dir / "event_memory" / "story_import" / "drafts" / "story-0123456789abcdef.json",
        {
            "draft_id": "story-0123456789abcdef",
            "source": {"canonical_url": url},
            "story": {"episode_title": "卡牌剧情"},
            "review": {"status": "rejected"},
        },
    )
    store.sync()
    rejected = store.load_catalog()["sources"][0]

    assert store.list_sources() == []
    assert store.list_sources({"workflow_status": "rejected"}) == [rejected]
    assert store.summary()["processed_sources"] == 1
    assert store.summary()["maintenance_sources"] == 0
    assert store.summary()["published_sources"] == 0
    assert store.summary()["rejected_records"] == 1
    assert store.summary()["classification"] == {}
    assert store.summary()["evaluation"] == {}
    with pytest.raises(CoverageError, match="已发布"):
        store.save_suggestion(
            rejected["source_id"],
            {"timeline_stage": "日常/未定位", "event_types": ["日常互动"], "participant_scope": "仅彰冬"},
        )
    with pytest.raises(CoverageError, match="已发布"):
        store.update_source(
            rejected["source_id"],
            classification={"timeline_stage": "日常/未定位", "event_types": ["日常互动"], "participant_scope": "仅彰冬"},
            confirm_classification=True,
        )


def test_eval_draft_does_not_change_formal_set_until_approved(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)
    before = store.load_eval_set()

    draft = store.save_eval_draft(entry["source_id"], _valid_cases())

    assert draft["status"] == "draft"
    assert store.load_eval_set() == before
    approved = store.approve_eval_draft(draft["draft_id"])
    assert approved["status"] == "approved"
    generated = [case for case in store.load_eval_set()["cases"] if case["id"].startswith("coverage-")]
    assert len(generated) == 5
    assert next(case for case in generated if case["id"].endswith("-a01"))["forbidden_event_ids"] == ["event-adjacent"]


def test_eval_approval_enforces_case_mix_and_adjacent_guard(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)
    cases = _valid_cases()
    cases[2]["forbidden_event_ids"] = []
    draft = store.save_eval_draft(entry["source_id"], cases)

    with pytest.raises(CoverageError, match="forbidden_event_ids"):
        store.approve_eval_draft(draft["draft_id"])


def test_eval_approval_rejects_unknown_forbidden_event(tmp_path: Path) -> None:
    store = _store(tmp_path)
    entry = _published_source(store)
    cases = _valid_cases()
    cases[2]["forbidden_event_ids"] = ["event-invented"]
    draft = store.save_eval_draft(entry["source_id"], cases)

    with pytest.raises(CoverageError, match="不存在"):
        store.approve_eval_draft(draft["draft_id"])


def test_find_adjacent_events_ranks_shared_tags() -> None:
    events = [
        {"event_id": "target", "topics": ["发烧", "对决"], "relationship_tags": ["信任"]},
        {"event_id": "near", "topics": ["对决"], "relationship_tags": ["信任"]},
        {"event_id": "far", "topics": ["做饭"]},
    ]

    assert [event["event_id"] for event in find_adjacent_events(events, ["target"])] == ["near"]
