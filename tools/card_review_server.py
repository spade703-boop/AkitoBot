"""Local-only review UI for generated PJSK limited-card hairstyles."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
from typing import Any
from urllib.parse import urlparse

try:
    from tools import build_card_catalog
except ModuleNotFoundError:
    import build_card_catalog  # type: ignore[no-redef]


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_CATALOG = Path("data/content/pjsk_cards.json")
DEFAULT_OVERRIDES = Path("data/content/pjsk_card_reviews.json")
DEFAULT_QUEUE = Path("data/card_catalog_review.json")
WEB_ROOT = Path(__file__).with_name("card_review_web")


class ReviewError(ValueError):
    pass


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _clean_text(value: object, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _clean_features(value: object) -> list[str]:
    if not isinstance(value, list):
        raise ReviewError("features 必须是字符串数组")
    return list(
        dict.fromkeys(
            feature
            for item in value[:12]
            if (feature := _clean_text(item, 60))
        )
    )


class ReviewStore:
    def __init__(self, catalog_path: Path, overrides_path: Path, queue_path: Path):
        self.catalog_path = catalog_path
        self.overrides_path = overrides_path
        self.queue_path = queue_path
        self._lock = threading.RLock()

    def _load_catalog(self) -> dict:
        payload = _read_json(self.catalog_path, {})
        if not isinstance(payload, dict) or not isinstance(payload.get("cards"), list):
            raise ReviewError(f"卡面库格式无效：{self.catalog_path}")
        return payload

    def _load_overrides(self) -> dict:
        payload = _read_json(self.overrides_path, {"version": 1, "cards": {}})
        if not isinstance(payload, dict):
            payload = {"version": 1, "cards": {}}
        if not isinstance(payload.get("cards"), dict):
            payload["cards"] = {}
        payload["version"] = 1
        return payload

    @staticmethod
    def _image_url(card: dict, variant: str) -> str:
        return build_card_catalog.DEFAULT_ASSET_URL.format(
            assetbundle=str(card.get("assetbundle_name") or ""),
            variant=variant,
        )

    def _card_view(self, card: dict) -> dict:
        hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
        normal_art = card.get("normal_art") if isinstance(card.get("normal_art"), dict) else {}
        trained_art = card.get("trained_art") if isinstance(card.get("trained_art"), dict) else {}
        return {
            "id": int(card["id"]),
            "sequence_alias": str(card.get("sequence_alias") or ""),
            "character_name": str(card.get("character_name") or ""),
            "title": str(card.get("title") or ""),
            "event_name": str(card.get("event_name") or ""),
            "commissioned_song": str(card.get("commissioned_song") or ""),
            "supply_label": str(card.get("supply_label") or ""),
            "release_at": int(card.get("release_at") or 0),
            "review_status": "reviewed" if hairstyle.get("status") == "reviewed" else "pending",
            "normal_image": self._image_url(card, "card_normal"),
            "trained_image": self._image_url(card, "card_after_training"),
            "normal_summary": str(normal_art.get("summary") or ""),
            "trained_summary": str(trained_art.get("summary") or ""),
            "hairstyle": hairstyle,
        }

    def snapshot(self) -> dict:
        with self._lock:
            payload = self._load_catalog()
            cards = [
                self._card_view(card)
                for card in payload["cards"]
                if isinstance(card, dict)
                and isinstance(card.get("hairstyle"), dict)
                and card["hairstyle"].get("available", False)
            ]
            cards.sort(key=lambda card: (card["release_at"], card["id"]))
            reviewed = sum(card["review_status"] == "reviewed" for card in cards)
            return {
                "cards": cards,
                "stats": {
                    "total": len(cards),
                    "reviewed": reviewed,
                    "pending": len(cards) - reviewed,
                },
            }

    def save_review(self, raw_card_id: object, raw_review: object) -> dict:
        try:
            card_id = int(raw_card_id)
        except (TypeError, ValueError) as exc:
            raise ReviewError("card_id 无效") from exc
        if not isinstance(raw_review, dict):
            raise ReviewError("review 必须是对象")
        description = _clean_text(raw_review.get("description"), 500)
        if not description:
            raise ReviewError("发型描述不能为空")
        features = _clean_features(raw_review.get("features", []))
        review_note = _clean_text(raw_review.get("review_note"), 500) or "本地审核页人工确认。"

        with self._lock:
            catalog_payload = self._load_catalog()
            cards = catalog_payload["cards"]
            card = next(
                (
                    item
                    for item in cards
                    if isinstance(item, dict) and int(item.get("id") or 0) == card_id
                ),
                None,
            )
            if card is None:
                raise ReviewError(f"找不到卡片 ID {card_id}")
            hairstyle = card.get("hairstyle") if isinstance(card.get("hairstyle"), dict) else {}
            if not hairstyle.get("available", False):
                raise ReviewError("该卡不是可审核发型的限定卡")

            overrides_payload = self._load_overrides()
            existing = overrides_payload["cards"].get(str(card_id), {})
            existing = existing if isinstance(existing, dict) else {}
            overrides_payload["cards"][str(card_id)] = {
                **existing,
                "hairstyle": {
                    "description": description,
                    "features": features,
                    "visible_in": "trained",
                    "owner_location": hairstyle.get("owner_location", ""),
                    "structure": hairstyle.get("structure", {}),
                    "hair_accessories": hairstyle.get("hair_accessories", []),
                    "headwear": hairstyle.get("headwear", []),
                    "observed_color": hairstyle.get("observed_color", ""),
                    "lighting_effect": hairstyle.get("lighting_effect", ""),
                    "review_note": review_note,
                },
            }
            build_card_catalog._atomic_write_json(self.overrides_path, overrides_payload)
            build_card_catalog.apply_review_overrides(cards, overrides_payload)
            build_card_catalog._atomic_write_json(self.catalog_path, catalog_payload)

            queue_items = [
                {
                    "id": item["id"],
                    "sequence_alias": item["sequence_alias"],
                    "reason": ",".join(build_card_catalog._card_review_reasons(item)),
                }
                for item in cards
                if build_card_catalog._card_review_reasons(item)
            ]
            build_card_catalog._atomic_write_json(
                self.queue_path,
                {"version": 1, "items": queue_items},
            )
            refreshed_card = next(item for item in cards if int(item.get("id") or 0) == card_id)
            snapshot = self.snapshot()
            return {"card": self._card_view(refreshed_card), "stats": snapshot["stats"]}


class ReviewRequestHandler(BaseHTTPRequestHandler):
    server_version = "PJSKCardReview/1.0"

    @property
    def store(self) -> ReviewStore:
        return self.server.store  # type: ignore[attr-defined,no-any-return]

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, message: str, status: HTTPStatus) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/state":
            try:
                self._send_json({"ok": True, **self.store.snapshot()})
            except (OSError, ValueError, ReviewError) as exc:
                self._send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        if path in {"/", "/index.html"}:
            try:
                body = (WEB_ROOT / "index.html").read_bytes()
            except OSError as exc:
                self._send_error_json(str(exc), HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self._send_error_json("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/review":
            self._send_error_json("Not found", HTTPStatus.NOT_FOUND)
            return
        origin = self.headers.get("Origin", "")
        if origin and urlparse(origin).hostname not in {"127.0.0.1", "localhost"}:
            self._send_error_json("Origin not allowed", HTTPStatus.FORBIDDEN)
            return
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error_json("Content-Length 无效", HTTPStatus.BAD_REQUEST)
            return
        if content_length <= 0 or content_length > 64_000:
            self._send_error_json("请求体大小无效", HTTPStatus.BAD_REQUEST)
            return
        try:
            payload = json.loads(self.rfile.read(content_length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ReviewError("请求体必须是 JSON 对象")
            result = self.store.save_review(payload.get("card_id"), payload.get("review"))
        except (UnicodeDecodeError, json.JSONDecodeError, ReviewError, OSError, ValueError) as exc:
            self._send_error_json(str(exc), HTTPStatus.BAD_REQUEST)
            return
        self._send_json({"ok": True, **result})

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {format_string % args}")


class ReviewHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], store: ReviewStore):
        super().__init__(server_address, ReviewRequestHandler)
        self.store = store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动本地 PJSK 卡面发型审核页")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--overrides", type=Path, default=DEFAULT_OVERRIDES)
    parser.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    return parser.parse_args()


def main() -> None:
    if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8" and hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    store = ReviewStore(args.catalog, args.overrides, args.queue)
    snapshot = store.snapshot()
    server = ReviewHTTPServer((args.host, args.port), store)
    print(f"卡面审核页：http://{args.host}:{args.port}")
    print(f"待审核 {snapshot['stats']['pending']} 张，已审核 {snapshot['stats']['reviewed']} 张")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
