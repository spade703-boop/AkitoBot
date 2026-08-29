"""Pure lexical scoring helpers shared by runtime retrieval and offline evaluation."""

from __future__ import annotations

import re
from typing import Any
import unicodedata

_GENERIC_TERMS = {
    "彰人",
    "冬弥",
    "青柳",
    "东云",
    "東雲",
    "toya",
    "akito",
    "大家",
    "我们",
    "你们",
    "一起",
    "时候",
    "事情",
    "这次",
    "那次",
    "之后",
    "现在",
    "一直",
    "还是",
    "真的",
    "的话",
    "东西",
    "可以",
    "觉得",
    "知道",
    "看到",
    "听到",
    "想到",
    "当时",
    "发生",
}
_QUERY_STOP_PHRASES = (
    "你还记得",
    "还记得",
    "你们以前",
    "以前",
    "那一次",
    "那次",
    "那天",
    "上次",
    "那个事情",
    "那个事",
    "后来",
    "当时",
    "发生过什么",
    "发生了什么",
    "发生过",
    "怎么样了",
    "怎么样",
    "怎么说的",
    "怎么说",
    "说说",
    "是不是",
    "有没有",
    "什么",
    "青柳冬弥",
    "东云彰人",
    "東雲彰人",
    "冬弥",
    "彰人",
    "青柳",
    "东云",
    "東雲",
    "你们",
    "你自己",
    "自己",
    "你",
    "他",
    "的",
    "了",
    "吗",
    "吧",
    "呢",
    "挺",
    "有点",
)
_QUERY_ALIASES = (
    ("庆生", "生日"),
    ("庆祝", "生日"),
    ("生日歌", "生日唱歌"),
    ("吃饭", "聚餐"),
    ("一起吃饭", "聚餐"),
    ("闹过头", "张扬"),
    ("别闹", "张扬"),
    ("努力学习", "学习"),
)
_SIGNAL_TERMS = (
    "生日",
    "惊喜",
    "甜食",
    "唱歌",
    "胜负",
    "切蛋糕",
    "聚餐",
    "学校",
    "热闹",
    "规则",
    "雪仗",
    "提醒",
    "学习",
    "感谢",
    "配合",
    "开心",
    "祝福",
    "讨论",
    "努力",
    "聚会",
    "清晨",
    "很早",
    "早上",
    "sekai",
    "rad blast",
    "练习",
    "技术",
    "不足",
    "并肩",
    "演出",
    "舞蹈",
    "露营",
    "篝火",
    "菜刀",
    "足球",
    "笔记",
    "帐篷",
    "绊倒",
    "蘑菇",
    "养猫",
    "学生寮",
    "纽约",
    "音轨",
    "古典乐",
    "发烧",
    "embers",
    "热情",
    "相遇",
    "组队",
    "搭档",
    "超越",
    "鼓励",
    "约定",
    "创造",
    "一起住",
)
_CURATED_PRIORITY_MARGIN = 0.25


def normalize(value: object) -> str:
    text = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or "")).lower())
    for source, target in _QUERY_ALIASES:
        text = text.replace(source, target)
    return text


def ngrams(value: object) -> set[str]:
    text = normalize(value)
    terms = set(re.findall(r"[a-z0-9]{2,}|[\u4e00-\u9fff]{2,}", text))
    for match in re.findall(r"[\u4e00-\u9fff]+", text):
        for size in (2, 3, 4):
            terms.update(match[index : index + size] for index in range(max(0, len(match) - size + 1)))
    return {term for term in terms if term and term not in _GENERIC_TERMS}


def _maximal_overlap(query_terms: set[str], value: object) -> set[str]:
    shared = ngrams(value) & query_terms
    return {
        term
        for term in shared
        if not any(other != term and term in other for other in shared)
    }


def has_specific_event_cue(query: str) -> bool:
    cue_text = normalize(query)
    for phrase in _QUERY_STOP_PHRASES:
        cue_text = cue_text.replace(phrase, "")
    latin_terms = re.findall(r"[a-z0-9]{2,}", cue_text)
    chinese_text = "".join(re.findall(r"[\u4e00-\u9fff]+", cue_text))
    return bool(latin_terms or len(chinese_text) >= 2)


def signal_cue_count(query: str) -> int:
    query_text = normalize(query)
    return sum(term in query_text for term in _SIGNAL_TERMS)


