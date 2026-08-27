from __future__ import annotations

import json
from pathlib import Path

import pytest

from nonebot_plugin_akito.core.story_import import (
    FetchedAsset,
    StoryImportError,
    _target_segments,
    capture_story,
    event_memory_from_draft,
    merge_event_memory,
    parse_story_url,
    preview_event_memory,
    save_draft,
    update_review,
    validate_story_draft,
)


def _asset(url: str, body: str, *, content_type: str = "application/json") -> FetchedAsset:
    import hashlib

    return FetchedAsset(
        url=url,
        status=200,
        content_type=content_type,
        body=body,
        sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
    )


def _fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(
            url,
            "<html><title>唱出超越那个夜晚的歌</title><meta name='description' content='初遇'></html>",
            content_type="text/html",
        )
    if url.endswith("eventStories.json"):
        body = json.dumps(
            {
                "eventId": 140,
                "eventStoryEpisodes": [
                    {
                        "eventStoryId": 140,
                        "episodeNo": 8,
                        "title": "唱出超越那个夜晚的歌",
                        "scenarioId": "wl_piapro_01_08",
                        "assetbundleName": "event_beginning_2024_08",
                    }
                ],
            },
            ensure_ascii=False,
        )
        return _asset(url, body)
    if url.endswith("events.json"):
        return _asset(url, "[]")
    if url.endswith("event_140.json"):
        body = json.dumps(
            {
                "episodes": {
                    "8": {
                        "scenarioId": "wl_piapro_01_08",
                        "talkData": {
                            "中学生的彰人": "初中生彰人",
                            "音准和机器一样精确……": "音准和机器一样精确……",
                            "中学生的冬弥": "初中生冬弥",
                            "——有意思。喂，要不要和我一起唱唱看？": "——有意思。喂，要不要和我一起唱唱看？",
                        },
                    }
                }
            },
            ensure_ascii=False,
        )
        return _asset(url, body)
    raise StoryImportError(f"fixture missing: {url}")


def test_parse_story_url_supports_localized_and_default_routes():
    route = parse_story_url("https://pjsk.moe/zh-cn/story/event/140/8/")
    assert route.locale == "zh-CN"
    assert route.region == "cn"
    assert route.route_type == "event"
    assert route.params == {"story_id": "140", "episode_no": "8"}

    default_route = parse_story_url("https://pjsk.moe/story/card/123/1/")
    assert default_route.locale == "zh-CN"
    assert default_route.route_type == "card"

    card_episode_route = parse_story_url("https://pjsk.moe/zh-cn/story/card/1388/")
    assert card_episode_route.params == {"story_id": "1388"}


@pytest.mark.parametrize(
    ("route_type", "path", "expected_params"),
    [
        ("event", "event/140/8", {"story_id": "140", "episode_no": "8"}),
        ("unit", "unit/2/1", {"story_id": "2", "episode_no": "1"}),
        ("card", "card/300/1", {"story_id": "300", "episode_no": "1"}),
        ("area", "area/10/1", {"story_id": "10", "episode_no": "1"}),
        ("self", "self/1", {"story_id": "1"}),
        ("special", "special/20/1", {"story_id": "20", "episode_no": "1"}),
    ],
)
def test_parse_story_url_supports_all_documented_routes(route_type, path, expected_params):
    route = parse_story_url(f"https://pjsk.moe/zh-cn/story/{path}/")

    assert route.route_type == route_type
    assert route.params == expected_params


@pytest.mark.parametrize(
    "url",
    [
        "http://pjsk.moe/zh-cn/story/event/140/8/",
        "https://example.com/zh-cn/story/event/140/8/",
        "https://pjsk.moe/zh-cn/cards/140/",
        "https://pjsk.moe/zh-cn/story/event/140/",
        "https://pjsk.moe/zh-cn/story/event/140/8/extra/",
    ],
)
def test_parse_story_url_rejects_unsafe_or_incomplete_urls(url: str):
    with pytest.raises(StoryImportError):
        parse_story_url(url)


