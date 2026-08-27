"""Import evidence-backed Project SEKAI story pages into local drafts.

The importer is intentionally independent from the bot runtime.  It resolves a
pjsk.moe URL to public metadata/translation/assets, preserves the Japanese and
localized text, and produces a reviewable draft without mutating bot data.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile
from typing import Any
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

ALLOWED_PAGE_HOSTS = frozenset({"pjsk.moe", "www.pjsk.moe"})
ALLOWED_ASSET_HOSTS = frozenset(
    {
        "metadata.exmeaning.com",
        "metadata.pjsk.moe",
        "translation.exmeaning.com",
        "storage.exmeaning.com",
        "storage.pjsk.moe",
    }
)
ALLOWED_FETCH_HOSTS = ALLOWED_PAGE_HOSTS | ALLOWED_ASSET_HOSTS
LOCALE_REGIONS = {
    "zh-cn": ("zh-CN", "cn"),
    "zh-tw": ("zh-TW", "tw"),
    "ja-jp": ("ja-JP", "jp"),
    "en-us": ("en-US", "en"),
    "ko-kr": ("ko-KR", "kr"),
}
ROUTE_PARAMETER_NAMES = {
    "event": ("story_id", "episode_no"),
    "unit": ("story_id", "episode_no"),
    "card": ("story_id", "episode_no"),
    "area": ("story_id", "episode_no"),
    "self": ("story_id",),
    "special": ("story_id", "episode_no"),
}
MASTER_FILES = {
    "event": ("eventStories.json", "events.json"),
    "unit": ("unitStories.json", "unitProfiles.json", "cards.json"),
    "card": ("cardEpisodes.json", "cards.json"),
    "area": ("actionSets.json", "areas.json", "events.json", "cards.json"),
    "self": ("characters.json", "unitProfiles.json"),
    "special": ("specialStories.json", "cards.json"),
}
ROUTE_ASSET_PREFIX = {
    "event": "event_story/{assetbundle_name}/scenario/{scenario_id}.json",
    "unit": "scenario/unitstory/{assetbundle_name}/{scenario_id}.json",
    "card": "character/member/{assetbundle_name}/{scenario_id}.json",
    "area": "scenario/actionset/group{group}/{scenario_id}.json",
    "self": "scenario/profile/{scenario_id}.json",
    "special": "scenario/special/{assetbundle_name}/{scenario_id}.json",
}
TARGET_ALIASES = {
    "akito": ("彰人", "东云彰人", "東雲彰人", "akito", "中学生的彰人", "中学生の彰人"),
    "toya": ("冬弥", "青柳冬弥", "青柳冬弥", "toya", "中学生的冬弥", "中学生の冬弥"),
}
SPEAKER_ALIASES = {alias: target for target, aliases in TARGET_ALIASES.items() for alias in aliases}


class StoryImportError(ValueError):
    """Raised when a URL or public story asset cannot be imported safely."""


class StoryAssetError(StoryImportError):
    """Raised when a required story asset is unavailable or invalid."""


@dataclass(frozen=True)
class StoryRoute:
    url: str
    canonical_url: str
    locale: str
    region: str
    route_type: str
    params: dict[str, str]


@dataclass(frozen=True)
class FetchedAsset:
    url: str
    status: int
    content_type: str
    body: str
    sha256: str
    cached: bool = False


Fetcher = Callable[[str], FetchedAsset]
MAX_ASSET_BYTES = 64 * 1024 * 1024


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _digest_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def story_content_digest(payload: dict[str, Any]) -> str:
    """Calculate a stable digest from the ordered bilingual action sequence."""
    actions = payload.get("actions", []) if isinstance(payload, dict) else []
    rows = []
    if isinstance(actions, list):
        for fallback_index, action in enumerate(actions):
            if not isinstance(action, dict):
                continue
            rows.append(
                {
                    "index": action.get("index", fallback_index),
                    "speaker_id": _digest_text(action.get("speaker_id")),
                    "speaker_ids": action.get("speaker_ids", []),
                    "speaker_ja": _digest_text(action.get("speaker_ja")),
                    "text_ja": _digest_text(action.get("text_ja")),
                    "text_zh": _digest_text(action.get("text_zh")),
                    "kind": _digest_text(action.get("kind")),
                }
            )
    return _sha256_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def story_evidence_digest(payload: dict[str, Any]) -> str:
    """Calculate a stable digest from the target evidence windows."""
    segments = payload.get("target_segments", []) if isinstance(payload, dict) else []
    rows = []
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            rows.append(
                {
                    "evidence_refs": segment.get("evidence_refs", []),
                    "text_ja": _digest_text(segment.get("text_ja")),
                    "text_zh": _digest_text(segment.get("text_zh")),
                }
            )
    return _sha256_text(json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _canonicalize_url(url: str) -> tuple[str, str, list[str]]:
    parsed = urlparse(str(url or "").strip())
    if parsed.scheme.lower() != "https":
        raise StoryImportError("只接受 HTTPS 页面 URL")
    if parsed.hostname not in ALLOWED_PAGE_HOSTS:
        raise StoryImportError("URL 必须来自 pjsk.moe")
    segments = [segment for segment in parsed.path.split("/") if segment]
    canonical = urlunparse(("https", parsed.hostname.lower(), "/" + "/".join(segments) + "/", "", "", ""))
    return canonical, parsed.hostname.lower(), segments


def parse_story_url(url: str) -> StoryRoute:
    """Validate and normalize a pjsk.moe story URL."""
    canonical, _, segments = _canonicalize_url(url)
    if segments and segments[0].lower() in LOCALE_REGIONS:
        locale_key = segments[0].lower()
        route_offset = 1
    else:
        locale_key = "zh-cn"
        route_offset = 0
    if len(segments) <= route_offset or segments[route_offset].lower() != "story":
        raise StoryImportError("URL 不是资讯站剧情页面")
    route_offset += 1
    if len(segments) <= route_offset:
        raise StoryImportError("剧情 URL 缺少路由类型和编号")
    route_type = segments[route_offset].lower()
    names = ROUTE_PARAMETER_NAMES.get(route_type)
    if names is None:
        raise StoryImportError(f"暂不支持剧情路由：{route_type}")
    values = segments[route_offset + 1 :]
    if route_type == "card":
        if len(values) not in {1, 2}:
            raise StoryImportError("card 路由需要 1 或 2 段编号")
    elif len(values) < len(names):
        raise StoryImportError(f"{route_type} 路由缺少参数：需要 {len(names)} 段编号")
    elif len(values) > len(names):
        raise StoryImportError(f"{route_type} 路由参数过多：需要 {len(names)} 段编号")
    params = {name: values[index] for index, name in enumerate(names[: len(values)])}
    locale, region = LOCALE_REGIONS[locale_key]
    return StoryRoute(
        url=str(url).strip(),
        canonical_url=canonical,
        locale=locale,
        region=region,
        route_type=route_type,
        params=params,
    )


def _assert_asset_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_FETCH_HOSTS:
        raise StoryImportError("解析出的资产地址不在允许的资讯站资源域名内")


def _request_asset(url: str, *, timeout: float = 15.0, retries: int = 2) -> FetchedAsset:
    _assert_asset_url(url)
    last_error: Exception | None = None
    for _attempt in range(retries + 1):
        try:
            request = Request(url, headers={"User-Agent": "akito-story-import/1.0"})
            with urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_ASSET_BYTES + 1)
                if len(raw) > MAX_ASSET_BYTES:
                    raise StoryAssetError("远程资产超过 64 MiB 限制")
                body = raw.decode("utf-8-sig")
                return FetchedAsset(
                    url=url,
                    status=int(getattr(response, "status", 200)),
                    content_type=response.headers.get("Content-Type", ""),
                    body=body,
                    sha256=_sha256_text(body),
                )
        except (HTTPError, URLError, TimeoutError, UnicodeDecodeError, StoryAssetError) as error:
            last_error = error
            if isinstance(error, HTTPError) and error.code in {400, 401, 403, 404}:
                break
    raise StoryAssetError(f"无法读取公开资产：{url} ({last_error})")


def _cache_file(cache_dir: Path, url: str) -> Path:
    return cache_dir / f"{_sha256_text(url)[:32]}.json"


def cached_fetch(url: str, *, cache_dir: Path | None = None, fetcher: Fetcher | None = None) -> FetchedAsset:
    """Fetch a text asset with an optional JSON cache."""
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = _cache_file(cache_dir, url)
        if path.exists():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                return FetchedAsset(
                    url=str(payload["url"]),
                    status=int(payload.get("status", 200)),
                    content_type=str(payload.get("content_type", "")),
                    body=str(payload["body"]),
                    sha256=str(payload["sha256"]),
                    cached=True,
                )
            except (OSError, KeyError, TypeError, ValueError):
                path.unlink(missing_ok=True)
    asset = (fetcher or _request_asset)(url)
    if cache_dir is not None:
        path = _cache_file(cache_dir, url)
        payload = json.dumps(asset.__dict__, ensure_ascii=False, indent=2)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=cache_dir, delete=False) as handle:
            handle.write(payload)
            temp_name = handle.name
        Path(temp_name).replace(path)
    return asset


def _parse_json(asset: FetchedAsset) -> Any:
    try:
        return json.loads(asset.body)
    except json.JSONDecodeError as error:
        raise StoryAssetError(f"资产不是有效 JSON：{asset.url}") from error


def _page_metadata(body: str) -> dict[str, str]:
    def match(pattern: str) -> str:
        found = re.search(pattern, body, flags=re.IGNORECASE | re.DOTALL)
        return html.unescape(re.sub(r"\s+", " ", found.group(1)).strip()) if found else ""

    return {
        "title": match(r"<title[^>]*>(.*?)</title>"),
        "description": match(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']'),
        "canonical_url": match(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\'](.*?)["\']'),
    }


def _metadata_url(region: str, filename: str) -> str:
    return f"https://metadata.exmeaning.com/{region}/master/{filename}"


def _asset_url(region: str, route_type: str, descriptor: dict[str, Any]) -> str | None:
    scenario_id = str(descriptor.get("scenarioId") or descriptor.get("scenario_id") or "").strip()
    bundle = str(descriptor.get("assetbundleName") or descriptor.get("assetbundle_name") or "").strip()
    if not scenario_id:
        return None
    template = ROUTE_ASSET_PREFIX[route_type]
    try:
        path = template.format(
            assetbundle_name=bundle,
            scenario_id=scenario_id,
            group=descriptor.get("group") or descriptor.get("actionSetGroup") or "",
        )
    except KeyError:
        return None
    if "{" in path or "}" in path:
        return None
    return f"https://storage.exmeaning.com/sekai-{region}-assets/{path}"


def _asset_url_candidates(region: str, route_type: str, descriptor: dict[str, Any]) -> list[str]:
    """Return the declared asset URL followed by a parent-bundle fallback."""
    urls: list[str] = []
    primary = _asset_url(region, route_type, descriptor)
    if primary:
        urls.append(primary)
    parent_bundle = str(descriptor.get("_parent_assetbundle_name") or "").strip()
    current_bundle = str(descriptor.get("assetbundleName") or "").strip()
    if parent_bundle and parent_bundle != current_bundle:
        parent_descriptor = dict(descriptor)
        parent_descriptor["assetbundleName"] = parent_bundle
        fallback = _asset_url(region, route_type, parent_descriptor)
        if fallback and fallback not in urls:
            urls.append(fallback)
    return urls


def _walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _walk_dicts_with_context(value: Any, inherited_bundle: str = "") -> Iterable[dict[str, Any]]:
    """Walk metadata while retaining a parent resource bundle for episodes."""
    if isinstance(value, dict):
        own_bundle = str(value.get("assetbundleName") or value.get("assetbundle_name") or "").strip()
        current_bundle = own_bundle or inherited_bundle
        item = dict(value)
        if inherited_bundle and own_bundle and own_bundle != inherited_bundle and item.get("scenarioId"):
            item["_parent_assetbundle_name"] = inherited_bundle
        elif inherited_bundle and not own_bundle and item.get("scenarioId"):
            item["assetbundleName"] = inherited_bundle
        yield item
        for child in value.values():
            yield from _walk_dicts_with_context(child, current_bundle)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts_with_context(child, inherited_bundle)


def _matches_descriptor(route: StoryRoute, value: dict[str, Any]) -> bool:
    story_id = route.params["story_id"]
    episode_no = route.params.get("episode_no")
    if route.route_type == "card":
        id_values = set()
        if value.get("cardId") is not None:
            id_values.add(str(value["cardId"]))
        if (
            value.get("id") is not None
            and not value.get("cardEpisodePartType")
            and (value.get("assetbundleName") or value.get("cardRarityType"))
        ):
            id_values.add(str(value["id"]))
    else:
        id_values = {
            str(value.get(key))
            for key in (
                "eventId",
                "eventStoryId",
                "unitStoryId",
                "unitId",
                "cardId",
                "actionSetId",
                "specialStoryId",
                "characterId",
                "profileId",
                "id",
            )
            if value.get(key) is not None
        }
    if story_id not in id_values:
        return False
    if episode_no is None:
        return True
    episode_values = {
        str(value.get(key)) for key in ("episodeNo", "episode_no", "chapterNo") if value.get(key) is not None
    }
    card_episode_type = str(value.get("cardEpisodePartType") or "")
    card_episode_number = _episode_number_from_token(card_episode_type)
    if card_episode_number is not None:
        episode_values.add(str(card_episode_number))
        episode_values.add(card_episode_type)
    return not episode_values or episode_no in episode_values


def _find_descriptor(route: StoryRoute, masters: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for payload in masters.values():
        candidates.extend(item for item in _walk_dicts_with_context(payload) if _matches_descriptor(route, item))
    if not candidates:
        if route.route_type == "card":
            raise StoryAssetError(
                f"主数据中找不到 {route.locale} 区域的卡面 ID {route.params['story_id']}；"
                "该卡可能尚未在此地区上线，请改用已上线地区对应的页面 URL"
            )
        raise StoryAssetError("主数据中找不到该剧情的场景描述")
    descriptor = max(
        candidates, key=lambda item: int(bool(item.get("scenarioId"))) + int(bool(item.get("assetbundleName")))
    )
    if not descriptor.get("scenarioId"):
        nested = [item for item in _walk_dicts(descriptor) if item.get("scenarioId")]
        if nested:
            descriptor = nested[0]
    if route.route_type == "card" and not descriptor.get("assetbundleName"):
        card_id = descriptor.get("cardId")
        if card_id is not None:
            for payload in masters.values():
                for item in _walk_dicts(payload):
                    if not item.get("assetbundleName"):
                        continue
                    if str(item.get("id")) != str(card_id) and str(item.get("cardId")) != str(card_id):
                        continue
                    merged = dict(item)
                    merged.update(descriptor)
                    descriptor = merged
                    break
                else:
                    continue
                break
    return descriptor


def _episode_number_from_token(value: object) -> int | None:
    token = str(value or "").strip().lower().replace("-", "_")
    if token.isdigit():
        return int(token)
    match = re.fullmatch(r"episode_?(\d+)", token)
    if match:
        return int(match.group(1))
    if token in {"first_part", "firstpart"}:
        return 1
    if token in {"second_part", "secondpart"}:
        return 2
    return None


def _card_episode_number(descriptor: dict[str, Any]) -> int | None:
    """Extract a card episode number for stable ordering across master variants."""
    for key in ("episodeNo", "episode_no", "chapterNo"):
        value = descriptor.get(key)
        number = _episode_number_from_token(value)
        if number is not None:
            return number
    number = _episode_number_from_token(descriptor.get("cardEpisodePartType"))
    if number is not None:
        return number
    return None


def _find_card_descriptors(route: StoryRoute, masters: dict[str, Any]) -> list[dict[str, Any]]:
    """Resolve a card ID from JP masters, returning its requested episode parts."""
    card_id = route.params["story_id"]
    card_rows = [
        item
        for payload in masters.values()
        for item in _walk_dicts(payload)
        if str(item.get("id")) == card_id and item.get("assetbundleName")
    ]
    bundle = str(card_rows[0].get("assetbundleName") or "") if card_rows else ""
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for payload in masters.values():
        for item in _walk_dicts(payload):
            if str(item.get("cardId")) != card_id or not item.get("scenarioId"):
                continue
            episode_number = _card_episode_number(item)
            requested_episode = route.params.get("episode_no")
            requested_number = _episode_number_from_token(requested_episode)
            if (
                requested_episode is not None
                and episode_number is not None
                and (requested_number is None or episode_number != requested_number)
            ):
                continue
            if requested_episode is not None and episode_number is None:
                episode_values = {
                    str(item.get(key))
                    for key in ("episodeNo", "episode_no", "chapterNo", "cardEpisodePartType")
                    if item.get(key) is not None
                }
                if episode_values and requested_episode not in episode_values:
                    continue
            descriptor = dict(item)
            if bundle and not descriptor.get("assetbundleName"):
                descriptor["assetbundleName"] = bundle
            key = (str(descriptor.get("scenarioId")), str(descriptor.get("assetbundleName") or ""))
            if key in seen:
                continue
            seen.add(key)
            candidates.append(descriptor)
    if not candidates:
        raise StoryAssetError(f"日服主数据中找不到卡面 ID {card_id}；请确认卡 ID 或日服资源是否已更新")
    candidates.sort(
        key=lambda item: (
            _card_episode_number(item) is None,
            _card_episode_number(item) or 0,
            str(item.get("scenarioId")),
        )
    )
    return candidates


def _target_for_speaker(speaker: str) -> str | None:
    targets = _target_ids_for_speaker(speaker)
    return targets[0] if targets else None


def _target_ids_for_speaker(speaker: str) -> list[str]:
    compact = str(speaker or "").replace(" ", "")
    return list(dict.fromkeys(target for alias, target in SPEAKER_ALIASES.items() if alias in compact))


def _line_kind(text: str) -> str:
    return "thought" if str(text).strip().startswith(("（", "(", "【")) else "dialogue"


def _looks_like_speaker_label(text: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9\u3040-\u30ff\u3400-\u9fff·・& ]{1,24}", text.strip()))


def _translation_path(route: StoryRoute) -> str | None:
    story_id = route.params["story_id"]
    names = {
        "event": f"eventStory/event_{story_id}.json",
        "unit": f"unitStory/unit_{story_id}.json",
        "card": f"cardStory/card_{story_id}.json",
        "area": f"areaTalk/area_{story_id}.json",
        "special": f"specialStory/special_{story_id}.json",
    }
    path = names.get(route.route_type)
    if not path:
        return None
    base = (
        "https://translation.exmeaning.com/files/translation"
        if route.locale == "zh-CN"
        else f"https://translation.exmeaning.com/files/v2/{route.locale}/translation"
    )
    return f"{base}/{path}"


def _talk_data(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict):
        return {}
    value = payload.get("talkData") or payload.get("TalkData")
    if isinstance(value, dict):
        return {str(key): str(item) for key, item in value.items() if isinstance(item, (str, int, float))}
    if isinstance(value, list):
        output: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            key = item.get("textJa") or item.get("text") or item.get("Text") or item.get("key")
            translated = (
                item.get("textZh") or item.get("translatedText") or item.get("translation") or item.get("value")
            )
            if key is not None and translated is not None:
                output[str(key)] = str(translated)
        return output
    return {}


def _select_translation_episode(payload: Any, route: StoryRoute) -> Any:
    if not isinstance(payload, dict):
        return payload
    episodes = payload.get("episodes")
    episode_no = route.params.get("episode_no")
    if isinstance(episodes, dict) and episode_no is not None:
        return episodes.get(str(episode_no), payload)
    return payload


def _scenario_actions(payload: Any) -> list[dict[str, str]]:
    actions: list[dict[str, str]] = []
    for item in _walk_dicts(payload):
        text = (
            item.get("Text")
            or item.get("text")
            or item.get("Body")
            or item.get("body")
            or item.get("dialogue")
            or item.get("Dialogue")
        )
        if not isinstance(text, str) or not text.strip():
            continue
        speaker = (
            item.get("WindowDisplayName") or item.get("speaker") or item.get("SpeakerName") or item.get("name") or ""
        )
        actions.append({"speaker": str(speaker), "text_ja": text.strip(), "text_zh": ""})
    return actions


def _normalize_actions(scenario: Any, translation: Any) -> list[dict[str, Any]]:
    translated = _talk_data(translation)
    actions = _scenario_actions(scenario)
    if not actions:
        current_speaker = ""
        for original, localized in _talk_data(translation).items():
            target = _target_for_speaker(original)
            if target or _looks_like_speaker_label(original):
                if target or len(original) <= 12:
                    current_speaker = original
                continue
            actions.append({"speaker": current_speaker, "text_ja": original, "text_zh": localized})
    normalized: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        text_ja = str(action.get("text_ja") or "").strip()
        if not text_ja:
            continue
        text_zh = str(action.get("text_zh") or translated.get(text_ja) or "").strip()
        speaker = str(action.get("speaker") or "").strip()
        normalized.append(
            {
                "index": index,
                "speaker_id": _target_for_speaker(speaker) or "",
                "speaker_ids": _target_ids_for_speaker(speaker),
                "speaker_ja": speaker,
                "speaker_zh": speaker,
                "text_ja": text_ja,
                "text_zh": text_zh,
                "kind": _line_kind(text_zh or text_ja),
            }
        )
    return normalized


def _normalize_paired_actions(scenario_ja: Any, scenario_zh: Any) -> list[dict[str, Any]]:
    """Pair Japanese and Chinese scenario actions by their native action order."""
    original_actions = _scenario_actions(scenario_ja)
    localized_actions = _scenario_actions(scenario_zh)
    if not original_actions:
        return _normalize_actions(scenario_ja, scenario_zh)
    normalized: list[dict[str, Any]] = []
    for index, original in enumerate(original_actions):
        localized = localized_actions[index] if index < len(localized_actions) else {}
        text_ja = str(original.get("text_ja") or "").strip()
        text_zh = str(localized.get("text_ja") or "").strip()
        speaker_ja = str(original.get("speaker") or "").strip()
        speaker_zh = str(localized.get("speaker") or "").strip()
        normalized.append(
            {
                "index": index,
                "speaker_id": _target_for_speaker(speaker_ja) or _target_for_speaker(speaker_zh) or "",
                "speaker_ids": list(
                    dict.fromkeys(_target_ids_for_speaker(speaker_ja) + _target_ids_for_speaker(speaker_zh))
                ),
                "speaker_ja": speaker_ja,
                "speaker_zh": speaker_zh or speaker_ja,
                "text_ja": text_ja,
                "text_zh": text_zh,
                "kind": _line_kind(text_zh or text_ja),
            }
        )
    return normalized


def _action_target_ids(action: dict[str, Any]) -> set[str]:
    values = action.get("speaker_ids")
    if isinstance(values, list):
        return {str(value) for value in values if str(value) in {"akito", "toya"}}
    speaker_id = str(action.get("speaker_id") or "")
    return {speaker_id} if speaker_id in {"akito", "toya"} else set()


def _target_segments(
    actions: list[dict[str, Any]],
    window: int = 2,
    pair_distance: int = 4,
    *,
    include_context: bool = True,
) -> list[dict[str, Any]]:
    """Extract exchanges that contain both Akito and Toya.

    ``include_context`` is retained for callers that need a wider review
    window.  Published/LLM evidence uses the target-only mode so unrelated
    speakers are not mistaken for part of the Akito/Toya memory.
    """
    anchors: list[list[int]] = []
    target_ids = [_action_target_ids(action) for action in actions]
    for index, current_ids in enumerate(target_ids):
        if not current_ids:
            continue
        if {"akito", "toya"}.issubset(current_ids):
            anchors.append([index, index])
            continue
        for other_index in range(index + 1, min(len(actions), index + pair_distance + 1)):
            other_ids = target_ids[other_index]
            if other_ids and {"akito", "toya"}.issubset(current_ids | other_ids):
                anchors.append([index, other_index])
    if not anchors:
        return []
    anchors.sort()
    ranges: list[list[int]] = []
    for start, end in anchors:
        if ranges and start <= ranges[-1][1] + (window * 2):
            ranges[-1][1] = max(ranges[-1][1], end)
        else:
            ranges.append([start, end])
    ranges = [[max(0, start - window), min(len(actions) - 1, end + window)] for start, end in ranges]
    segments: list[dict[str, Any]] = []
    for ordinal, (start, end) in enumerate(ranges, 1):
        rows = actions[start : end + 1]
        if not include_context:
            rows = [row for row in rows if _action_target_ids(row)]
        if not rows:
            continue
        target_speakers: list[str] = []
        for row in rows:
            row_targets = _action_target_ids(row)
            for target in ("akito", "toya"):
                if target in row_targets and target not in target_speakers:
                    target_speakers.append(target)
        segments.append(
            {
                "segment_id": f"segment-{ordinal:03d}",
                "start_index": rows[0]["index"],
                "end_index": rows[-1]["index"],
                "target_speakers": target_speakers,
                "evidence_refs": [row["index"] for row in rows],
                "text_ja": "\n".join(f"{row['speaker_ja']}: {row['text_ja']}" for row in rows),
                "text_zh": "\n".join(f"{row['speaker_zh']}: {row['text_zh']}" for row in rows),
            }
        )
    return segments


def _asset_record(asset: FetchedAsset, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "url": asset.url,
        "status": asset.status,
        "content_type": asset.content_type,
        "sha256": asset.sha256,
        "cached": asset.cached,
    }


def _fetch_masters(
    route: StoryRoute, *, cache_dir: Path, fetcher: Fetcher | None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    masters: dict[str, Any] = {}
    source_assets: list[dict[str, Any]] = []
    for filename in MASTER_FILES[route.route_type]:
        asset_url = _metadata_url(route.region, filename)
        try:
            asset = cached_fetch(asset_url, cache_dir=cache_dir, fetcher=fetcher)
        except StoryImportError:
            continue
        masters[filename] = _parse_json(asset)
        source_assets.append(_asset_record(asset, "master"))
    return masters, source_assets


def capture_story(
    url: str,
    *,
    data_dir: Path | None = None,
    fetcher: Fetcher | None = None,
    enrich: bool = False,
) -> dict[str, Any]:
    """Capture one story page into a schema-valid local draft payload."""
    route = parse_story_url(url)
    root = Path(data_dir or "data")
    cache_dir = root / "event_memory" / "story_import" / "cache"
    page = cached_fetch(route.canonical_url, cache_dir=cache_dir, fetcher=fetcher)
    page_meta = _page_metadata(page.body)
    source_assets = [_asset_record(page, "page")]
    # Card IDs are shared by JP and CN, while the locale in a pjsk.moe URL is
    # only the site's display language.  Resolve cards from JP masters first so
    # an untranslated CN card-episode ID can never collide with a card ID.
    data_route = replace(route, region="jp") if route.route_type == "card" else route
    story_assetbundle_name = ""
    masters, master_assets = _fetch_masters(data_route, cache_dir=cache_dir, fetcher=fetcher)
    source_assets.extend(master_assets)
    if route.route_type == "card":
        descriptors = _find_card_descriptors(data_route, masters)
        actions = []
        segments: list[dict[str, Any]] = []
        card_parts: list[dict[str, Any]] = []
        for descriptor in descriptors:
            scenario_id = str(descriptor.get("scenarioId") or "").strip()
            scenario_ja_url = _asset_url("jp", "card", descriptor)
            if not scenario_ja_url:
                raise StoryAssetError(f"日服卡面 {scenario_id} 缺少可读取的资源地址")
            try:
                original_asset = cached_fetch(scenario_ja_url, cache_dir=cache_dir, fetcher=fetcher)
                original_payload = _parse_json(original_asset)
            except StoryImportError as error:
                raise StoryAssetError(f"无法读取日服卡面剧情资源：{scenario_id}") from error
            source_assets.append(_asset_record(original_asset, "scenario"))

            localized_payload: Any = {}
            scenario_zh_url = _asset_url("cn", "card", descriptor)
            if scenario_zh_url:
                try:
                    localized_asset = cached_fetch(scenario_zh_url, cache_dir=cache_dir, fetcher=fetcher)
                    localized_payload = _parse_json(localized_asset)
                    source_assets.append(_asset_record(localized_asset, "scenario_translation"))
                except StoryImportError:
                    localized_payload = {}
            part_actions = _normalize_paired_actions(original_payload, localized_payload)
            if not part_actions:
                raise StoryAssetError(f"卡面剧情资源没有可读取的台词：{scenario_id}")
            offset = len(actions)
            part_segments = _target_segments(part_actions, include_context=False)
            for action in part_actions:
                action["index"] = int(action["index"]) + offset
            for segment in part_segments:
                segment["segment_id"] = f"segment-{len(segments) + 1:03d}"
                segment["start_index"] += offset
                segment["end_index"] += offset
                segment["evidence_refs"] = [int(ref) + offset for ref in segment["evidence_refs"]]
                segments.append(segment)
            actions.extend(part_actions)
            card_parts.append(
                {
                    "scenario_id": scenario_id,
                    "episode_no": _card_episode_number(descriptor),
                    "part_type": str(descriptor.get("cardEpisodePartType") or ""),
                }
            )
        scenario_ids = [str(item["scenario_id"]) for item in card_parts]
        story_id = "+".join(scenario_ids)
        descriptor = descriptors[0]
        story_assetbundle_name = str(descriptor.get("assetbundleName") or "")
        story_episode_title = str(descriptor.get("title") or "")
    else:
        try:
            descriptor = _find_descriptor(data_route, masters)
        except StoryAssetError as original_error:
            if data_route.region == "jp":
                raise
            fallback_route = replace(data_route, region="jp")
            fallback_masters, fallback_assets = _fetch_masters(fallback_route, cache_dir=cache_dir, fetcher=fetcher)
            try:
                descriptor = _find_descriptor(fallback_route, fallback_masters)
            except StoryAssetError as fallback_error:
                raise original_error from fallback_error
            data_route = fallback_route
            masters = fallback_masters
            source_assets.extend(fallback_assets)
        scenario_region = data_route.region
        scenario_payload: Any = {}
        scenario_regions = ["jp"]
        if data_route.region != "jp":
            scenario_regions.append(data_route.region)
        for candidate_region in scenario_regions:
            scenario_urls = _asset_url_candidates(candidate_region, data_route.route_type, descriptor)
            for scenario_url in scenario_urls:
                try:
                    asset = cached_fetch(scenario_url, cache_dir=cache_dir, fetcher=fetcher)
                    scenario_payload = _parse_json(asset)
                    source_assets.append(_asset_record(asset, "scenario"))
                    scenario_region = candidate_region
                    story_assetbundle_name = (
                        str(descriptor.get("_parent_assetbundle_name") or descriptor.get("assetbundleName") or "")
                        if scenario_url != scenario_urls[0]
                        else str(descriptor.get("assetbundleName") or "")
                    )
                    break
                except StoryImportError:
                    continue
            if scenario_payload:
                break
        translation_payload: Any = {}
        translation_url = _translation_path(route)
        if translation_url:
            try:
                asset = cached_fetch(translation_url, cache_dir=cache_dir, fetcher=fetcher)
                translation_payload = _parse_json(asset)
                source_assets.append(_asset_record(asset, "translation"))
            except StoryImportError:
                translation_url = None
        translation_episode = _select_translation_episode(translation_payload, route)
        actions = _normalize_actions(scenario_payload, translation_episode)
        story_id = str(
            descriptor.get("scenarioId")
            or f"{route.route_type}:{route.params['story_id']}:{route.params.get('episode_no', '')}"
        )
        story_episode_title = str(descriptor.get("title") or "")
    if not actions:
        raise StoryAssetError("没有找到可读取的剧情台词；页面可能尚未提供公开文本资产")
    draft_key = route.canonical_url + "\n" + story_id
    draft_id = f"story-{_sha256_text(draft_key)[:16]}"
    if route.route_type != "card":
        segments = _target_segments(actions, include_context=False)
    return {
        "schema_version": 1,
        "draft_id": draft_id,
        "status": "draft",
        "source": {
            "site": "pjsk.moe",
            "url": route.url,
            "canonical_url": route.canonical_url,
            "locale": route.locale,
            "route_type": route.route_type,
            "route_params": route.params,
            "data_region": data_route.region,
            "original_region": "jp" if route.route_type == "card" else scenario_region,
            "translation_region": "cn" if route.route_type == "card" else route.region,
            "fetched_at": _now_iso(),
            "source_hash": _sha256_text("\n".join(asset["sha256"] for asset in source_assets)),
            "assets": source_assets,
        },
        "page": page_meta,
        "story": {
            "title": page_meta.get("title", ""),
            "episode_title": story_episode_title,
            "scenario_id": story_id,
            "assetbundle_name": story_assetbundle_name,
            **({"scenario_ids": scenario_ids, "parts": card_parts} if route.route_type == "card" else {}),
        },
        "participants": list(
            {
                target: {
                    "id": target,
                    "name_ja": "東雲彰人" if target == "akito" else "青柳冬弥",
                    "name_zh": "东云彰人" if target == "akito" else "青柳冬弥",
                    "role": "target",
                }
                for target in ("akito", "toya")
                if any(target in _action_target_ids(row) for row in actions)
            }.values()
        ),
        "actions": actions,
        "target_segments": segments,
        "draft_analysis": {
            "status": "pending_llm" if enrich else "not_requested",
            "summary_zh": "",
            "timeline": [],
            "relationship_facts": [],
            "akito_attitude": [],
            "toya_traits": [],
            "uncertain_or_missing": [],
            "style_examples": [],
        },
        "review": {"status": "draft", "reviewer": "", "reviewed_at": "", "notes": []},
        "publish": {"event_memory_id": "", "published_at": ""},
    }


def validate_story_draft(payload: object) -> list[str]:
    """Return schema and evidence errors without modifying the payload."""
    if not isinstance(payload, dict):
        return ["draft root must be an object"]
    errors: list[str] = []
    for key in (
        "schema_version",
        "draft_id",
        "status",
        "source",
        "story",
        "actions",
        "target_segments",
        "review",
        "publish",
    ):
        if key not in payload:
            errors.append(f"missing field: {key}")
    if payload.get("schema_version") != 1:
        errors.append("unsupported schema_version")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions:
        errors.append("actions must be a non-empty array")
        actions = []
    action_indices = {row.get("index") for row in actions if isinstance(row, dict)}
    segments = payload.get("target_segments")
    if not isinstance(segments, list):
        errors.append("target_segments must be an array")
        segments = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            errors.append(f"target_segments[{index}] must be an object")
            continue
        refs = segment.get("evidence_refs")
        if not isinstance(refs, list) or not refs or not set(refs).issubset(action_indices):
            errors.append(f"target_segments[{index}] evidence_refs must reference actions")
    review = payload.get("review")
    if not isinstance(review, dict) or review.get("status") not in {"draft", "approved", "rejected"}:
        errors.append("review.status must be draft, approved or rejected")
    return errors


def save_draft(payload: dict[str, Any], *, data_dir: Path | None = None) -> Path:
    errors = validate_story_draft(payload)
    if errors:
        raise StoryImportError("草稿校验失败：" + "; ".join(errors[:5]))
    directory = Path(data_dir or "data") / "event_memory" / "story_import" / "drafts"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{payload['draft_id']}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=directory, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)
    return path


def load_draft(draft_id: str, *, data_dir: Path | None = None) -> tuple[dict[str, Any], Path]:
    if not re.fullmatch(r"story-[a-f0-9]{16}", str(draft_id or "")):
        raise StoryImportError("draft_id 格式无效")
    path = Path(data_dir or "data") / "event_memory" / "story_import" / "drafts" / f"{draft_id}.json"
    if not path.exists():
        raise StoryImportError(f"找不到草稿：{draft_id}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise StoryImportError(f"草稿无法读取：{draft_id}") from error
    errors = validate_story_draft(payload)
    if errors:
        raise StoryImportError("草稿校验失败：" + "; ".join(errors[:5]))
    return payload, path


def update_review(payload: dict[str, Any], status: str, *, reviewer: str = "local", note: str = "") -> dict[str, Any]:
    if status not in {"approved", "rejected"}:
        raise StoryImportError("审核状态只能是 approved 或 rejected")
    updated = json.loads(json.dumps(payload, ensure_ascii=False))
    updated["review"] = {
        "status": status,
        "reviewer": reviewer,
        "reviewed_at": _now_iso(),
        "notes": [*updated.get("review", {}).get("notes", []), note]
        if note
        else updated.get("review", {}).get("notes", []),
    }
    updated["status"] = status
    return updated


def _compact_memory_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</?[a-zA-Z][^>]*>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _memory_digest(event: dict[str, Any], field: str) -> str:
    value = event.get(field)
    if value:
        return str(value)
    source = event.get("source", {})
    return str(source.get(field) or "") if isinstance(source, dict) else ""


def _action_line(action: dict[str, Any], *, locale: str = "zh") -> str:
    speaker = str(action.get("speaker_zh" if locale == "zh" else "speaker_ja") or "").strip()
    raw_text = (action.get("text_zh") or action.get("text_ja")) if locale == "zh" else action.get("text_ja")
    text = _compact_memory_text(raw_text)
    if not text:
        return ""
    return f"{speaker}: {text}" if speaker else text


def _compact_lines(actions: list[dict[str, Any]], *, locale: str = "zh", limit: int = 600) -> str:
    lines: list[str] = []
    used = 0
    for action in actions:
        line = _action_line(action, locale=locale)
        if not line or line in lines:
            continue
        remaining = limit - used - (1 if lines else 0)
        if remaining <= 0:
            break
        if len(line) > remaining:
            if lines:
                continue
            line = line[:remaining].rstrip()
        if not line:
            break
        lines.append(line)
        used += len(line) + (1 if len(lines) > 1 else 0)
        if used >= limit:
            break
    return "\n".join(lines)


def _salient_rows(actions: list[dict[str, Any]], *, target: str, limit: int) -> list[dict[str, Any]]:
    markers = {
        "akito": (
            "搭档",
            "信任",
            "期待",
            "音轨",
            "作曲",
            "音乐",
            "唱",
            "演出",
            "练习",
            "一起",
            "冬弥",
            "出色",
            "努力",
            "很棒",
            "半吊子",
            "RAD WEEKEND",
        ),
        "toya": (
            "彰人",
            "搭档",
            "信任",
            "组队",
            "音轨",
            "作曲",
            "音乐",
            "唱",
            "演出",
            "练习",
            "感谢",
            "支柱",
            "并肩",
            "古典乐",
            "不安",
            "愿望",
            "梦想",
            "父亲",
            "RAD WEEKEND",
        ),
    }[target]
    pivotal_markers = {
        "akito": (
            "知道了",
            "明白了",
            "随你",
            "同意",
            "答应",
            "约定",
            "保证",
            "阻止",
            "撑不",
            "坚持",
            "退让",
            "理由",
            "身体不舒服",
            "发烧",
            "冷敷",
            "保存体力",
            "我会去",
            "我来说明",
            "わかった",
            "好きにしろ",
            "止める",
            "譲れ",
            "体力を温存",
        ),
        "toya": (
            "身体不适",
            "身体不舒服",
            "发烧",
            "疲劳",
            "想唱",
            "坚持",
            "Embers",
            "对决",
            "后悔",
            "不给队伍添麻烦",
            "竭尽",
            "拜托",
            "保证",
            "听你的",
            "従う",
            "約束",
            "歌いたい",
            "後悔",
            "頼む",
        ),
    }[target]
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for ordinal, action in enumerate(actions):
        text = _compact_memory_text(action.get("text_zh") or action.get("text_ja"))
        if not text:
            continue
        marker_hits = sum(marker.lower() in text.lower() for marker in markers)
        pivotal_hits = sum(marker.lower() in text.lower() for marker in pivotal_markers)
        score = (
            (pivotal_hits * 6.0)
            + (marker_hits * 4.0)
            + (1.5 if action.get("kind") == "dialogue" else 0.0)
            + min(len(text), 160) / 160
        )
        scored.append((score, ordinal, action))
    selected = sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
    return [item[2] for item in sorted(selected, key=lambda item: item[1])]


def _evidence_units(
    payload: dict[str, Any], segments: list[dict[str, Any]], *, evidence_type: str = "story"
) -> list[dict[str, Any]]:
    actions = {
        row.get("index"): row
        for row in payload.get("actions", [])
        if isinstance(row, dict) and row.get("index") is not None
    }
    units: list[dict[str, Any]] = []
    for segment in segments:
        refs = segment.get("evidence_refs", [])
        if not isinstance(refs, list):
            continue
        rows = [actions[ref] for ref in refs if ref in actions]
        target_rows = [row for row in rows if _action_target_ids(row)]
        akito_rows = [row for row in target_rows if "akito" in _action_target_ids(row)]
        toya_rows = [row for row in target_rows if "toya" in _action_target_ids(row)]
        if not akito_rows or not toya_rows:
            continue
        dialogue = _compact_lines(_salient_rows(akito_rows, target="akito", limit=5), limit=480)
        if not dialogue:
            continue
        context_rows = _salient_rows(toya_rows, target="toya", limit=6)
        context = _compact_lines(context_rows, limit=560)
        if not context:
            context = "彰人与冬弥在该剧情片段中共同互动。"
        units.append(
            {
                "record_index": int(segment.get("start_index", refs[0] if refs else -1)),
                "type": evidence_type,
                "context": context,
                "dialogue": dialogue,
            }
        )
    return units


def _fallback_memory_summary(evidence: list[dict[str, Any]], topics: list[str]) -> str:
    labels = "、".join(topics[:3])
    if labels:
        return f"彰人与冬弥在原作剧情中共同互动，围绕{labels}展开。"
    if evidence:
        return "彰人与冬弥在原作剧情中共同经历了一段互动。"
    return ""


def _memory_keywords(summary: str, topics: list[str], evidence: list[dict[str, Any]]) -> list[str]:
    corpus = _compact_memory_text(
        " ".join(
            [
                summary,
                *topics,
                *(row.get("context", "") for row in evidence),
                *(row.get("dialogue", "") for row in evidence),
            ]
        )
    )
    markers = (
        "初遇",
        "相遇",
        "组队",
        "搭档",
        "梦想",
        "信任",
        "支柱",
        "并肩",
        "音乐",
        "作曲",
        "音轨",
        "古典乐",
        "演出",
        "练习",
        "生日",
        "惊喜",
        "聚餐",
        "雪仗",
        "父亲",
        "RAD WEEKEND",
    )
    return list(
        dict.fromkeys(
            [
                *(str(item).strip() for item in topics if str(item).strip()),
                *(marker for marker in markers if marker in corpus),
                "冬弥",
                "彰人",
            ]
        )
    )[:16]


def _relationship_tags(summary: str, topics: list[str]) -> list[str]:
    searchable = _compact_memory_text(" ".join([summary, *topics]))
    return list(
        dict.fromkeys(
            tag
            for marker, tag in (
                ("练习", "练习"),
                ("演出", "演出"),
                ("创作", "共同音乐"),
                ("音乐", "共同音乐"),
                ("信任", "信任"),
                ("支持", "支持"),
                ("搭档", "搭档"),
            )
            if marker in searchable
        )
    )


def event_memory_from_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Convert an approved draft to the existing event-memory shape."""
    if payload.get("review", {}).get("status") != "approved":
        raise StoryImportError("只有 approved 草稿可以发布")
    source = payload["source"]
    story = payload["story"]
    stable_key = f"{source['canonical_url']}\n{story['scenario_id']}"
    event_id = f"akito-toya-web-{_sha256_text(stable_key)[:12]}"
    analysis = payload.get("draft_analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
    segments = [
        segment
        for segment in payload.get("target_segments", [])
        if isinstance(segment, dict) and segment.get("evidence_refs")
    ]
    analysis_topics = analysis.get("topics", [])
    if not isinstance(analysis_topics, list):
        analysis_topics = []
    topics = list(dict.fromkeys(str(item).strip() for item in analysis_topics if str(item).strip()))[:8]
    if not segments:
        raise StoryImportError("草稿中没有可发布的彰人/冬弥证据")
    evidence = _evidence_units(payload, segments, evidence_type=str(source.get("route_type") or "story"))
    if not evidence:
        raise StoryImportError("草稿中没有可发布的彰人台词证据")
    summary = _compact_memory_text(analysis.get("summary_zh"))
    if not summary:
        summary = _fallback_memory_summary(evidence, topics)
    if not summary:
        raise StoryImportError("草稿中没有可发布的事件摘要；请先补充审核摘要")
    record_indices = list(
        dict.fromkeys(int(item["record_index"]) for item in evidence if int(item["record_index"]) >= 0)
    )
    content_digest = story_content_digest(payload)
    evidence_digest = story_evidence_digest(payload)
    return {
        "event_id": event_id,
        "source": {
            "url": source["canonical_url"],
            "draft_id": payload["draft_id"],
            "record_indices": record_indices,
            "content_digest": content_digest,
            "evidence_digest": evidence_digest,
        },
        "title": "",
        "summary": summary,
        "category": "冬弥·彰冬",
        "topics": topics,
        "confidence": "high",
        "entities": ["akito", "toya"],
        "participants": ["彰人", "冬弥"],
        "relationship_tags": _relationship_tags(summary, topics),
        "timeline": [],
        "locations": [],
        "evidence": evidence,
        "keywords": _memory_keywords(summary, topics, evidence),
    }


def _event_memory_path(data_dir: Path | None) -> Path:
    return Path(data_dir or "data") / "content" / "akito_event_memories.json"


def _load_event_inventory(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            inventory = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as error:
            raise StoryImportError("现有事件记忆文件无法读取") from error
    else:
        inventory = {"schema_version": 1, "source": {"kind": "story_import"}, "events": []}
    if not isinstance(inventory, dict) or not isinstance(inventory.get("events"), list):
        raise StoryImportError("现有事件记忆文件格式不正确")
    return inventory


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_name = handle.name
    Path(temp_name).replace(path)


def preview_event_memory(payload: dict[str, Any], *, data_dir: Path | None = None) -> dict[str, Any]:
    """Describe whether an approved draft is new, duplicate, or a revision."""
    memory = event_memory_from_draft(payload)
    inventory = _load_event_inventory(_event_memory_path(data_dir))
    events = [item for item in inventory["events"] if isinstance(item, dict)]
    event_id = memory["event_id"]
    existing = next((item for item in events if item.get("event_id") == event_id), None)
    incoming_digest = _memory_digest(memory, "content_digest")
    incoming_evidence_digest = _memory_digest(memory, "evidence_digest")
    if existing is not None:
        existing_digest = _memory_digest(existing, "content_digest")
        existing_evidence_digest = _memory_digest(existing, "evidence_digest")
        same_content = existing_digest == incoming_digest or (
            not existing_digest and incoming_evidence_digest and existing_evidence_digest == incoming_evidence_digest
        )
        status = "same_identity" if same_content else "revision"
        return {
            "status": status,
            "event_id": event_id,
            "incoming_content_digest": incoming_digest,
            "existing_content_digest": existing_digest,
            "incoming_evidence_digest": incoming_evidence_digest,
            "existing_evidence_digest": existing_evidence_digest,
            "existing_event": existing,
            "reason": "同一来源和内容" if status == "same_identity" else "同一来源内容发生变化",
        }
    duplicate = next(
        (
            item
            for item in events
            if _memory_digest(item, "content_digest") == incoming_digest
            or (incoming_evidence_digest and _memory_digest(item, "evidence_digest") == incoming_evidence_digest)
        ),
        None,
    )
    if duplicate is not None:
        return {
            "status": "duplicate_content",
            "event_id": event_id,
            "incoming_content_digest": incoming_digest,
            "existing_content_digest": incoming_digest,
            "incoming_evidence_digest": incoming_evidence_digest,
            "existing_evidence_digest": _memory_digest(duplicate, "evidence_digest"),
            "existing_event": duplicate,
            "reason": "不同来源但规范化剧情内容相同",
        }
    return {
        "status": "new",
        "event_id": event_id,
        "incoming_content_digest": incoming_digest,
        "existing_content_digest": "",
        "incoming_evidence_digest": incoming_evidence_digest,
        "existing_evidence_digest": "",
        "existing_event": None,
        "reason": "没有匹配的现有事件",
    }


def _revision_snapshot_path(root: Path, event_id: str, content_digest: str) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return root / "event_memory" / "story_import" / "revisions" / event_id / f"{timestamp}-{content_digest[:16]}.json"


def merge_event_memory(
    payload: dict[str, Any],
    *,
    data_dir: Path | None = None,
    confirm_revision: bool = False,
) -> tuple[Path, str]:
    """Merge an approved draft, requiring explicit confirmation for revisions."""
    memory = event_memory_from_draft(payload)
    root = Path(data_dir or "data")
    path = _event_memory_path(root)
    inventory = _load_event_inventory(path)
    events = [item for item in inventory["events"] if isinstance(item, dict)]
    existing = next((item for item in events if item.get("event_id") == memory["event_id"]), None)
    existing_digest = _memory_digest(existing, "content_digest") if existing else ""
    incoming_digest = _memory_digest(memory, "content_digest")
    incoming_evidence_digest = _memory_digest(memory, "evidence_digest")
    if existing is None and any(
        _memory_digest(item, "content_digest") == incoming_digest
        or (
            not _memory_digest(item, "content_digest")
            and incoming_evidence_digest
            and _memory_digest(item, "evidence_digest") == incoming_evidence_digest
        )
        for item in events
    ):
        raise StoryImportError("剧情内容与已有事件重复；请在网页中查看已有来源")
    if existing is not None and (
        existing_digest == incoming_digest
        or (
            not existing_digest
            and incoming_evidence_digest
            and _memory_digest(existing, "evidence_digest") == incoming_evidence_digest
        )
    ):
        revision = existing.get("revision")
        if isinstance(revision, dict) and int(revision.get("number", 1) or 1) > 1:
            memory["revision"] = revision
        if existing != memory:
            events = [memory if item.get("event_id") == memory["event_id"] else item for item in events]
            inventory["events"] = events
            _write_json_atomic(path, inventory)
        return path, memory["event_id"]
    if existing is not None:
        if not confirm_revision:
            raise StoryImportError("同一剧情已有不同版本；请先完成去重预览并确认修订")
        revision = existing.get("revision", {})
        revision_number = int(revision.get("number", 1) or 1) + 1 if isinstance(revision, dict) else 2
        snapshot_path = _revision_snapshot_path(root, memory["event_id"], incoming_digest)
        _write_json_atomic(
            snapshot_path,
            {
                "schema_version": 1,
                "created_at": _now_iso(),
                "event_id": memory["event_id"],
                "reason": "revision",
                "previous_event": existing,
                "incoming_event": memory,
            },
        )
        memory["revision"] = {"number": revision_number, "published_at": _now_iso(), "snapshot": str(snapshot_path)}
    events = [item for item in events if item.get("event_id") != memory["event_id"]]
    events.append(memory)
    inventory["events"] = events
    inventory["schema_version"] = max(int(inventory.get("schema_version", 1)), 1)
    _write_json_atomic(path, inventory)
    return path, memory["event_id"]
