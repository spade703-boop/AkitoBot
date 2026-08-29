"""Local browser UI and JSON API for the standalone story importer."""

from __future__ import annotations

import argparse
from copy import deepcopy
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import sys
from threading import RLock
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

try:
    from .runtime import (
        enrich_with_llm,
        generate_coverage_eval_cases,
        load_story_import_module,
        suggest_coverage_classification,
    )
except ImportError:  # pragma: no cover - direct ``python tools/...`` execution
    from runtime import (
        enrich_with_llm,
        generate_coverage_eval_cases,
        load_story_import_module,
        suggest_coverage_classification,
    )

from tools.event_memory.coverage.core import (
    CLASSIFICATION_STATUSES,
    EVAL_STATUSES,
    EVENT_TYPES,
    PARTICIPANT_SCOPES,
    PRIORITIES,
    TIMELINE_STAGES,
    WORKFLOW_STATUSES,
    CoverageError,
    CoverageStore,
    default_store,
    find_adjacent_events,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


PROJECT_ROOT = Path(__file__).resolve().parents[3]
UI_PATH = Path(__file__).resolve().parent / "ui" / "index.html"
_DRAFT_ID_PATTERN = re.compile(r"^story-[a-f0-9]{16}$")
_ANALYSIS_FIELDS = {
    "summary_zh",
    "timeline",
    "relationship_facts",
    "akito_attitude",
    "toya_traits",
    "uncertain_or_missing",
    "style_examples",
    "topics",
}
_REF_FIELDS = {"timeline", "relationship_facts", "akito_attitude", "toya_traits", "style_examples"}
_TEXT_LIST_FIELDS = {"uncertain_or_missing", "topics"}


class WebRequestError(ValueError):
    def __init__(self, message: str, *, status: int = HTTPStatus.BAD_REQUEST, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = int(status)
        self.details = details or {}


class StoryImportService:
    """State-free request operations with a process-local write lock."""

    def __init__(self, data_dir: Path, *, core: Any | None = None, coverage: CoverageStore | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.core = core or load_story_import_module()
        if coverage is not None:
            self.coverage = coverage
        elif self.data_dir == (PROJECT_ROOT / "data").resolve():
            self.coverage = default_store(PROJECT_ROOT, self.data_dir)
        else:
            coverage_dir = self.data_dir / "event_memory" / "coverage"
            self.coverage = CoverageStore(
                data_dir=self.data_dir,
                catalog_path=coverage_dir / "catalog.json",
                eval_drafts_path=coverage_dir / "eval_drafts.json",
                eval_set_path=coverage_dir / "eval_set.json",
                report_path=coverage_dir / "COVERAGE_REPORT.md",
            )
        self._write_lock = RLock()

    def health(self) -> dict[str, Any]:
        return {"ok": True, "data_dir": str(self.data_dir), "ui": str(UI_PATH)}

    def list_drafts(self, status: str | None = None) -> list[dict[str, Any]]:
        directory = self.data_dir / "event_memory" / "story_import" / "drafts"
        rows: list[dict[str, Any]] = []
        if not directory.exists():
            return rows
        for path in sorted(directory.glob("story-*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            current_status = str(payload.get("review", {}).get("status") or payload.get("status") or "unknown")
            if status and current_status != status:
                continue
            rows.append(
                {
                    "draft_id": str(payload.get("draft_id") or path.stem),
                    "status": current_status,
                    "title": str(payload.get("story", {}).get("episode_title") or payload.get("page", {}).get("title") or ""),
                    "route_type": str(payload.get("source", {}).get("route_type") or ""),
                    "locale": str(payload.get("source", {}).get("locale") or ""),
                    "actions": len(payload.get("actions", [])) if isinstance(payload.get("actions"), list) else 0,
                    "target_segments": len(payload.get("target_segments", [])) if isinstance(payload.get("target_segments"), list) else 0,
                    "path": str(path),
                }
            )
        return rows

    def get_draft(self, draft_id: str) -> dict[str, Any]:
        try:
            payload, _ = self.core.load_draft(draft_id, data_dir=self.data_dir)
        except self.core.StoryImportError as error:
            raise WebRequestError(str(error), status=HTTPStatus.NOT_FOUND) from error
        return payload

    def capture(self, url: str, *, enrich: bool = False) -> dict[str, Any]:
        if not str(url or "").strip():
            raise WebRequestError("url 不能为空")
        try:
            payload = self.core.capture_story(url, data_dir=self.data_dir, enrich=enrich)
            if enrich:
                payload = enrich_with_llm(payload)
            with self._write_lock:
                self.core.save_draft(payload, data_dir=self.data_dir)
                self.coverage.sync()
        except self.core.StoryImportError as error:
            raise WebRequestError(str(error)) from error
        return payload

    def update_analysis(self, draft_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise WebRequestError("analysis 必须是对象")
        unknown = sorted(set(patch) - _ANALYSIS_FIELDS)
        if unknown:
            raise WebRequestError(f"不允许修改分析字段：{', '.join(unknown)}")
        payload = self.get_draft(draft_id)
        actions = payload.get("actions", [])
        action_indices = {row.get("index") for row in actions if isinstance(row, dict)}
        analysis = deepcopy(payload.get("draft_analysis") or {})
        for field, value in patch.items():
            if field == "summary_zh":
                if not isinstance(value, str):
                    raise WebRequestError("summary_zh 必须是字符串")
                analysis[field] = value.strip()
            elif field in _TEXT_LIST_FIELDS:
                if not isinstance(value, list):
                    raise WebRequestError(f"{field} 必须是数组")
                analysis[field] = [str(item).strip() for item in value if str(item).strip()]
            else:
                if not isinstance(value, list):
                    raise WebRequestError(f"{field} 必须是数组")
                valid_items = []
                for item in value:
                    if not isinstance(item, dict):
                        raise WebRequestError(f"{field} 只能包含对象")
                    refs = item.get("evidence_refs")
                    if not isinstance(refs, list) or not refs or not set(refs).issubset(action_indices):
                        raise WebRequestError(f"{field} 中存在无效 evidence_refs")
                    valid_items.append(deepcopy(item))
                analysis[field] = valid_items
        payload["draft_analysis"] = analysis
        payload["review"] = {"status": "draft", "reviewer": "", "reviewed_at": "", "notes": []}
        payload["status"] = "draft"
        payload["publish"] = {"event_memory_id": "", "published_at": ""}
        try:
            with self._write_lock:
                self.core.save_draft(payload, data_dir=self.data_dir)
                self.coverage.sync()
        except self.core.StoryImportError as error:
            raise WebRequestError(str(error)) from error
        return payload

    def review(self, draft_id: str, *, status: str, reviewer: str, note: str) -> dict[str, Any]:
        payload = self.get_draft(draft_id)
        try:
            payload = self.core.update_review(payload, status, reviewer=reviewer or "web", note=note)
            with self._write_lock:
                self.core.save_draft(payload, data_dir=self.data_dir)
                self.coverage.sync()
        except self.core.StoryImportError as error:
            raise WebRequestError(str(error)) from error
        return payload

    def dedupe_preview(self, draft_id: str) -> dict[str, Any]:
        payload = self.get_draft(draft_id)
        try:
            return self.core.preview_event_memory(payload, data_dir=self.data_dir)
        except self.core.StoryImportError as error:
            raise WebRequestError(str(error)) from error

    def publish(self, draft_id: str, *, confirm_revision: bool = False) -> dict[str, Any]:
        with self._write_lock:
            payload = self.get_draft(draft_id)
            try:
                preview = self.core.preview_event_memory(payload, data_dir=self.data_dir)
                if preview["status"] == "duplicate_content":
                    raise WebRequestError(
                        "剧情内容与已有事件重复；请查看已有来源",
                        status=HTTPStatus.CONFLICT,
                        details={"preview": preview},
                    )
                if preview["status"] == "revision" and not confirm_revision:
                    raise WebRequestError(
                        "同一剧情已有不同版本；请先确认修订",
                        status=HTTPStatus.CONFLICT,
                        details={"preview": preview},
                    )
                path, event_id = self.core.merge_event_memory(
                    payload,
                    data_dir=self.data_dir,
                    confirm_revision=confirm_revision,
                )
                payload["publish"] = {"event_memory_id": event_id, "published_at": self.core._now_iso()}
                self.core.save_draft(payload, data_dir=self.data_dir)
                self.coverage.sync()
            except self.core.StoryImportError as error:
                raise WebRequestError(str(error)) from error
        return {"event_id": event_id, "path": str(path), "dedupe": preview, "draft": payload}

    def coverage_payload(self, filters: dict[str, str] | None = None) -> dict[str, Any]:
        return {
            "sources": self.coverage.list_sources(filters),
            "summary": self.coverage.summary(),
            "options": {
                "timeline_stages": TIMELINE_STAGES,
                "event_types": EVENT_TYPES,
                "participant_scopes": PARTICIPANT_SCOPES,
                "priorities": PRIORITIES,
                "workflow_statuses": WORKFLOW_STATUSES,
                "classification_statuses": CLASSIFICATION_STATUSES,
                "eval_statuses": EVAL_STATUSES,
            },
        }

    def sync_coverage(self) -> dict[str, Any]:
        with self._write_lock:
            self.coverage.sync()
        return self.coverage_payload()

    def add_coverage_source(self, *, url: str, priority: str, notes: str) -> dict[str, Any]:
        with self._write_lock:
            return self.coverage.add_source(url, priority=priority, notes=notes)

    def update_coverage_source(self, source_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        classification = patch.get("classification")
        if classification is not None and not isinstance(classification, dict):
            raise WebRequestError("classification 必须是对象")
        with self._write_lock:
            return self.coverage.update_source(
                source_id,
                priority=str(patch["priority"]) if "priority" in patch else None,
                notes=str(patch["notes"]) if "notes" in patch else None,
                classification=classification,
                confirm_classification=bool(patch.get("confirm_classification")),
            )

    def suggest_classification(self, source_id: str) -> dict[str, Any]:
        entry, events, draft = self.coverage.source_material(source_id)
        try:
            suggestion = suggest_coverage_classification(entry, events, draft)
            with self._write_lock:
                return self.coverage.save_suggestion(source_id, suggestion)
        except (RuntimeError, CoverageError) as error:
            raise WebRequestError(f"分类建议失败：{error}") from error

    def generate_eval_draft(self, source_id: str) -> dict[str, Any]:
        entry, events, _draft = self.coverage.source_material(source_id)
        inventory_path = self.data_dir / "content" / "akito_event_memories.json"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8-sig")) if inventory_path.exists() else {"events": []}
        adjacent = find_adjacent_events(
            [item for item in inventory.get("events", []) if isinstance(item, dict)],
            entry.get("event_ids", []),
        )
        try:
            cases = generate_coverage_eval_cases(entry, events, adjacent)
            with self._write_lock:
                return self.coverage.save_eval_draft(source_id, cases)
        except (RuntimeError, CoverageError) as error:
            raise WebRequestError(f"评测草稿生成失败：{error}") from error

    def get_eval_draft(self, draft_id: str) -> dict[str, Any]:
        return self.coverage.get_eval_draft(draft_id)

    def update_eval_draft(self, draft_id: str, cases: list[dict[str, Any]]) -> dict[str, Any]:
        with self._write_lock:
            return self.coverage.update_eval_draft(draft_id, cases)

    def approve_eval_draft(self, draft_id: str) -> dict[str, Any]:
        with self._write_lock:
            return self.coverage.approve_eval_draft(draft_id)


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    try:
        length = int(handler.headers.get("Content-Length", "0"))
    except ValueError as error:
        raise WebRequestError("Content-Length 无效") from error
    if length < 0 or length > 2 * 1024 * 1024:
        raise WebRequestError("请求体超过 2 MiB 限制")
    try:
        value = json.loads(handler.rfile.read(length).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WebRequestError("请求体必须是 UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise WebRequestError("请求体必须是 JSON 对象")
    return value


def _draft_id_from_path(path: str) -> str:
    draft_id = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    if not _DRAFT_ID_PATTERN.fullmatch(draft_id):
        raise WebRequestError("draft_id 格式无效")
    return draft_id


def _coverage_id_from_path(path: str) -> str:
    value = unquote(path.rstrip("/").rsplit("/", 1)[-1])
    if not re.fullmatch(r"(?:source|eval)-[a-f0-9]{16}", value):
        raise WebRequestError("覆盖来源或评测草稿 ID 格式无效")
    return value


def make_handler(service: StoryImportService) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AkitoStoryImport/1.0"

        def _send_json(self, value: object, *, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error(self, error: Exception) -> None:
            if isinstance(error, WebRequestError):
                self._send_json({"error": str(error), **error.details}, status=error.status)
            elif isinstance(error, CoverageError):
                self._send_json({"error": str(error)}, status=HTTPStatus.BAD_REQUEST)
            else:
                self._send_json({"error": f"{type(error).__name__}: {error}"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

        def _send_ui(self) -> None:
            try:
                body = UI_PATH.read_bytes()
            except OSError as error:
                self._send_error(WebRequestError(f"前端文件无法读取：{error}", status=HTTPStatus.INTERNAL_SERVER_ERROR))
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            try:
                if parsed.path in {"/", "/index.html"}:
                    self._send_ui()
                    return
                if parsed.path == "/api/health":
                    self._send_json(service.health())
                    return
                if parsed.path == "/api/drafts":
                    status = parse_qs(parsed.query).get("status", [None])[0]
                    self._send_json({"drafts": service.list_drafts(status)})
                    return
                if parsed.path.startswith("/api/drafts/"):
                    self._send_json({"draft": service.get_draft(_draft_id_from_path(parsed.path))})
                    return
                if parsed.path == "/api/coverage":
                    query = {key: values[0] for key, values in parse_qs(parsed.query).items() if values}
                    self._send_json(service.coverage_payload(query))
                    return
                if parsed.path.startswith("/api/coverage/evals/"):
                    self._send_json({"draft": service.get_eval_draft(_coverage_id_from_path(parsed.path))})
                    return
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as error:
                self._send_error(error)

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            try:
                body = _json_body(self)
                if parsed.path == "/api/capture":
                    url = body.get("url")
                    enrich = body.get("enrich", "none") == "llm"
                    self._send_json({"draft": service.capture(str(url or ""), enrich=enrich)}, status=HTTPStatus.CREATED)
                    return
                if parsed.path == "/api/coverage/sync":
                    self._send_json(service.sync_coverage())
                    return
                if parsed.path == "/api/coverage/sources":
                    source = service.add_coverage_source(
                        url=str(body.get("url") or ""),
                        priority=str(body.get("priority") or "medium"),
                        notes=str(body.get("notes") or ""),
                    )
                    self._send_json({"source": source}, status=HTTPStatus.CREATED)
                    return
                if parsed.path.endswith("/suggest-classification"):
                    source_id = _coverage_id_from_path(parsed.path[: -len("/suggest-classification")])
                    self._send_json({"source": service.suggest_classification(source_id)})
                    return
                if parsed.path.endswith("/generate-eval"):
                    source_id = _coverage_id_from_path(parsed.path[: -len("/generate-eval")])
                    self._send_json({"draft": service.generate_eval_draft(source_id)}, status=HTTPStatus.CREATED)
                    return
                if parsed.path.endswith("/approve") and parsed.path.startswith("/api/coverage/evals/"):
                    draft_id = _coverage_id_from_path(parsed.path[: -len("/approve")])
                    self._send_json({"draft": service.approve_eval_draft(draft_id)})
                    return
                if parsed.path.endswith("/review"):
                    draft_id = _draft_id_from_path(parsed.path[: -len("/review")])
                    self._send_json(
                        {"draft": service.review(
                            draft_id,
                            status=str(body.get("status") or ""),
                            reviewer=str(body.get("reviewer") or "web"),
                            note=str(body.get("note") or ""),
                        )}
                    )
                    return
                if parsed.path.endswith("/dedupe-preview"):
                    draft_id = _draft_id_from_path(parsed.path[: -len("/dedupe-preview")])
                    self._send_json({"preview": service.dedupe_preview(draft_id)})
                    return
                if parsed.path.endswith("/publish"):
                    draft_id = _draft_id_from_path(parsed.path[: -len("/publish")])
                    self._send_json(service.publish(draft_id, confirm_revision=bool(body.get("confirm_revision"))))
                    return
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as error:
                self._send_error(error)

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlsplit(self.path)
            try:
                body = _json_body(self)
                if parsed.path.startswith("/api/coverage/sources/"):
                    source_id = _coverage_id_from_path(parsed.path)
                    self._send_json({"source": service.update_coverage_source(source_id, body)})
                    return
                if parsed.path.startswith("/api/coverage/evals/"):
                    draft_id = _coverage_id_from_path(parsed.path)
                    cases = body.get("cases")
                    if not isinstance(cases, list):
                        raise WebRequestError("cases 必须是数组")
                    self._send_json({"draft": service.update_eval_draft(draft_id, cases)})
                    return
                if parsed.path.endswith("/analysis"):
                    draft_id = _draft_id_from_path(parsed.path[: -len("/analysis")])
                    analysis = body.get("analysis", body)
                    self._send_json({"draft": service.update_analysis(draft_id, analysis)})
                    return
                self._send_json({"error": "not found"}, status=HTTPStatus.NOT_FOUND)
            except Exception as error:
                self._send_error(error)

        def log_message(self, format: str, *args: object) -> None:
            print(f"[story-import-web] {self.address_string()} - {format % args}", file=sys.stderr)

    return Handler


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="启动本地剧情采集审核网页")
    parser.add_argument("--data-dir", default=str(PROJECT_ROOT / "data"), help="本地 data 根目录，默认仓库 data")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认只监听本机")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("port 必须在 1-65535 范围内")
    service = StoryImportService(Path(args.data_dir))
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service))
    print(f"剧情采集工具已启动：http://{args.host}:{args.port}/")
    print(f"数据目录：{service.data_dir}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止剧情采集工具")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