def test_capture_preserves_bilingual_target_evidence_when_scenario_asset_missing(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/140/8/",
        data_dir=tmp_path,
        fetcher=_fixture_fetcher,
    )

    assert draft["story"]["scenario_id"] == "wl_piapro_01_08"
    assert draft["source"]["route_type"] == "event"
    assert draft["target_segments"]
    assert any(action["speaker_id"] == "akito" for action in draft["actions"])
    assert any(action["speaker_id"] == "toya" for action in draft["actions"])
    assert any(action["text_ja"].startswith("——有意思") for action in draft["actions"])
    assert any(asset["kind"] == "translation" for asset in draft["source"]["assets"])
    assert validate_story_draft(draft) == []


def test_non_card_capture_prefers_japanese_scenario_and_requested_translation(tmp_path: Path):
    requested_urls: list[str] = []

    def fetcher(url: str) -> FetchedAsset:
        requested_urls.append(url)
        if url.startswith("https://pjsk.moe/"):
            return _asset(url, "<html><title>双语活动</title></html>", content_type="text/html")
        if url == "https://metadata.exmeaning.com/cn/master/eventStories.json":
            return _asset(
                url,
                json.dumps(
                    [
                        {
                            "eventStoryId": 140,
                            "episodeNo": 8,
                            "scenarioId": "event_140_08",
                            "assetbundleName": "event_bundle",
                        }
                    ],
                    ensure_ascii=False,
                ),
            )
        if url.endswith("events.json"):
            return _asset(url, "[]")
        if url == "https://storage.exmeaning.com/sekai-jp-assets/event_story/event_bundle/scenario/event_140_08.json":
            return _asset(
                url,
                json.dumps(
                    {
                        "TalkData": [
                            {"WindowDisplayName": "彰人", "Body": "行くぞ。"},
                            {"WindowDisplayName": "冬弥", "Body": "ああ。"},
                        ]
                    },
                    ensure_ascii=False,
                ),
            )
        if url == "https://translation.exmeaning.com/files/translation/eventStory/event_140.json":
            return _asset(
                url,
                json.dumps(
                    {
                        "episodes": {
                            "8": {
                                "talkData": {
                                    "行くぞ。": "走了。",
                                    "ああ。": "嗯。",
                                }
                            }
                        }
                    },
                    ensure_ascii=False,
                ),
            )
        raise StoryImportError(f"fixture missing: {url}")

    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/140/8/",
        data_dir=tmp_path,
        fetcher=fetcher,
    )

    assert draft["source"]["original_region"] == "jp"
    assert draft["source"]["translation_region"] == "cn"
    assert draft["actions"][0]["text_ja"] == "行くぞ。"
    assert draft["actions"][0]["text_zh"] == "走了。"
    assert any("sekai-jp-assets" in url for url in requested_urls)


def _all_route_fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(url, "<html><title>路由 fixture</title></html>", content_type="text/html")

    descriptors = {
        "eventStories.json": ("140", "8", "event", "event_bundle"),
        "unitStories.json": ("2", "1", "unit", "unit_bundle"),
        "cardEpisodes.json": ("300", "1", "card", "card_bundle"),
        "actionSets.json": ("10", "1", "area", "area_bundle"),
        "characters.json": ("1", "", "self", "self_bundle"),
        "specialStories.json": ("20", "1", "special", "special_bundle"),
    }
    for filename, (story_id, episode_no, route_type, bundle) in descriptors.items():
        if url.endswith(filename):
            key = (
                "id"
                if route_type == "self"
                else {
                    "event": "eventStoryId",
                    "unit": "unitStoryId",
                    "card": "cardId",
                    "area": "actionSetId",
                    "special": "specialStoryId",
                }[route_type]
            )
            row = {key: story_id, "scenarioId": f"fixture_{route_type}_{story_id}", "assetbundleName": bundle}
            if episode_no:
                row["episodeNo"] = episode_no
            return _asset(url, json.dumps([row], ensure_ascii=False))
    if "metadata.exmeaning.com" in url:
        return _asset(url, "[]")
    if "storage.exmeaning.com" in url:
        return _asset(
            url,
            json.dumps(
                {
                    "actions": [
                        {"WindowDisplayName": "東雲彰人", "Text": "……冬弥、行くぞ。"},
                        {"WindowDisplayName": "青柳冬弥", "Text": "ああ、彰人。"},
                    ]
                },
                ensure_ascii=False,
            ),
        )
    if "translation.exmeaning.com" in url:
        return _asset(
            url,
            json.dumps(
                {
                    "talkData": {
                        "……冬弥、行くぞ。": "……冬弥，走了。",
                        "ああ、彰人。": "嗯，彰人。",
                    }
                },
                ensure_ascii=False,
            ),
        )
    raise StoryImportError(f"fixture missing: {url}")


