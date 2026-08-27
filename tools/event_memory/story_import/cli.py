"""Capture, review and publish pjsk.moe story drafts.

Examples:
    python tools/event_memory/story_import/cli.py capture --url https://pjsk.moe/zh-cn/story/event/140/8/
    python tools/event_memory/story_import/cli.py review story-0123456789abcdef --status approved
    python tools/event_memory/story_import/cli.py publish story-0123456789abcdef
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

try:
    from .runtime import enrich_with_llm, load_story_import_module
except ImportError:  # pragma: no cover - direct ``python tools/...`` execution
    from runtime import enrich_with_llm, load_story_import_module

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

_CORE_MODULE = load_story_import_module()
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

StoryImportError = _CORE_MODULE.StoryImportError
capture_story = _CORE_MODULE.capture_story
load_draft = _CORE_MODULE.load_draft
merge_event_memory = _CORE_MODULE.merge_event_memory
preview_event_memory = _CORE_MODULE.preview_event_memory
save_draft = _CORE_MODULE.save_draft
update_review = _CORE_MODULE.update_review


def _data_dir(value: str | None) -> Path:
    return Path(value).resolve() if value else PROJECT_ROOT / "data"


def _capture(args: argparse.Namespace) -> int:
    payload = capture_story(
        args.url,
        data_dir=_data_dir(args.data_dir),
        enrich=args.enrich == "llm",
    )
    if args.enrich == "llm":
        payload = enrich_with_llm(payload)
    path = save_draft(payload, data_dir=_data_dir(args.data_dir))
    print(f"draft_id={payload['draft_id']}")
    print(f"path={path}")
    print(f"route={payload['source']['route_type']} locale={payload['source']['locale']}")
    print(f"actions={len(payload['actions'])} target_segments={len(payload['target_segments'])}")
    return 0


def _list(args: argparse.Namespace) -> int:
    directory = _data_dir(args.data_dir) / "event_memory" / "story_import" / "drafts"
    if not directory.exists():
        print("没有本地剧情草稿")
        return 0
    rows: list[tuple[str, str, str]] = []
    for path in sorted(directory.glob("story-*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        status = str(payload.get("review", {}).get("status") or payload.get("status") or "unknown")
        if args.status and status != args.status:
            continue
        title = str(payload.get("story", {}).get("episode_title") or payload.get("page", {}).get("title") or "")
        rows.append((str(payload.get("draft_id") or path.stem), status, title))
    if not rows:
        print("没有符合条件的剧情草稿")
        return 0
    for draft_id, status, title in rows:
        print(f"{draft_id}\t{status}\t{title}")
    return 0


def _show(args: argparse.Namespace) -> int:
    payload, _ = load_draft(args.draft_id, data_dir=_data_dir(args.data_dir))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _review(args: argparse.Namespace) -> int:
    payload, _ = load_draft(args.draft_id, data_dir=_data_dir(args.data_dir))
    updated = update_review(payload, args.status, reviewer=args.reviewer, note=args.note or "")
    path = save_draft(updated, data_dir=_data_dir(args.data_dir))
    print(f"review_status={updated['review']['status']}")
    print(f"path={path}")
    return 0


def _publish(args: argparse.Namespace) -> int:
    payload, _ = load_draft(args.draft_id, data_dir=_data_dir(args.data_dir))
    preview = preview_event_memory(payload, data_dir=_data_dir(args.data_dir))
    print(f"dedupe_status={preview['status']}")
    path, event_id = merge_event_memory(
        payload,
        data_dir=_data_dir(args.data_dir),
        confirm_revision=args.confirm_revision,
    )
    payload["publish"] = {"event_memory_id": event_id, "published_at": _now_iso()}
    save_draft(payload, data_dir=_data_dir(args.data_dir))
    print(f"event_id={event_id}")
    print(f"path={path}")
    return 0


def _dedupe(args: argparse.Namespace) -> int:
    payload, _ = load_draft(args.draft_id, data_dir=_data_dir(args.data_dir))
    print(json.dumps(preview_event_memory(payload, data_dir=_data_dir(args.data_dir)), ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="抓取并审核 pjsk.moe 彰人/冬弥剧情")
    parser.add_argument("--data-dir", default=None, help="本地 data 根目录，默认 data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_subcommand_data_dir(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument(
            "--data-dir",
            default=argparse.SUPPRESS,
            help="本地 data 根目录（也可放在子命令前）",
        )

    capture = subparsers.add_parser("capture", help="抓取页面并生成草稿")
    add_subcommand_data_dir(capture)
    capture.add_argument("--url", required=True)
    capture.add_argument("--enrich", choices=("none", "llm"), default="none")
    capture.set_defaults(handler=_capture)

    listing = subparsers.add_parser("list", help="列出本地草稿")
    add_subcommand_data_dir(listing)
    listing.add_argument("--status", choices=("draft", "approved", "rejected"), default=None)
    listing.set_defaults(handler=_list)

    show = subparsers.add_parser("show", help="显示草稿 JSON")
    add_subcommand_data_dir(show)
    show.add_argument("draft_id")
    show.set_defaults(handler=_show)

    review = subparsers.add_parser("review", help="审核草稿")
    add_subcommand_data_dir(review)
    review.add_argument("draft_id")
    review.add_argument("--status", choices=("approved", "rejected"), required=True)
    review.add_argument("--reviewer", default="local")
    review.add_argument("--note", default="")
    review.set_defaults(handler=_review)

    publish = subparsers.add_parser("publish", help="发布已审核草稿")
    add_subcommand_data_dir(publish)
    publish.add_argument("draft_id")
    publish.add_argument("--confirm-revision", action="store_true", help="确认覆盖同一剧情的旧版本")
    publish.set_defaults(handler=_publish)

    dedupe = subparsers.add_parser("dedupe", help="预览草稿的去重结果")
    add_subcommand_data_dir(dedupe)
    dedupe.add_argument("draft_id")
    dedupe.set_defaults(handler=_dedupe)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except StoryImportError as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
