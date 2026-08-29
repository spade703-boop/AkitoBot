from __future__ import annotations

from copy import deepcopy
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

from nonebot_plugin_akito.core import story_import
from tools.event_memory.story_import import web as story_import_web
from tools.event_memory.story_import.web import UI_PATH, StoryImportService, make_handler


def _draft() -> dict:
    return {
        "schema_version": 1,
        "draft_id": "story-0123456789abcdef",
        "status": "draft",
        "source": {
            "site": "pjsk.moe",
            "url": "https://pjsk.moe/zh-cn/story/event/140/8/",
            "canonical_url": "https://pjsk.moe/zh-cn/story/event/140/8/",
            "locale": "zh-CN",
            "route_type": "event",
            "route_params": {"story_id": "140", "episode_no": "8"},
            "source_hash": "fixture-source",
            "assets": [],
        },
        "page": {"title": "fixture"},
        "story": {"title": "fixture", "episode_title": "初遇", "scenario_id": "fixture-140-8", "assetbundle_name": ""},
        "participants": [
            {"id": "akito", "name_ja": "東雲彰人", "name_zh": "东云彰人", "role": "target"},
            {"id": "toya", "name_ja": "青柳冬弥", "name_zh": "青柳冬弥", "role": "target"},
        ],
        "actions": [
            {"index": 0, "speaker_id": "akito", "speaker_ja": "彰人", "speaker_zh": "彰人", "text_ja": "行くぞ。", "text_zh": "走了。", "kind": "dialogue"},
            {"index": 1, "speaker_id": "toya", "speaker_ja": "冬弥", "speaker_zh": "冬弥", "text_ja": "ああ。", "text_zh": "嗯。", "kind": "dialogue"},
        ],
        "target_segments": [{"segment_id": "segment-001", "start_index": 0, "end_index": 1, "target_speakers": ["akito", "toya"], "evidence_refs": [0, 1], "text_ja": "彰人: 行くぞ。\n冬弥: ああ。", "text_zh": "彰人: 走了。\n冬弥: 嗯。"}],
        "draft_analysis": {"status": "not_requested", "summary_zh": "初遇", "timeline": [], "relationship_facts": [], "akito_attitude": [], "toya_traits": [], "uncertain_or_missing": [], "style_examples": []},
        "review": {"status": "draft", "reviewer": "", "reviewed_at": "", "notes": []},
        "publish": {"event_memory_id": "", "published_at": ""},
    }


class _FakeCore:
    StoryImportError = story_import.StoryImportError
    _now_iso = staticmethod(story_import._now_iso)

    def __init__(self):
        self.payload = _draft()

    def capture_story(self, url: str, *, data_dir: Path, enrich: bool):
        payload = deepcopy(self.payload)
        payload["source"]["url"] = url
        payload["source"]["canonical_url"] = url.rstrip("/") + "/"
        return payload

    save_draft = staticmethod(story_import.save_draft)
    load_draft = staticmethod(story_import.load_draft)
    update_review = staticmethod(story_import.update_review)
    preview_event_memory = staticmethod(story_import.preview_event_memory)
    merge_event_memory = staticmethod(story_import.merge_event_memory)