@pytest.mark.parametrize("route_path", ["event/140/8", "unit/2/1", "card/300/1", "area/10/1", "self/1", "special/20/1"])
def test_capture_supports_all_routes_with_native_scenario_actions(tmp_path: Path, route_path: str):
    draft = capture_story(
        f"https://pjsk.moe/zh-cn/story/{route_path}/",
        data_dir=tmp_path,
        fetcher=_all_route_fixture_fetcher,
    )

    assert draft["actions"]
    assert {row["speaker_id"] for row in draft["actions"]} >= {"akito", "toya"}
    assert draft["target_segments"]
    assert validate_story_draft(draft) == []


def _card_episode_id_fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(url, "<html><title>卡面剧情</title></html>", content_type="text/html")
    if url.endswith("cardEpisodes.json"):
        return _asset(
            url,
            json.dumps(
                [
                    {
                        "id": 2662,
                        "cardId": 1388,
                        "scenarioId": "012053_touya02",
                        "cardEpisodePartType": "episode_2",
                    }
                ],
                ensure_ascii=False,
            ),
        )
    if url.endswith("cards.json"):
        return _asset(url, json.dumps([{"id": 1388, "assetbundleName": "res012_no053"}], ensure_ascii=False))
    if "storage.exmeaning.com" in url:
        return _asset(
            url,
            json.dumps(
                {"TalkData": [{"WindowDisplayName": "彰人", "Body": "冬弥，走了。"}]},
                ensure_ascii=False,
            ),
        )
    raise StoryImportError(f"fixture missing: {url}")


def test_capture_supports_card_episode_id_and_native_body_field(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/card/1388/",
        data_dir=tmp_path,
        fetcher=_card_episode_id_fixture_fetcher,
    )

    assert draft["source"]["route_params"] == {"story_id": "1388"}
    assert draft["story"]["scenario_id"] == "012053_touya02"
    assert draft["actions"][0]["text_ja"] == "冬弥，走了。"
    assert draft["actions"][0]["speaker_id"] == "akito"
    assert draft["target_segments"] == []
    assert any(asset["kind"] == "scenario" for asset in draft["source"]["assets"])
    assert validate_story_draft(draft) == []


def _card_episode_id_collision_fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(url, "<html><title>卡面剧情</title></html>", content_type="text/html")
    if url.endswith("cardEpisodes.json"):
        return _asset(
            url,
            json.dumps(
                [
                    {"id": 1388, "cardId": 695, "scenarioId": "011027_akito02"},
                ],
                ensure_ascii=False,
            ),
        )
    if url.endswith("cards.json"):
        return _asset(
            url,
            json.dumps(
                [{"id": 695, "assetbundleName": "res011_no027", "cardRarityType": "rarity_4"}], ensure_ascii=False
            ),
        )
    raise StoryImportError(f"fixture missing: {url}")


def test_card_url_does_not_treat_episode_id_as_card_id(tmp_path: Path):
    with pytest.raises(StoryImportError):
        capture_story(
            "https://pjsk.moe/zh-cn/story/card/1388/",
            data_dir=tmp_path,
            fetcher=_card_episode_id_collision_fixture_fetcher,
        )