def event_signal_cue_count(query: str, event: dict[str, Any]) -> int:
    query_text = normalize(query)
    event_text = normalize(
        " ".join(
            str(value)
            for value in (
                event.get("title", ""),
                event.get("summary", ""),
                *(event.get("topics", []) if isinstance(event.get("topics"), list) else []),
                *(event.get("keywords", []) if isinstance(event.get("keywords"), list) else []),
                *(event.get("relationship_tags", []) if isinstance(event.get("relationship_tags"), list) else []),
            )
        )
    )
    return sum(term in query_text and term in event_text for term in _SIGNAL_TERMS)


def _value_score(query_text: str, query_terms: set[str], value: object, signal_weight: float) -> float:
    normalized = normalize(value)
    if not normalized:
        return 0.0
    overlap = _maximal_overlap(query_terms, normalized)
    score = min(2.5, 1.25 * len(overlap))
    score += signal_weight * min(3, sum(term in query_text and term in normalized for term in _SIGNAL_TERMS))
    return score


def score_event(query: str, event: dict[str, Any]) -> float:
    """Score an event once per semantic field group, not once per duplicate text."""
    query_text = normalize(query)
    query_terms = ngrams(query)
    title = event.get("title", "")
    title_score = _value_score(query_text, query_terms, title, 1.0)
    normalized_title = normalize(title)
    if len(normalized_title) >= 2 and normalized_title in query_text:
        title_score += 2.0

    concise_keywords = [
        item for item in event.get("keywords", [])
        if isinstance(item, str) and 0 < len(normalize(item)) <= 40
    ]
    keyword_score = max(
        (
            _value_score(query_text, query_terms, item, 0.65)
            + (1.0 if len(normalize(item)) >= 2 and normalize(item) in query_text else 0.0)
            for item in concise_keywords
        ),
        default=0.0,
    )
    summary_score = _value_score(query_text, query_terms, event.get("summary", ""), 0.35)

    metadata_values = [
        item
        for field in ("topics", "relationship_tags")
        for item in event.get(field, [])
        if isinstance(item, str)
    ]
    metadata_score = max(
        (_value_score(query_text, query_terms, item, 0.25) for item in metadata_values),
        default=0.0,
    )

    evidence_scores: list[float] = []
    for row in event.get("evidence", []):
        if not isinstance(row, dict):
            continue
        evidence_scores.append(
            max(
                _value_score(query_text, query_terms, row.get("context", ""), 0.3),
                _value_score(query_text, query_terms, row.get("dialogue", ""), 0.3),
            )
        )
    evidence_score = 0.55 * sum(sorted(evidence_scores, reverse=True)[:2])
    return round(title_score + 0.8 * keyword_score + 0.55 * summary_score + 0.3 * metadata_score + evidence_score, 3)


def source_kind(event: dict[str, Any]) -> str:
    explicit = str(event.get("source_kind") or "").strip().lower()
    if explicit in {"curated_story", "legacy_script"}:
        return explicit
    source = event.get("source")
    return "curated_story" if isinstance(source, dict) and (source.get("draft_id") or source.get("url")) else "legacy_script"


def rank_events(query: str, events: list[dict[str, Any]]) -> list[tuple[float, dict[str, Any]]]:
    ranked = [(score_event(query, event), event) for event in events]
    ranked = [(round(score, 3), event) for score, event in ranked if score > 0]
    ranked.sort(
        key=lambda item: (
            -item[0],
            0 if source_kind(item[1]) == "curated_story" else 1,
            str(item[1].get("event_id") or ""),
        )
    )
    return ranked


def qualified_events(
    scored: list[tuple[float, dict[str, Any]]],
    *,
    top_k: int,
    minimum_score: float,
    max_score_gap: float,
) -> tuple[list[tuple[float, dict[str, Any]]], bool]:
    if not scored:
        return [], False
    top_score = scored[0][0]
    qualified = [
        item for item in scored
        if item[0] >= minimum_score and top_score - item[0] <= max_score_gap
    ]
    curated = [item for item in qualified if source_kind(item[1]) == "curated_story"]
    legacy = [item for item in qualified if source_kind(item[1]) != "curated_story"]
    if curated and should_prioritize_curated(curated, legacy):
        return curated[: max(1, min(3, int(top_k)))], True
    return qualified[: max(1, min(3, int(top_k)))], False


def should_prioritize_curated(
    curated: list[tuple[float, dict[str, Any]]],
    legacy: list[tuple[float, dict[str, Any]]],
    *,
    margin: float = _CURATED_PRIORITY_MARGIN,
) -> bool:
    return bool(curated and (not legacy or curated[0][0] >= legacy[0][0] + margin))
