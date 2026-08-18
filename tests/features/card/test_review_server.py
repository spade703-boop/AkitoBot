"""Local card hairstyle review server persistence tests."""

from __future__ import annotations

import json

import pytest

from tools import card_review_server


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _catalog_payload():
    return {
        "version": 1,
        "scope": "vbs_rarity_4",
        "cards": [
            {
                "id": 212,
                "sequence_alias": "彰3",
                "character_name": "东云彰人",
                "title": "敗北の夜",
                "event_name": "STRAY BAD DOG",
                "commissioned_song": "シネマ",
                "supply_label": "期间限定",
                "release_at": 100,
                "assetbundle_name": "res011_no007",
                "hairstyle": {
                    "available": True,
                    "description": "模型初稿",
                    "features": ["短发"],
                    "owner_location": "中央",
                    "structure": {"length": "短发"},
                    "hair_accessories": ["绿色发带"],
                    "headwear": [],
                    "observed_color": "暖棕色",
                    "lighting_effect": "阳光偏暖",
                    "quality_flags": ["observed_color_affected_by_lighting"],
                    "status": "needs_review",
                    "source": "vision_model",
                },
                "normal_art": {"summary": "花前描述", "status": "verified"},
                "trained_art": {"summary": "花后描述", "status": "verified"},
                "generation": {
                    "status": "complete",
                    "art_status": "complete",
                    "hairstyle_status": "complete",
                    "quality_status": "needs_review",
                    "review_reasons": ["hairstyle_needs_review"],
                    "error": "",
                },
            },
            {
                "id": 500,
                "sequence_alias": "彰10",
                "character_name": "东云彰人",
                "title": "常驻卡",
                "release_at": 200,
                "assetbundle_name": "res011_no010",
                "hairstyle": {"available": False, "status": "not_applicable"},
                "normal_art": {"status": "verified"},
                "trained_art": {"status": "verified"},
                "generation": {"status": "complete", "quality_status": "verified"},
            },
        ],
    }


def test_snapshot_only_lists_limited_cards(tmp_path):
    catalog = tmp_path / "cards.json"
    overrides = tmp_path / "reviews.json"
    queue = tmp_path / "queue.json"
    _write(catalog, _catalog_payload())
    store = card_review_server.ReviewStore(catalog, overrides, queue)

    result = store.snapshot()

    assert result["stats"] == {"total": 1, "reviewed": 0, "pending": 1}
    assert result["cards"][0]["sequence_alias"] == "彰3"
    assert result["cards"][0]["trained_image"].endswith("res011_no007/card_after_training.webp")


def test_save_review_updates_override_catalog_and_queue(tmp_path):
    catalog = tmp_path / "cards.json"
    overrides = tmp_path / "reviews.json"
    queue = tmp_path / "queue.json"
    _write(catalog, _catalog_payload())
    _write(overrides, {"version": 1, "cards": {}})
    _write(queue, {"version": 1, "items": [{"id": 212, "reason": "hairstyle_needs_review"}]})
    store = card_review_server.ReviewStore(catalog, overrides, queue)

    result = store.save_review(
        212,
        {
            "description": "人工确认的短发造型",
            "features": ["短发", "绿色发带"],
            "review_note": "对照花后原图确认",
        },
    )

    saved_overrides = json.loads(overrides.read_text(encoding="utf-8"))
    saved_catalog = json.loads(catalog.read_text(encoding="utf-8"))
    saved_queue = json.loads(queue.read_text(encoding="utf-8"))
    hairstyle = saved_catalog["cards"][0]["hairstyle"]
    generation = saved_catalog["cards"][0]["generation"]
    assert saved_overrides["cards"]["212"]["hairstyle"]["description"] == "人工确认的短发造型"
    assert hairstyle["status"] == "reviewed"
    assert hairstyle["structure"] == {"length": "短发"}
    assert hairstyle["hair_accessories"] == ["绿色发带"]
    assert generation["quality_status"] == "verified"
    assert saved_queue["items"] == []
    assert result["stats"] == {"total": 1, "reviewed": 1, "pending": 0}


def test_save_review_rejects_unknown_card(tmp_path):
    catalog = tmp_path / "cards.json"
    _write(catalog, _catalog_payload())
    store = card_review_server.ReviewStore(catalog, tmp_path / "reviews.json", tmp_path / "queue.json")

    with pytest.raises(card_review_server.ReviewError, match="找不到卡片"):
        store.save_review(9999, {"description": "描述", "features": []})


def test_review_page_contains_required_workflow_controls():
    html = (card_review_server.WEB_ROOT / "index.html").read_text(encoding="utf-8")

    assert "花前" in html
    assert "花后" in html
    assert 'data-testid="save-review"' in html
    assert "跳过" in html
    assert "/api/review" in html