def _jp_card_bilingual_fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(url, "<html><title>卡面剧情（测试）</title></html>", content_type="text/html")
    if url == "https://metadata.exmeaning.com/jp/master/cardEpisodes.json":
        return _asset(
            url,
            json.dumps(
                [
                    {"id": 2661, "cardId": 1388, "scenarioId": "012053_touya01", "cardEpisodePartType": "episode_1"},
                    {"id": 2662, "cardId": 1388, "scenarioId": "012053_touya02", "cardEpisodePartType": "episode_2"},
                ],
                ensure_ascii=False,
            ),
        )
    if url == "https://metadata.exmeaning.com/jp/master/cards.json":
        return _asset(url, json.dumps([{"id": 1388, "assetbundleName": "res012_no053"}], ensure_ascii=False))
    if (
        url == "https://metadata.exmeaning.com/cn/master/cardEpisodes.json"
        or url == "https://metadata.exmeaning.com/cn/master/cards.json"
    ):
        return _asset(url, json.dumps([], ensure_ascii=False))
    if "storage.exmeaning.com/sekai-jp-assets/" in url:
        scenario_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        return _asset(
            url,
            json.dumps(
                {
                    "TalkData": [
                        {"WindowDisplayName": "彰人", "Body": f"日文 {scenario_id}"},
                        {"WindowDisplayName": "冬弥", "Body": "ああ。"},
                    ]
                },
                ensure_ascii=False,
            ),
        )
    if "storage.exmeaning.com/sekai-cn-assets/" in url:
        scenario_id = url.rsplit("/", 1)[-1].removesuffix(".json")
        return _asset(
            url,
            json.dumps(
                {
                    "TalkData": [
                        {"WindowDisplayName": "彰人", "Body": f"中文 {scenario_id}"},
                        {"WindowDisplayName": "冬弥", "Body": "嗯。"},
                    ]
                },
                ensure_ascii=False,
            ),
        )
    raise StoryImportError(f"fixture missing: {url}")


def test_card_uses_jp_master_and_pairs_jp_cn_assets_by_order(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/card/1388/",
        data_dir=tmp_path,
        fetcher=_jp_card_bilingual_fixture_fetcher,
    )

    assert draft["source"]["data_region"] == "jp"
    assert draft["source"]["original_region"] == "jp"
    assert draft["source"]["translation_region"] == "cn"
    assert draft["story"]["scenario_ids"] == ["012053_touya01", "012053_touya02"]
    assert len(draft["actions"]) == 4
    assert draft["actions"][0]["text_ja"] == "日文 012053_touya01"
    assert draft["actions"][0]["text_zh"] == "中文 012053_touya01"
    assert draft["actions"][2]["text_ja"] == "日文 012053_touya02"
    assert draft["actions"][2]["text_zh"] == "中文 012053_touya02"
    assert sum(asset["kind"] == "scenario" for asset in draft["source"]["assets"]) == 2
    assert sum(asset["kind"] == "scenario_translation" for asset in draft["source"]["assets"]) == 2
    assert len(draft["target_segments"]) == 2
    assert draft["target_segments"][0]["evidence_refs"] == [0, 1]
    assert draft["target_segments"][1]["evidence_refs"] == [2, 3]
    assert validate_story_draft(draft) == []


def test_card_episode_url_only_fetches_requested_jp_part(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/card/1388/2/",
        data_dir=tmp_path,
        fetcher=_jp_card_bilingual_fixture_fetcher,
    )

    assert draft["story"]["scenario_ids"] == ["012053_touya02"]
    assert len(draft["actions"]) == 2
    assert draft["actions"][0]["text_ja"] == "日文 012053_touya02"


def test_card_episode_aliases_match_numeric_parts(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/card/1388/second_part/",
        data_dir=tmp_path,
        fetcher=_jp_card_bilingual_fixture_fetcher,
    )

    assert draft["story"]["scenario_ids"] == ["012053_touya02"]