def _request(port: int, method: str, path: str, body: dict | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    encoded = json.dumps(body or {}, ensure_ascii=False).encode("utf-8") if body is not None else None
    connection.request(method, path, body=encoded, headers={"Content-Type": "application/json"} if encoded else {})
    response = connection.getresponse()
    raw = response.read().decode("utf-8") if response.length != 0 else ""
    data = raw if response.getheader("Content-Type", "").startswith("text/html") else (json.loads(raw) if raw else {})
    connection.close()
    return response.status, data


def test_web_ui_is_local_and_api_runs_complete_flow(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        story_import_web,
        "suggest_coverage_classification",
        lambda *_args: {"timeline_stage": "初遇组队", "event_types": ["相遇组队"], "participant_scope": "仅彰冬"},
    )
    monkeypatch.setattr(
        story_import_web,
        "generate_coverage_eval_cases",
        lambda *_args: [
            {"case_type": "positive", "query": "你和冬弥初遇那次？"},
            {"case_type": "positive", "query": "你为什么会选冬弥当搭档？"},
            {"case_type": "adjacent", "query": "你们刚组队时发生了什么？", "forbidden_event_ids": ["event-other"]},
            {"case_type": "negative", "query": "你们是在纽约初遇的？"},
            {"case_type": "negative", "query": "冬弥一开始拒绝和你唱歌？"},
        ],
    )
    assert UI_PATH.exists()
    fake_core = _FakeCore()
    service = StoryImportService(tmp_path, core=fake_core)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(service))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        status, health = _request(port, "GET", "/api/health")
        assert status == 200 and health["ok"] is True
        status, page = _request(port, "GET", "/")
        assert status == 200
        assert "彰人 / 冬弥剧情采集" in page if isinstance(page, str) else True
        assert "story/card/1388/" in page if isinstance(page, str) else True

        status, captured = _request(port, "POST", "/api/capture", {"url": "https://pjsk.moe/zh-cn/story/event/140/8/"})
        assert status == 201
        draft_id = captured["draft"]["draft_id"]
        status, listing = _request(port, "GET", "/api/drafts")
        assert status == 200 and listing["drafts"][0]["draft_id"] == draft_id

        status, updated = _request(port, "PATCH", f"/api/drafts/{draft_id}/analysis", {"analysis": {"summary_zh": "已核对初遇"}})
        assert status == 200 and updated["draft"]["review"]["status"] == "draft"
        status, reviewed = _request(port, "POST", f"/api/drafts/{draft_id}/review", {"status": "approved", "reviewer": "test"})
        assert status == 200 and reviewed["draft"]["review"]["status"] == "approved"
        status, preview = _request(port, "POST", f"/api/drafts/{draft_id}/dedupe-preview", {})
        assert status == 200 and preview["preview"]["status"] == "new"
        status, published = _request(port, "POST", f"/api/drafts/{draft_id}/publish", {"confirm_revision": False})
        assert status == 200 and published["event_id"].startswith("akito-toya-web-")

        status, coverage = _request(port, "GET", "/api/coverage")
        assert status == 200 and coverage["summary"]["published_sources"] == 1
        assert "赴美/美国筹备（RUSH BEATS）" in coverage["options"]["timeline_stages"]
        assert coverage["options"]["participant_scope_label"].startswith("参与范围")
        assert "目标片段" in coverage["options"]["participant_scope_descriptions"]["彰冬+多方角色"]
        status, synced = _request(port, "POST", "/api/coverage/sync", {})
        assert status == 200 and synced["summary"]["published_sources"] == 1
        source = coverage["sources"][0]
        assert source["workflow_status"] == "published"
        source_id = source["source_id"]
        status, suggested = _request(port, "POST", f"/api/coverage/sources/{source_id}/suggest-classification", {})
        assert status == 200 and suggested["source"]["classification_status"] == "suggested"
        status, confirmed = _request(
            port,
            "PATCH",
            f"/api/coverage/sources/{source_id}",
            {"confirm_classification": True},
        )
        assert status == 200 and confirmed["source"]["classification_status"] == "confirmed"
        inventory_path = tmp_path / "content" / "akito_event_memories.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        inventory["events"].append({"event_id": "event-other", "source_kind": "legacy_script"})
        inventory_path.write_text(json.dumps(inventory, ensure_ascii=False), encoding="utf-8")
        status, generated = _request(port, "POST", f"/api/coverage/sources/{source_id}/generate-eval", {})
        assert status == 201 and generated["draft"]["status"] == "draft"
        eval_id = generated["draft"]["draft_id"]
        status, approved = _request(port, "POST", f"/api/coverage/evals/{eval_id}/approve", {})
        assert status == 200 and approved["draft"]["status"] == "approved"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
