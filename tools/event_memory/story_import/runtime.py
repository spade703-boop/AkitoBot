"""Standalone runtime helpers shared by the story-import CLI and web app."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_project_env() -> None:
    """Load the repository .env without printing or exposing secret values."""
    paths = []
    for path in (PROJECT_ROOT / ".env", Path.cwd() / ".env"):
        if path in paths:
            continue
        paths.append(path)
        if path.exists():
            load_dotenv(path)


def _format_llm_error(error: Exception, secret: str) -> str:
    message = str(error).strip().replace(secret, "<redacted>")
    if len(message) > 240:
        message = message[:237] + "..."
    return f"{type(error).__name__}: {message or 'request failed'}"


def _parse_llm_json(raw: str) -> Any:
    """Parse an object from common LLM JSON wrappers without repairing data."""
    text = str(raw or "").strip()
    candidates: list[str] = []
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.IGNORECASE)
    candidates.extend(item.strip() for item in fenced if item.strip())
    if text:
        candidates.append(text)
    decoder = json.JSONDecoder()
    last_error: json.JSONDecodeError | None = None
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as error:
            last_error = error
        object_start = candidate.find("{")
        if object_start < 0:
            continue
        try:
            value, _end = decoder.raw_decode(candidate, object_start)
            return value
        except json.JSONDecodeError as error:
            last_error = error
    if last_error is not None:
        raise last_error
    raise json.JSONDecodeError("empty response", text, 0)


def _format_json_error(error: json.JSONDecodeError, raw: str) -> str:
    fence = "yes" if re.search(r"```(?:json)?", str(raw or ""), flags=re.IGNORECASE) else "no"
    return (
        f"{type(error).__name__}: {error.msg} at line {error.lineno} column {error.colno} "
        f"(char {error.pos}); response_length={len(str(raw or ''))}; markdown_fence={fence}"
    )


def _llm_max_tokens() -> int:
    """Read a bounded output budget so long episodes are not cut mid-JSON."""
    try:
        value = int(os.environ.get("DEEPSEEK_MAX_TOKENS", "3200"))
    except ValueError:
        value = 3200
    return max(256, min(value, 8192))


def load_story_import_module() -> Any:
    """Load the stdlib-only story importer without initializing NoneBot."""
    core_path = PROJECT_ROOT / "nonebot_plugin_akito" / "core" / "story_import.py"
    spec_name = "akito_story_import_standalone"
    existing = sys.modules.get(spec_name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(spec_name, core_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载剧情导入核心：{core_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def enrich_with_llm(payload: dict[str, Any]) -> dict[str, Any]:
    """Generate an evidence-referenced analysis draft when explicitly requested."""
    analysis = payload.setdefault("draft_analysis", {})
    try:
        from openai import OpenAI
    except ImportError:
        analysis.update({"status": "llm_unavailable", "error": "openai package is not installed"})
        return payload

    _load_project_env()
    load_dotenv()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        analysis.update({"status": "llm_unavailable", "error": "DEEPSEEK_API_KEY is not configured"})
        return payload

    segments = payload.get("target_segments", [])
    segment_text = "\n\n".join(
        f"[{segment.get('segment_id')}] evidence_refs={segment.get('evidence_refs')}\n"
        f"日文:\n{segment.get('text_ja', '')}\n中文:\n{segment.get('text_zh', '')}"
        for segment in segments
        if isinstance(segment, dict)
    )
    prompt = (
        "只根据给定证据整理彰人/冬弥剧情草稿。不要补写证据中没有的事实；每个 timeline、"
        "relationship_facts、akito_attitude、toya_traits、style_examples 项都必须包含 evidence_refs。"
        "summary_zh 只能概括给定片段。输出严格 JSON，字段为 summary_zh、timeline、relationship_facts、"
        "akito_attitude、toya_traits、uncertain_or_missing、style_examples、topics。\n\n" + segment_text
    )
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        response = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=[
                {"role": "system", "content": "你是严格的剧情资料整理助手。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=_llm_max_tokens(),
            response_format={"type": "json_object"},
        )
        raw_content = response.choices[0].message.content or ""
        finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "")
    except Exception as error:
        analysis.update({"status": "llm_request_failed", "error": _format_llm_error(error, api_key)})
        return payload
    try:
        generated = _parse_llm_json(raw_content)
    except json.JSONDecodeError as error:
        detail = _format_json_error(error, raw_content)
        if finish_reason:
            detail += f"; finish_reason={finish_reason}"
        analysis.update({"status": "llm_invalid_json", "error": detail})
        return payload

    if not isinstance(generated, dict):
        analysis.update({"status": "llm_invalid_json", "error": "response JSON root is not an object"})
        return payload
    action_indices = {row.get("index") for row in payload.get("actions", []) if isinstance(row, dict)}
    list_fields = {
        "timeline",
        "relationship_facts",
        "akito_attitude",
        "toya_traits",
        "style_examples",
    }
    cleaned: dict[str, Any] = {"status": "ready"}
    if isinstance(generated.get("summary_zh"), str):
        cleaned["summary_zh"] = generated["summary_zh"].strip()
    for key in list_fields:
        value = generated.get(key, [])
        if not isinstance(value, list):
            continue
        cleaned[key] = [
            item
            for item in value
            if isinstance(item, dict)
            and isinstance(item.get("evidence_refs"), list)
            and item.get("evidence_refs")
            and set(item["evidence_refs"]).issubset(action_indices)
        ]
    uncertain = generated.get("uncertain_or_missing", [])
    if isinstance(uncertain, list):
        cleaned["uncertain_or_missing"] = [str(item).strip() for item in uncertain if str(item).strip()]
    topics = generated.get("topics", [])
    if isinstance(topics, list):
        cleaned["topics"] = [str(item).strip() for item in topics if str(item).strip()]
    analysis.clear()
    analysis.update(cleaned)
    return payload


def _deepseek_json(prompt: str, *, max_tokens: int = 1800) -> dict[str, Any]:
    """Call the configured DeepSeek endpoint for review-gated metadata drafts."""
    try:
        from openai import OpenAI
    except ImportError as error:
        raise RuntimeError("openai package is not installed") from error
    _load_project_env()
    load_dotenv()
    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is not configured")
    try:
        client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        )
        response = client.chat.completions.create(
            model=os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"),
            messages=[
                {"role": "system", "content": "你是严格的剧情资料维护助手，只输出 JSON。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.1,
            max_tokens=max(256, min(max_tokens, _llm_max_tokens())),
            response_format={"type": "json_object"},
        )
        parsed = _parse_llm_json(response.choices[0].message.content or "")
    except Exception as error:
        raise RuntimeError(_format_llm_error(error, api_key)) from error
    if not isinstance(parsed, dict):
        raise RuntimeError("LLM response JSON root is not an object")
    return parsed


def suggest_coverage_classification(
    entry: dict[str, Any],
    events: list[dict[str, Any]],
    draft: dict[str, Any] | None,
) -> dict[str, Any]:
    """Suggest coverage labels from compact reviewed metadata."""
    from tools.event_memory.coverage.core import (
        EVENT_TYPES,
        PARTICIPANT_SCOPE_DESCRIPTIONS,
        TIMELINE_STAGE_DESCRIPTIONS,
    )

    material = {
        "url": entry.get("canonical_url"),
        "route_type": entry.get("route_type"),
        "source_speakers": entry.get("source_speakers", []),
        "target_speakers": entry.get("target_speakers", ["akito", "toya"]),
        "published_events": [
            {
                "summary": event.get("summary", ""),
                "topics": event.get("topics", []),
                "relationship_tags": event.get("relationship_tags", []),
            }
            for event in events
        ],
        "draft_analysis": (draft or {}).get("draft_analysis", {}),
    }
    prompt = (
        "根据给定的已审核摘要或分析建议剧情覆盖分类。页面标题不是事件事实。"
        "participant_scope 只描述原剧情场景中出现的角色范围；target_speakers 才是实际写入彰冬目标片段的说话人，二者不要混淆。"
        "timeline_stage 必须恰好选一个；event_types 选 1-3 个；participant_scope 恰好选一个。"
        f"timeline_stage 可选及定义：{json.dumps(TIMELINE_STAGE_DESCRIPTIONS, ensure_ascii=False)}；"
        f"event_types 可选：{list(EVENT_TYPES)}；"
        f"participant_scope 可选及定义：{json.dumps(PARTICIPANT_SCOPE_DESCRIPTIONS, ensure_ascii=False)}。"
        "输出 JSON：{\"timeline_stage\":\"\",\"event_types\":[],\"participant_scope\":\"\"}。\n"
        + json.dumps(material, ensure_ascii=False)
    )
    return _deepseek_json(prompt, max_tokens=800)


def generate_coverage_eval_cases(
    entry: dict[str, Any],
    events: list[dict[str, Any]],
    adjacent_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate review-only query drafts without mutating the formal eval set."""
    material = {
        "target": [
            {
                "event_id": event.get("event_id"),
                "summary": event.get("summary", ""),
                "topics": event.get("topics", []),
            }
            for event in events
        ],
        "adjacent_candidates": [
            {
                "event_id": event.get("event_id"),
                "summary": event.get("summary", ""),
                "topics": event.get("topics", []),
            }
            for event in adjacent_events
        ],
    }
    prompt = (
        "为目标剧情生成待人工审核的检索问题，不做逐字匹配。严格生成 5 条：2 条 positive（不同自然问法）、"
        "1 条 adjacent（仍询问目标剧情，但容易误召回某个相邻候选，必须填写该候选 event_id 到 forbidden_event_ids）、"
        "2 条 negative（与目标剧情相近但事实不成立）。不得把 negative 写成真实事实。"
        "forbidden_event_ids 只能从 adjacent_candidates 中选择；没有候选时留空等待人工补充，禁止编造 ID。"
        "输出 JSON：{\"cases\":[{\"case_type\":\"positive|adjacent|negative\",\"query\":\"\","
        "\"forbidden_event_ids\":[]}]}。\n" + json.dumps(material, ensure_ascii=False)
    )
    parsed = _deepseek_json(prompt, max_tokens=1400)
    cases = parsed.get("cases")
    if not isinstance(cases, list):
        raise RuntimeError("LLM response is missing cases")
    return [item for item in cases if isinstance(item, dict)]