def _event_jp_parent_bundle_fixture_fetcher(url: str) -> FetchedAsset:
    if url.startswith("https://pjsk.moe/"):
        return _asset(url, "<html><title>自己的战斗方式</title></html>", content_type="text/html")
    if (
        url == "https://metadata.exmeaning.com/cn/master/eventStories.json"
        or url == "https://metadata.exmeaning.com/cn/master/events.json"
    ):
        return _asset(url, "[]")
    if url == "https://metadata.exmeaning.com/jp/master/eventStories.json":
        return _asset(
            url,
            json.dumps(
                [
                    {
                        "eventId": 187,
                        "assetbundleName": "event_overcome_2025",
                        "eventStoryEpisodes": [
                            {
                                "eventStoryId": 187,
                                "episodeNo": 7,
                                "scenarioId": "event_187_07",
                                "assetbundleName": "event_overcome_2025_07",
                                "title": "自分の戦い方",
                            }
                        ],
                    }
                ],
                ensure_ascii=False,
            ),
        )
    if url == "https://metadata.exmeaning.com/jp/master/events.json":
        return _asset(url, "[]")
    if "event_overcome_2025_07" in url:
        raise StoryImportError("fixture child bundle missing")
    if (
        url
        == "https://storage.exmeaning.com/sekai-jp-assets/event_story/event_overcome_2025/scenario/event_187_07.json"
    ):
        return _asset(
            url,
            json.dumps(
                {
                    "TalkData": [
                        {"WindowDisplayName": "彰人", "Body": "よう。冬弥、来てるか？"},
                        {"WindowDisplayName": "冬弥", "Body": "ああ。来ている。"},
                    ]
                },
                ensure_ascii=False,
            ),
        )
    if url == "https://translation.exmeaning.com/files/translation/eventStory/event_187.json":
        return _asset(
            url,
            json.dumps(
                {
                    "episodes": {
                        "7": {
                            "scenarioId": "event_187_07",
                            "talkData": {
                                "よう。冬弥、来てるか？": "哟。冬弥在吗？",
                                "ああ。来ている。": "嗯。我在。",
                            },
                        }
                    }
                },
                ensure_ascii=False,
            ),
        )
    raise StoryImportError(f"fixture missing: {url}")


def test_event_falls_back_to_jp_master_and_parent_bundle(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/187/7/",
        data_dir=tmp_path,
        fetcher=_event_jp_parent_bundle_fixture_fetcher,
    )

    assert draft["source"]["data_region"] == "jp"
    assert draft["story"]["scenario_id"] == "event_187_07"
    assert draft["story"]["assetbundle_name"] == "event_overcome_2025"
    assert [action["text_zh"] for action in draft["actions"]] == ["哟。冬弥在吗？", "嗯。我在。"]
    assert any("event_overcome_2025/scenario/event_187_07.json" in asset["url"] for asset in draft["source"]["assets"])
    assert draft["target_segments"]


