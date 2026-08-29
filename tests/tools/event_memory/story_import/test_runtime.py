from __future__ import annotations

import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from tools.event_memory.story_import import runtime as story_import_runtime


def test_load_project_env_reads_repository_env_without_outputting_secret(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text("DEEPSEEK_API_KEY=test-secret-from-env\n", encoding="utf-8")
    monkeypatch.setattr(story_import_runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    loaded_paths: list[Path] = []
    monkeypatch.setattr(story_import_runtime, "load_dotenv", lambda path: loaded_paths.append(Path(path)))

    story_import_runtime._load_project_env()

    assert loaded_paths == [env_path]


def test_format_llm_error_redacts_api_key_and_limits_detail():
    error = RuntimeError("request failed with test-secret and " + "x" * 400)

    message = story_import_runtime._format_llm_error(error, "test-secret")

    assert "test-secret" not in message
    assert message.startswith("RuntimeError: request failed with <redacted>")
    assert len(message) <= 255


def test_parse_llm_json_accepts_fenced_and_surrounded_object():
    raw = "分析如下：\n```json\n{\"summary_zh\": \"已核对\", \"topics\": []}\n```\n"

    assert story_import_runtime._parse_llm_json(raw) == {"summary_zh": "已核对", "topics": []}


def test_parse_llm_json_does_not_accept_nested_object_from_truncated_root():
    with pytest.raises(json.JSONDecodeError):
        story_import_runtime._parse_llm_json('{"timeline": [{"time": "开场"}')


def test_llm_max_tokens_is_bounded_and_configurable(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "5000")
    assert story_import_runtime._llm_max_tokens() == 5000
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "99999")
    assert story_import_runtime._llm_max_tokens() == 8192
    monkeypatch.setenv("DEEPSEEK_MAX_TOKENS", "not-a-number")
    assert story_import_runtime._llm_max_tokens() == 3200


def test_enrich_with_llm_marks_malformed_json_separately(monkeypatch):
    class FakeCompletions:
        def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"summary_zh":'))])

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    payload = {
        "actions": [{"index": 0}],
        "target_segments": [],
        "draft_analysis": {},
    }

    result = story_import_runtime.enrich_with_llm(payload)

    assert result["draft_analysis"]["status"] == "llm_invalid_json"
    assert "response_length=" in result["draft_analysis"]["error"]
    assert "test-secret" not in json.dumps(result, ensure_ascii=False)


def test_enrich_with_llm_marks_request_failure_and_redacts_secret(monkeypatch):
    class FakeOpenAI:
        def __init__(self, **_kwargs):
            raise RuntimeError("request failed with test-secret")

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    payload = {"actions": [], "target_segments": [], "draft_analysis": {}}

    result = story_import_runtime.enrich_with_llm(payload)

    assert result["draft_analysis"]["status"] == "llm_request_failed"
    assert "test-secret" not in result["draft_analysis"]["error"]


def test_suggest_coverage_classification_uses_compact_material(monkeypatch):
    captured: dict[str, object] = {}

    def fake_deepseek(prompt: str, *, max_tokens: int):
        captured.update(prompt=prompt, max_tokens=max_tokens)
        return {"timeline_stage": "早期搭档", "event_types": ["支持照顾"], "participant_scope": "仅彰冬"}

    monkeypatch.setattr(story_import_runtime, "_deepseek_json", fake_deepseek)
    result = story_import_runtime.suggest_coverage_classification(
        {"canonical_url": "https://pjsk.moe/zh-cn/story/event/193/6/", "route_type": "event"},
        [{"summary": "彰人担心发烧的冬弥。", "topics": ["发烧"], "relationship_tags": ["关心"], "evidence": "不应发送"}],
        {"draft_analysis": {"summary_zh": "两人讨论是否继续对决。"}, "actions": ["不应发送完整台词"]},
    )

    assert result["timeline_stage"] == "早期搭档"
    assert "不应发送" not in str(captured["prompt"])
    assert "不应发送完整台词" not in str(captured["prompt"])


def test_generate_coverage_eval_cases_rejects_missing_cases(monkeypatch):
    monkeypatch.setattr(story_import_runtime, "_deepseek_json", lambda *_args, **_kwargs: {"wrong": []})

    with pytest.raises(RuntimeError, match="missing cases"):
        story_import_runtime.generate_coverage_eval_cases({}, [], [])
