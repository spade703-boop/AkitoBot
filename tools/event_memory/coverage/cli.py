"""Command-line maintenance for the known-source coverage catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from .core import default_store
except ImportError:  # pragma: no cover
    from core import default_store

ROOT = Path(__file__).resolve().parents[3]


def main() -> int:
    parser = argparse.ArgumentParser(description="维护彰冬剧情已知来源覆盖台账")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("sync", help="从本地草稿和已发布事件同步状态")
    subparsers.add_parser("summary", help="输出已知来源覆盖汇总")
    add = subparsers.add_parser("add", help="加入一个待抓取剧情 URL")
    add.add_argument("url")
    add.add_argument("--priority", choices=("high", "medium", "low"), default="medium")
    add.add_argument("--notes", default="")
    args = parser.parse_args()
    store = default_store(ROOT)
    if args.command == "sync":
        store.sync()
        summary = store.summary()
        print(json.dumps({**summary, "report": str(store.report_path)}, ensure_ascii=False))
    elif args.command == "summary":
        print(json.dumps(store.summary(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(store.add_source(args.url, priority=args.priority, notes=args.notes), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