def test_target_segments_exclude_unpaired_target_scenes_and_track_joint_speakers():
    actions = [
        {
            "index": 0,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "场景",
            "text_zh": "场景",
        },
        {
            "index": 1,
            "speaker_id": "akito",
            "speaker_ids": ["akito"],
            "speaker_ja": "彰人",
            "speaker_zh": "彰人",
            "text_ja": "单独台词",
            "text_zh": "单独台词",
        },
        {
            "index": 2,
            "speaker_id": "toya",
            "speaker_ids": ["toya"],
            "speaker_ja": "冬弥",
            "speaker_zh": "冬弥",
            "text_ja": "回应",
            "text_zh": "回应",
        },
        {
            "index": 3,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "转场",
            "text_zh": "转场",
        },
        {
            "index": 4,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "转场",
            "text_zh": "转场",
        },
        {
            "index": 5,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "转场",
            "text_zh": "转场",
        },
        {
            "index": 6,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "转场",
            "text_zh": "转场",
        },
        {
            "index": 7,
            "speaker_id": "akito",
            "speaker_ids": ["akito"],
            "speaker_ja": "彰人",
            "speaker_zh": "彰人",
            "text_ja": "单独场景",
            "text_zh": "单独场景",
        },
        {
            "index": 8,
            "speaker_id": "",
            "speaker_ids": [],
            "speaker_ja": "旁白",
            "speaker_zh": "旁白",
            "text_ja": "结束",
            "text_zh": "结束",
        },
    ]

    segments = _target_segments(actions)

    assert len(segments) == 1
    assert segments[0]["evidence_refs"] == [0, 1, 2, 3, 4]
    assert segments[0]["target_speakers"] == ["akito", "toya"]

    target_only = _target_segments(actions, include_context=False)
    assert target_only[0]["evidence_refs"] == [1, 2]
    assert "旁白" not in target_only[0]["text_zh"]

    joint = dict(actions[0], speaker_id="akito", speaker_ids=["akito", "toya"], speaker_ja="彰人&心羽&冬弥")
    assert _target_segments([joint])[0]["target_speakers"] == ["akito", "toya"]


def test_review_and_publish_are_evidence_backed_and_idempotent(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/140/8/",
        data_dir=tmp_path,
        fetcher=_fixture_fetcher,
    )
    draft = update_review(draft, "approved", note="核对初遇剧情")
    assert draft["review"]["status"] == "approved"
    save_draft(draft, data_dir=tmp_path)

    memory_path = tmp_path / "content" / "akito_event_memories.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps({"schema_version": 1, "events": []}), encoding="utf-8")
    path, event_id = merge_event_memory(draft, data_dir=tmp_path)
    assert path == memory_path
    assert event_id.startswith("akito-toya-web-")
    first = json.loads(memory_path.read_text(encoding="utf-8"))
    assert len(first["events"]) == 1
    event = first["events"][0]
    assert list(event) == [
        "event_id",
        "source",
        "title",
        "summary",
        "category",
        "topics",
        "confidence",
        "entities",
        "participants",
        "relationship_tags",
        "timeline",
        "locations",
        "evidence",
        "keywords",
    ]
    assert set(event["source"]) == {"url", "draft_id", "record_indices", "content_digest", "evidence_digest"}
    assert set(event["evidence"][0]) == {"record_index", "type", "context", "dialogue"}
    assert event["evidence"][0]["context"]
    assert event["evidence"][0]["dialogue"]
    assert len(json.dumps(event, ensure_ascii=False)) < 3000

    merge_event_memory(draft, data_dir=tmp_path)
    second = json.loads(memory_path.read_text(encoding="utf-8"))
    assert len(second["events"]) == 1


def test_event_memory_publishes_multiple_joint_evidence_units_without_page_title():
    payload = {
        "draft_id": "story-0000000000000000",
        "source": {
            "canonical_url": "https://pjsk.moe/zh-cn/story/event/71/8/",
            "route_type": "event",
        },
        "story": {"scenario_id": "event_71_08", "episode_title": "页面小节标题"},
        "actions": [
            {
                "index": 0,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_ja": "冬弥",
                "speaker_zh": "冬弥",
                "text_ja": "俺は彰人を信じている。",
                "text_zh": "我信任彰人。",
                "kind": "dialogue",
            },
            {
                "index": 1,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_ja": "彰人",
                "speaker_zh": "彰人",
                "text_ja": "お前は最高の相棒だ。",
                "text_zh": "你是我最出色的搭档。",
                "kind": "dialogue",
            },
            {
                "index": 2,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_ja": "冬弥",
                "speaker_zh": "冬弥",
                "text_ja": "彰人と歌い続けたい。",
                "text_zh": "我想继续和彰人一起唱歌。",
                "kind": "dialogue",
            },
            {
                "index": 3,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_ja": "彰人",
                "speaker_zh": "彰人",
                "text_ja": "最高の舞台にしようぜ。",
                "text_zh": "让我们打造最精彩的舞台。",
                "kind": "dialogue",
            },
        ],
        "target_segments": [
            {"segment_id": "segment-001", "start_index": 0, "end_index": 1, "evidence_refs": [0, 1]},
            {"segment_id": "segment-002", "start_index": 2, "end_index": 3, "evidence_refs": [2, 3]},
        ],
        "draft_analysis": {"summary_zh": "彰人与冬弥互相信任，并继续共同歌唱。", "topics": ["搭档", "信任"]},
        "review": {"status": "approved"},
    }

    event = event_memory_from_draft(payload)

    assert event["title"] == ""
    assert len(event["evidence"]) == 2
    assert "冬弥" in event["evidence"][0]["context"]
    assert "彰人" in event["evidence"][0]["dialogue"]
    assert event["source"]["record_indices"] == [0, 2]
    assert "页面小节标题" not in event["summary"]


