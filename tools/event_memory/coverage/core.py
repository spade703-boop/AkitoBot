"""Known-source coverage catalog and review-gated evaluation drafts."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
from typing import Any, Callable
from urllib.parse import urlparse, urlunparse

TIMELINE_STAGES = (
    "相遇前",
    "初遇组队",
    "早期搭档",
    "追逐RAD WEEKEND",
    "超越阶段",
    "超越后成长",
    "日常/未定位",
)
EVENT_TYPES = (
    "相遇组队",
    "演出对决",
    "创作练习",
    "冲突受挫",
    "支持照顾",
    "日常互动",
    "学校家庭",
    "旅行海外",
    "庆祝纪念",
    "关系回顾",
)
PARTICIPANT_SCOPES = ("仅彰冬", "VBS集体", "有其他角色", "未知")
PRIORITIES = ("high", "medium", "low")
WORKFLOW_STATUSES = ("todo", "draft", "approved", "published", "rejected", "revision_pending")
CLASSIFICATION_STATUSES = ("unclassified", "suggested", "confirmed")
EVAL_STATUSES = ("missing", "draft", "approved")
_ALLOWED_HOSTS = {"pjsk.moe", "www.pjsk.moe"}
_ROUTE_TYPES = {"event", "unit", "card", "area", "self", "special"}


class CoverageError(ValueError):
    """Raised when coverage metadata cannot be safely updated."""


def canonicalize_story_url(value: object) -> tuple[str, str]:
    parsed = urlparse(str(value or "").strip())
    if parsed.scheme.lower() != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise CoverageError("只接受 pjsk.moe 的 HTTPS 剧情 URL")
    segments = [segment for segment in parsed.path.split("/") if segment]
    route_offset = 1 if segments and "-" in segments[0] else 0
    if len(segments) <= route_offset + 1 or segments[route_offset].lower() != "story":
        raise CoverageError("URL 不是资讯站剧情页面")
    route_type = segments[route_offset + 1].lower()
    if route_type not in _ROUTE_TYPES:
        raise CoverageError(f"暂不支持剧情路由：{route_type}")
    canonical = urlunparse(("https", parsed.hostname.lower(), "/" + "/".join(segments) + "/", "", "", ""))
    return canonical, route_type


def source_id_for_url(canonical_url: str) -> str:
    digest = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()[:16]
    return f"source-{digest}"


def _read_json(path: Path, default: object) -> Any:
    if not path.exists():
        return deepcopy(default)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as error:
        raise CoverageError(f"无法读取 JSON：{path}") from error


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def _empty_classification() -> dict[str, Any]:
    return {"timeline_stage": "", "event_types": [], "participant_scope": "未知"}


def _normalize_classification(value: object, *, allow_empty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CoverageError("classification 必须是对象")
    timeline_stage = str(value.get("timeline_stage") or "").strip()
    event_types = list(dict.fromkeys(str(item).strip() for item in value.get("event_types", []) if str(item).strip()))
    participant_scope = str(value.get("participant_scope") or "未知").strip()
    if timeline_stage not in TIMELINE_STAGES and not (allow_empty and not timeline_stage):
        raise CoverageError("timeline_stage 不在允许范围内")
    if not event_types and not allow_empty:
        raise CoverageError("event_types 至少选择一项")
    if any(item not in EVENT_TYPES for item in event_types):
        raise CoverageError("event_types 包含不支持的分类")
    if participant_scope not in PARTICIPANT_SCOPES:
        raise CoverageError("participant_scope 不在允许范围内")
    return {
        "timeline_stage": timeline_stage,
        "event_types": event_types,
        "participant_scope": participant_scope,
    }


def _new_entry(canonical_url: str, route_type: str) -> dict[str, Any]:
    return {
        "source_id": source_id_for_url(canonical_url),
        "canonical_url": canonical_url,
        "route_type": route_type,
        "display_label": "",
        "priority": "medium",
        "classification": _empty_classification(),
        "suggested_classification": None,
        "classification_status": "unclassified",
        "draft_id": "",
        "event_ids": [],
        "workflow_status": "todo",
        "eval_status": "missing",
        "origins": [],
        "notes": "",
    }


def _load_drafts(data_dir: Path) -> list[dict[str, Any]]:
    directory = data_dir / "event_memory" / "story_import" / "drafts"
    rows: list[dict[str, Any]] = []
    if not directory.exists():
        return rows
    for path in sorted(directory.glob("story-*.json")):
        payload = _read_json(path, {})
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _published_events(data_dir: Path) -> list[dict[str, Any]]:
    payload = _read_json(data_dir / "content" / "akito_event_memories.json", {"events": []})
    events = payload.get("events", []) if isinstance(payload, dict) else []
    return [
        event for event in events
        if isinstance(event, dict)
        and event.get("source_kind") == "curated_story"
        and isinstance(event.get("source"), dict)
        and event["source"].get("url")
    ]


def _draft_workflow(draft: dict[str, Any], event_ids: list[str]) -> str:
    review_status = str(draft.get("review", {}).get("status") or draft.get("status") or "draft")
    published_id = str(draft.get("publish", {}).get("event_memory_id") or "")
    if event_ids and review_status == "approved" and not published_id:
        return "revision_pending"
    if published_id:
        return "published"
    if review_status == "rejected":
        return "rejected"
    if review_status == "approved":
        return "approved"
    return "draft"


def _eval_source_status(source_id: str, event_ids: list[str], eval_drafts: dict[str, Any], eval_set: dict[str, Any]) -> str:
    formal_ids = {
        str(event_id)
        for case in eval_set.get("cases", [])
        if isinstance(case, dict)
        for event_id in case.get("expected_event_ids", [])
    }
    if set(event_ids) & formal_ids:
        return "approved"
    drafts = [item for item in eval_drafts.get("drafts", []) if isinstance(item, dict) and item.get("source_id") == source_id]
    if any(item.get("status") == "approved" for item in drafts):
        return "approved"
    if drafts:
        return "draft"
    return "missing"


def _eval_draft_id(source_id: str, eval_drafts: dict[str, Any]) -> str:
    draft = next(
        (
            item for item in eval_drafts.get("drafts", [])
            if isinstance(item, dict) and item.get("source_id") == source_id
        ),
        None,
    )
    return str(draft.get("draft_id") or "") if draft else ""


class CoverageStore:
    """Persist known-source metadata without copying story dialogue."""

    def __init__(
        self,
        *,
        data_dir: Path,
        catalog_path: Path,
        eval_drafts_path: Path,
        eval_set_path: Path,
        report_path: Path | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.catalog_path = Path(catalog_path)
        self.eval_drafts_path = Path(eval_drafts_path)
        self.eval_set_path = Path(eval_set_path)
        self.report_path = Path(report_path) if report_path else None

    def load_catalog(self) -> dict[str, Any]:
        payload = _read_json(
            self.catalog_path,
            {"schema_version": 1, "scope": "known_sources_only", "sources": []},
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
            raise CoverageError("覆盖台账格式不正确")
        return payload

    def load_eval_drafts(self) -> dict[str, Any]:
        payload = _read_json(self.eval_drafts_path, {"schema_version": 1, "drafts": []})
        if not isinstance(payload, dict) or not isinstance(payload.get("drafts"), list):
            raise CoverageError("评测草稿格式不正确")
        return payload

    def load_eval_set(self) -> dict[str, Any]:
        payload = _read_json(self.eval_set_path, {"schema_version": 2, "cases": []})
        if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
            raise CoverageError("正式评测集格式不正确")
        return payload

    def _save_catalog(self, payload: dict[str, Any]) -> None:
        payload["sources"] = sorted(payload["sources"], key=lambda item: item["source_id"])
        _write_json_atomic(self.catalog_path, payload)
        if self.report_path is not None:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            self.report_path.write_text(self.render_report(payload), encoding="utf-8")

    def sync(self) -> dict[str, Any]:
        catalog = self.load_catalog()
        by_url = {str(item.get("canonical_url")): deepcopy(item) for item in catalog["sources"] if isinstance(item, dict)}
        drafts = _load_drafts(self.data_dir)
        events = _published_events(self.data_dir)
        events_by_url: dict[str, list[dict[str, Any]]] = {}
        for event in events:
            canonical_url, _ = canonicalize_story_url(event["source"]["url"])
            events_by_url.setdefault(canonical_url, []).append(event)

        for draft in drafts:
            source = draft.get("source", {})
            if not isinstance(source, dict) or not source.get("canonical_url"):
                continue
            canonical_url, route_type = canonicalize_story_url(source["canonical_url"])
            entry = by_url.get(canonical_url, _new_entry(canonical_url, route_type))
            linked_events = events_by_url.get(canonical_url, [])
            entry["route_type"] = route_type
            entry["display_label"] = str(
                draft.get("story", {}).get("episode_title")
                or draft.get("page", {}).get("title")
                or entry.get("display_label")
                or canonical_url
            )
            entry["draft_id"] = str(draft.get("draft_id") or "")
            entry["event_ids"] = sorted({str(event.get("event_id")) for event in linked_events if event.get("event_id")})
            entry["workflow_status"] = _draft_workflow(draft, entry["event_ids"])
            entry["origins"] = sorted(set(entry.get("origins", [])) | {"draft"} | ({"published"} if linked_events else set()))
            by_url[canonical_url] = entry

        for canonical_url, linked_events in events_by_url.items():
            _, route_type = canonicalize_story_url(canonical_url)
            entry = by_url.get(canonical_url, _new_entry(canonical_url, route_type))
            entry["event_ids"] = sorted({str(event.get("event_id")) for event in linked_events if event.get("event_id")})
            if not entry.get("draft_id"):
                entry["workflow_status"] = "published"
            entry["origins"] = sorted(set(entry.get("origins", [])) | {"published"})
            if not entry.get("display_label"):
                entry["display_label"] = canonical_url
            by_url[canonical_url] = entry

        eval_drafts = self.load_eval_drafts()
        eval_set = self.load_eval_set()
        for entry in by_url.values():
            entry["eval_status"] = _eval_source_status(entry["source_id"], entry.get("event_ids", []), eval_drafts, eval_set)
            entry["eval_draft_id"] = _eval_draft_id(entry["source_id"], eval_drafts)
        catalog["sources"] = list(by_url.values())
        self._save_catalog(catalog)
        return catalog

    def add_source(self, url: str, *, priority: str = "medium", notes: str = "") -> dict[str, Any]:
        canonical_url, route_type = canonicalize_story_url(url)
        if priority not in PRIORITIES:
            raise CoverageError("priority 不在允许范围内")
        catalog = self.load_catalog()
        existing = next((item for item in catalog["sources"] if item.get("canonical_url") == canonical_url), None)
        if existing is None:
            existing = _new_entry(canonical_url, route_type)
            catalog["sources"].append(existing)
        existing["origins"] = sorted(set(existing.get("origins", [])) | {"manual"})
        existing["priority"] = priority
        if notes.strip():
            existing["notes"] = notes.strip()
        self._save_catalog(catalog)
        return deepcopy(existing)

    def get_source(self, source_id: str) -> dict[str, Any]:
        catalog = self.load_catalog()
        entry = next((item for item in catalog["sources"] if item.get("source_id") == source_id), None)
        if entry is None:
            raise CoverageError("找不到覆盖来源")
        return deepcopy(entry)

    def update_source(
        self,
        source_id: str,
        *,
        priority: str | None = None,
        notes: str | None = None,
        classification: dict[str, Any] | None = None,
        confirm_classification: bool = False,
    ) -> dict[str, Any]:
        catalog = self.load_catalog()
        entry = next((item for item in catalog["sources"] if item.get("source_id") == source_id), None)
        if entry is None:
            raise CoverageError("找不到覆盖来源")
        if priority is not None:
            if priority not in PRIORITIES:
                raise CoverageError("priority 不在允许范围内")
            entry["priority"] = priority
        if notes is not None:
            entry["notes"] = str(notes).strip()
        if classification is not None:
            entry["classification"] = _normalize_classification(classification, allow_empty=not confirm_classification)
            entry["classification_status"] = "confirmed" if confirm_classification else "unclassified"
        elif confirm_classification:
            suggestion = entry.get("suggested_classification")
            entry["classification"] = _normalize_classification(suggestion)
            entry["classification_status"] = "confirmed"
        self._save_catalog(catalog)
        return deepcopy(entry)

    def save_suggestion(self, source_id: str, suggestion: dict[str, Any]) -> dict[str, Any]:
        catalog = self.load_catalog()
        entry = next((item for item in catalog["sources"] if item.get("source_id") == source_id), None)
        if entry is None:
            raise CoverageError("找不到覆盖来源")
        entry["suggested_classification"] = _normalize_classification(suggestion)
        entry["classification_status"] = "suggested"
        self._save_catalog(catalog)
        return deepcopy(entry)

    def list_sources(self, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        rows = self.load_catalog()["sources"]
        filters = filters or {}
        for field in ("workflow_status", "priority", "classification_status", "eval_status"):
            value = str(filters.get(field) or "")
            if value:
                rows = [item for item in rows if item.get(field) == value]
        timeline_stage = str(filters.get("timeline_stage") or "")
        if timeline_stage:
            rows = [item for item in rows if item.get("classification", {}).get("timeline_stage") == timeline_stage]
        return deepcopy(rows)

    def summary(self) -> dict[str, Any]:
        rows = self.load_catalog()["sources"]
        return {
            "scope": "known_sources_only",
            "known_sources": len(rows),
            "workflow": dict(Counter(str(item.get("workflow_status") or "unknown") for item in rows)),
            "priority": dict(Counter(str(item.get("priority") or "unknown") for item in rows)),
            "classification": dict(Counter(str(item.get("classification_status") or "unknown") for item in rows)),
            "evaluation": dict(Counter(str(item.get("eval_status") or "unknown") for item in rows)),
        }

    def render_report(self, catalog: dict[str, Any] | None = None) -> str:
        provided_catalog = catalog is not None
        catalog = catalog or self.load_catalog()
        rows = catalog["sources"]
        summary = self.summary() if not provided_catalog else {
            "known_sources": len(rows),
            "workflow": dict(Counter(str(item.get("workflow_status") or "unknown") for item in rows)),
            "classification": dict(Counter(str(item.get("classification_status") or "unknown") for item in rows)),
            "evaluation": dict(Counter(str(item.get("eval_status") or "unknown") for item in rows)),
        }
        lines = [
            "# 彰冬剧情已知来源覆盖报告",
            "",
            "> 该报告只统计已抓取、已发布和人工加入待办的已知来源，不代表全游戏剧情覆盖率。",
            "",
            f"- 已知来源：{summary['known_sources']}",
            f"- 工作流状态：`{json.dumps(summary['workflow'], ensure_ascii=False)}`",
            f"- 分类状态：`{json.dumps(summary['classification'], ensure_ascii=False)}`",
            f"- 评测状态：`{json.dumps(summary['evaluation'], ensure_ascii=False)}`",
            "",
            "| 来源 | 优先级 | 工作流 | 分类 | 评测 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for item in sorted(rows, key=lambda row: (PRIORITIES.index(row.get("priority", "low")), row["source_id"])):
            classification = item.get("classification", {})
            labels = "/".join(
                [classification.get("timeline_stage", ""), *classification.get("event_types", [])]
            ).strip("/") or "未确认"
            lines.append(
                f"| [{item.get('display_label') or item['source_id']}]({item['canonical_url']}) | "
                f"{item['priority']} | {item['workflow_status']} | {labels} | {item['eval_status']} |"
            )
        return "\n".join(lines) + "\n"

    def source_material(self, source_id: str) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]:
        entry = self.get_source(source_id)
        events = [event for event in _published_events(self.data_dir) if event.get("event_id") in entry.get("event_ids", [])]
        draft = next((item for item in _load_drafts(self.data_dir) if item.get("draft_id") == entry.get("draft_id")), None)
        return entry, events, deepcopy(draft) if draft else None

    def save_eval_draft(self, source_id: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        entry = self.get_source(source_id)
        if entry.get("workflow_status") != "published" or not entry.get("event_ids"):
            raise CoverageError("只有已发布的人工剧情可以生成评测草稿")
        normalized_cases = self._validate_eval_cases(cases, entry["event_ids"], allow_incomplete=True)
        payload = self.load_eval_drafts()
        draft_id = f"eval-{source_id.removeprefix('source-')}"
        draft = {
            "draft_id": draft_id,
            "source_id": source_id,
            "event_ids": entry["event_ids"],
            "status": "draft",
            "cases": normalized_cases,
        }
        payload["drafts"] = [item for item in payload["drafts"] if item.get("draft_id") != draft_id] + [draft]
        _write_json_atomic(self.eval_drafts_path, payload)
        self.sync()
        return deepcopy(draft)

    def get_eval_draft(self, draft_id: str) -> dict[str, Any]:
        draft = next((item for item in self.load_eval_drafts()["drafts"] if item.get("draft_id") == draft_id), None)
        if draft is None:
            raise CoverageError("找不到评测草稿")
        return deepcopy(draft)

    def update_eval_draft(self, draft_id: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        payload = self.load_eval_drafts()
        draft = next((item for item in payload["drafts"] if item.get("draft_id") == draft_id), None)
        if draft is None:
            raise CoverageError("找不到评测草稿")
        if draft.get("status") == "approved":
            raise CoverageError("已批准评测草稿不可直接修改")
        draft["cases"] = self._validate_eval_cases(cases, draft.get("event_ids", []), allow_incomplete=True)
        _write_json_atomic(self.eval_drafts_path, payload)
        return deepcopy(draft)

    @staticmethod
    def _validate_eval_cases(cases: object, event_ids: list[str], *, allow_incomplete: bool) -> list[dict[str, Any]]:
        if not isinstance(cases, list):
            raise CoverageError("cases 必须是数组")
        normalized: list[dict[str, Any]] = []
        for item in cases:
            if not isinstance(item, dict):
                raise CoverageError("评测 case 必须是对象")
            case_type = str(item.get("case_type") or "").strip()
            if case_type not in {"positive", "adjacent", "negative"}:
                raise CoverageError("case_type 必须是 positive、adjacent 或 negative")
            query = str(item.get("query") or "").strip()
            if not query and not allow_incomplete:
                raise CoverageError("批准前所有 query 都必须填写")
            forbidden = list(dict.fromkeys(str(value) for value in item.get("forbidden_event_ids", []) if str(value)))
            normalized.append(
                {
                    "case_type": case_type,
                    "query": query,
                    "expected_event_ids": list(event_ids) if case_type != "negative" else [],
                    "forbidden_event_ids": forbidden if case_type == "adjacent" else [],
                }
            )
        return normalized

    def approve_eval_draft(self, draft_id: str) -> dict[str, Any]:
        draft_payload = self.load_eval_drafts()
        draft = next((item for item in draft_payload["drafts"] if item.get("draft_id") == draft_id), None)
        if draft is None:
            raise CoverageError("找不到评测草稿")
        cases = self._validate_eval_cases(draft.get("cases"), draft.get("event_ids", []), allow_incomplete=False)
        counts = Counter(item["case_type"] for item in cases)
        if counts["positive"] < 2 or counts["adjacent"] < 1 or counts["negative"] < 2:
            raise CoverageError("批准前至少需要 2 个正例、1 个相邻干扰例和 2 个事实错误负例")
        if any(item["case_type"] == "adjacent" and not item["forbidden_event_ids"] for item in cases):
            raise CoverageError("相邻干扰例必须指定 forbidden_event_ids")
        inventory = _read_json(self.data_dir / "content" / "akito_event_memories.json", {"events": []})
        known_event_ids = {
            str(event.get("event_id"))
            for event in inventory.get("events", [])
            if isinstance(event, dict) and event.get("event_id")
        }
        target_ids = set(draft.get("event_ids", []))
        forbidden_ids = {
            event_id
            for item in cases
            if item["case_type"] == "adjacent"
            for event_id in item["forbidden_event_ids"]
        }
        if forbidden_ids - known_event_ids:
            raise CoverageError("相邻干扰例包含事件库中不存在的 forbidden_event_ids")
        if forbidden_ids & target_ids:
            raise CoverageError("相邻干扰例不能把目标事件本身列为 forbidden_event_ids")
        eval_set = self.load_eval_set()
        prefix = draft_id.replace("eval-", "coverage-")
        generated: list[dict[str, Any]] = []
        type_counts: Counter[str] = Counter()
        for item in cases:
            type_counts[item["case_type"]] += 1
            suffix = {"positive": "p", "adjacent": "a", "negative": "n"}[item["case_type"]]
            generated.append(
                {
                    "id": f"{prefix}-{suffix}{type_counts[item['case_type']]:02d}",
                    "kind": "negative" if item["case_type"] == "negative" else "positive",
                    "query": item["query"],
                    "expected_event_ids": item["expected_event_ids"],
                    **({"forbidden_event_ids": item["forbidden_event_ids"]} if item["forbidden_event_ids"] else {}),
                }
            )
        eval_set["cases"] = [
            item for item in eval_set["cases"]
            if not str(item.get("id") or "").startswith(prefix + "-")
        ] + generated
        _write_json_atomic(self.eval_set_path, eval_set)
        draft["status"] = "approved"
        draft["cases"] = cases
        _write_json_atomic(self.eval_drafts_path, draft_payload)
        self.sync()
        return deepcopy(draft)


def default_store(project_root: Path, data_dir: Path | None = None) -> CoverageStore:
    root = Path(project_root)
    return CoverageStore(
        data_dir=Path(data_dir or root / "data"),
        catalog_path=root / "tools" / "event_memory" / "coverage" / "catalog.json",
        eval_drafts_path=root / "tools" / "event_memory" / "coverage" / "eval_drafts.json",
        eval_set_path=root / "tools" / "event_memory" / "retrieval" / "eval_set.json",
        report_path=root / "docs" / "conversation_ai" / "event_memory" / "COVERAGE_REPORT.md",
    )


def find_adjacent_events(events: list[dict[str, Any]], selected_ids: list[str], limit: int = 5) -> list[dict[str, Any]]:
    selected = [event for event in events if str(event.get("event_id")) in selected_ids]
    selected_terms = {
        str(value).strip()
        for event in selected
        for field in ("topics", "keywords", "relationship_tags")
        for value in (event.get(field, []) if isinstance(event.get(field), list) else [])
        if str(value).strip()
    }
    candidates: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        if str(event.get("event_id")) in selected_ids:
            continue
        terms = {
            str(value).strip()
            for field in ("topics", "keywords", "relationship_tags")
            for value in (event.get(field, []) if isinstance(event.get(field), list) else [])
            if str(value).strip()
        }
        overlap = len(selected_terms & terms)
        if overlap:
            candidates.append((overlap, event))
    candidates.sort(key=lambda item: (-item[0], str(item[1].get("event_id") or "")))
    return [deepcopy(event) for _, event in candidates[:limit]]


ClassificationGenerator = Callable[[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None], dict[str, Any]]
EvalGenerator = Callable[[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]], list[dict[str, Any]]]