def test_event_memory_cleans_html_and_keeps_pivotal_decisions_and_boundaries():
    payload = {
        "draft_id": "story-1111111111111111",
        "source": {
            "canonical_url": "https://pjsk.moe/zh-cn/story/event/193/6/",
            "route_type": "event",
        },
        "story": {"scenario_id": "event_193_06", "episode_title": "页面标题"},
        "actions": [
            {
                "index": 0,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_zh": "冬弥",
                "text_zh": "只是从昨晚开始头晕，恐怕是发烧了。",
                "kind": "dialogue",
            },
            {
                "index": 1,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "——冬弥。你身体不舒服吧。",
                "kind": "dialogue",
            },
            {
                "index": 2,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "终于要参加演出了。",
                "kind": "dialogue",
            },
            {
                "index": 3,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_zh": "冬弥",
                "text_zh": "我一直很期待和Embers对决。",
                "kind": "dialogue",
            },
            {
                "index": 4,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_zh": "冬弥",
                "text_zh": "就算明白，我也还是想唱。",
                "kind": "dialogue",
            },
            {
                "index": 5,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "知道了，随你便吧。",
                "kind": "dialogue",
            },
            {
                "index": 6,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "既然你坚持到这份上，想必是有不能退让的理由。",
                "kind": "dialogue",
            },
            {
                "index": 7,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "不过，要是你撑不下去，到时候我会阻止你。",
                "kind": "dialogue",
            },
            {
                "index": 8,
                "speaker_id": "toya",
                "speaker_ids": ["toya"],
                "speaker_zh": "冬弥",
                "text_zh": "我保证，到时候会听你的。",
                "kind": "dialogue",
            },
            {
                "index": 9,
                "speaker_id": "akito",
                "speaker_ids": ["akito"],
                "speaker_zh": "彰人",
                "text_zh": "我去拿冷敷的东西。<br>正式演出前尽量保存体力。",
                "kind": "dialogue",
            },
        ],
        "target_segments": [
            {"segment_id": "segment-001", "start_index": 0, "end_index": 9, "evidence_refs": list(range(10))}
        ],
        "draft_analysis": {
            "summary_zh": "冬弥发烧仍坚持演出，彰人同意后约定必要时阻止他。",
            "topics": ["身体不适", "坚持演出"],
        },
        "review": {"status": "approved"},
    }

    event = event_memory_from_draft(payload)
    evidence = event["evidence"][0]

    assert "<br>" not in evidence["dialogue"]
    assert "知道了，随你便吧" in evidence["dialogue"]
    assert "撑不下去" in evidence["dialogue"]
    assert "冷敷" in evidence["dialogue"]
    assert "发烧" in evidence["context"]
    assert "Embers对决" in evidence["context"]
    assert "会听你的" in evidence["context"]


def test_dedupe_preview_requires_confirmation_and_keeps_revision_snapshot(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/140/8/",
        data_dir=tmp_path,
        fetcher=_fixture_fetcher,
    )
    draft = update_review(draft, "approved")
    memory_path = tmp_path / "content" / "akito_event_memories.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps({"schema_version": 1, "events": []}), encoding="utf-8")
    merge_event_memory(draft, data_dir=tmp_path)

    changed = json.loads(json.dumps(draft, ensure_ascii=False))
    changed["actions"][0]["text_zh"] += "（修订）"
    preview = preview_event_memory(changed, data_dir=tmp_path)
    assert preview["status"] == "revision"
    with pytest.raises(StoryImportError, match="确认修订"):
        merge_event_memory(changed, data_dir=tmp_path)

    merge_event_memory(changed, data_dir=tmp_path, confirm_revision=True)
    snapshots = list((tmp_path / "event_memory" / "story_import" / "revisions").rglob("*.json"))
    assert snapshots
    current = json.loads(memory_path.read_text(encoding="utf-8"))
    assert current["events"][0]["revision"]["number"] == 2


def test_republish_compacts_legacy_web_memory_without_creating_revision(tmp_path: Path):
    draft = update_review(
        capture_story(
            "https://pjsk.moe/zh-cn/story/event/140/8/",
            data_dir=tmp_path,
            fetcher=_fixture_fetcher,
        ),
        "approved",
    )
    memory_path = tmp_path / "content" / "akito_event_memories.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps({"schema_version": 1, "events": []}), encoding="utf-8")
    merge_event_memory(draft, data_dir=tmp_path)
    inventory = json.loads(memory_path.read_text(encoding="utf-8"))
    event = inventory["events"][0]
    event["content_digest"] = event["source"]["content_digest"]
    event["evidence_digest"] = event["source"]["evidence_digest"]
    event["evidence"][0].update(
        {
            "context_zh": event["evidence"][0]["context"] * 10,
            "dialogue_zh": event["evidence"][0]["dialogue"] * 10,
            "original_ja": "原文" * 100,
            "evidence_refs": list(range(100)),
        }
    )
    event["style_examples"] = [{"text_zh": "示例", "evidence_refs": list(range(100))}]
    event["revision"] = {"number": 1, "published_at": "legacy", "snapshot": ""}
    memory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")

    merge_event_memory(draft, data_dir=tmp_path)

    compact = json.loads(memory_path.read_text(encoding="utf-8"))["events"][0]
    assert "content_digest" not in compact
    assert "style_examples" not in compact
    assert "revision" not in compact
    assert set(compact["evidence"][0]) == {"record_index", "type", "context", "dialogue"}
    assert len(json.dumps(compact, ensure_ascii=False)) < 3000


def test_dedupe_rejects_same_content_from_different_source(tmp_path: Path):
    draft = update_review(
        capture_story(
            "https://pjsk.moe/zh-cn/story/event/140/8/",
            data_dir=tmp_path,
            fetcher=_fixture_fetcher,
        ),
        "approved",
    )
    memory_path = tmp_path / "content" / "akito_event_memories.json"
    memory_path.parent.mkdir(parents=True)
    memory_path.write_text(json.dumps({"schema_version": 1, "events": []}), encoding="utf-8")
    merge_event_memory(draft, data_dir=tmp_path)

    duplicate = json.loads(json.dumps(draft, ensure_ascii=False))
    duplicate["source"]["canonical_url"] = "https://pjsk.moe/zh-tw/story/event/140/8/"
    duplicate["source"]["url"] = duplicate["source"]["canonical_url"]
    assert preview_event_memory(duplicate, data_dir=tmp_path)["status"] == "duplicate_content"
    with pytest.raises(StoryImportError, match="内容与已有事件重复"):
        merge_event_memory(duplicate, data_dir=tmp_path)


def test_unapproved_draft_cannot_be_published(tmp_path: Path):
    draft = capture_story(
        "https://pjsk.moe/zh-cn/story/event/140/8/",
        data_dir=tmp_path,
        fetcher=_fixture_fetcher,
    )
    with pytest.raises(StoryImportError, match="approved"):
        event_memory_from_draft(draft)
