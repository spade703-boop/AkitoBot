"""Text normalization, token counting, and daily report aggregation."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from datetime import time as time_type
import re
from typing import Any
import unicodedata

from ...core import TZ_CN, find_data_path
from . import store

MAX_WORDS = 50
RAW_RETENTION_DAYS = 7

_URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+")
_CQ_RE = re.compile(r"\[CQ:[^\]]+\]")
_VALID_TOKEN_RE = re.compile(r"(?:[\u3400-\u9fff]{2,}|[a-z][a-z0-9_]*[a-z0-9])")
_DEFAULT_STOPWORDS = {
    "一个", "一些", "不是", "什么", "这个", "那个", "然后", "但是", "就是", "还是", "可以",
    "因为", "所以", "如果", "已经", "没有", "怎么", "为什么", "真的", "感觉", "觉得", "现在",
    "今天", "昨天", "明天", "哈哈", "哈哈哈", "啊啊", "啊啊啊", "我们", "你们", "他们", "自己",
    "the", "and", "for", "with", "that", "this", "you", "are", "but", "not",
}

TokenCutter = Callable[[str], Iterable[str]]


def normalize_word(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().lower()


def parse_blocked_word_arguments(raw: str) -> list[str]:
    """Parse comma- or whitespace-separated exact tokens for admin commands."""
    words: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,，]+", raw):
        word = normalize_word(part)
        if not word or word in seen or not _VALID_TOKEN_RE.fullmatch(word):
            continue
        seen.add(word)
        words.append(word)
    return words


def parse_excluded_user_arguments(raw: str) -> list[str]:
    """Parse whitespace- or comma-separated QQ IDs for global exclusion."""
    user_ids: list[str] = []
    seen: set[str] = set()
    for part in re.split(r"[\s,，]+", unicodedata.normalize("NFKC", raw)):
        user_id = part.strip()
        if not re.fullmatch(r"\d{5,20}", user_id) or user_id in seen:
            continue
        seen.add(user_id)
        user_ids.append(user_id)
    return user_ids


def _load_stopwords() -> set[str]:
    words = set(_DEFAULT_STOPWORDS)
    path = find_data_path("wordcloud_stopwords.txt")
    if not path:
        return words
    try:
        with open(path, encoding="utf-8") as file:
            words.update(normalize_word(line) for line in file if line.strip() and not line.lstrip().startswith("#"))
    except OSError:
        return words
    return words


def create_jieba_cutter(extra_words: Iterable[str] = ()) -> TokenCutter:
    """Create an isolated Jieba tokenizer and load the optional user dictionary."""
    import jieba

    tokenizer = jieba.Tokenizer()
    user_dict = find_data_path("wordcloud_user_dict.txt")
    if user_dict:
        tokenizer.load_userdict(str(user_dict))
    for word in extra_words:
        tokenizer.add_word(word)
    return tokenizer.cut


def clean_message_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text).lower()
    normalized = _CQ_RE.sub(" ", normalized)
    return _URL_RE.sub(" ", normalized)


def is_recordable_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped or stripped.startswith("/"):
        return False
    cleaned = clean_message_text(stripped)
    return bool(re.search(r"[a-z\u3400-\u9fff]", cleaned))


def extract_tokens(
    text: str,
    *,
    cutter: TokenCutter,
    stopwords: set[str],
    blocked_words: set[str],
) -> list[str]:
    tokens: list[str] = []
    for raw_token in cutter(clean_message_text(text)):
        token = normalize_word(raw_token)
        if (
            token
            and token not in stopwords
            and token not in blocked_words
            and _VALID_TOKEN_RE.fullmatch(token)
        ):
            tokens.append(token)
    return tokens


def date_bounds(report_date: date) -> tuple[int, int]:
    start = datetime.combine(report_date, time_type.min, tzinfo=TZ_CN)
    end = start + timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def retention_cutoff(today: date) -> int:
    cutoff_date = today - timedelta(days=RAW_RETENTION_DAYS)
    return int(datetime.combine(cutoff_date, time_type.min, tzinfo=TZ_CN).timestamp())


def build_report(
    group_id: str,
    report_date: date,
    rows: list[tuple[str, str, str, int]],
    *,
    blocked_words: set[str],
    cutter: TokenCutter,
    stopwords: set[str] | None = None,
) -> dict[str, Any]:
    """Aggregate raw rows into deterministic word and contributor rankings."""
    effective_stopwords = stopwords if stopwords is not None else _load_stopwords()
    frequencies: Counter[str] = Counter()
    per_word_users: dict[str, Counter[str]] = defaultdict(Counter)
    latest_names: dict[str, str] = {}
    effective_message_count = 0
    participant_ids: set[str] = set()

    for user_id, nickname, content, _event_time in rows:
        message_tokens = extract_tokens(
            content,
            cutter=cutter,
            stopwords=effective_stopwords,
            blocked_words=blocked_words,
        )
        if not message_tokens:
            continue
        effective_message_count += 1
        participant_ids.add(user_id)
        latest_names[user_id] = nickname.strip() or f"用户{user_id}"
        frequencies.update(message_tokens)
        for token, count in Counter(message_tokens).items():
            per_word_users[token][user_id] += count

    sorted_frequencies = sorted(frequencies.items(), key=lambda item: (-item[1], item[0]))[:MAX_WORDS]
    top_words = []
    for word, count in sorted_frequencies[:3]:
        contributors = sorted(
            per_word_users[word].items(),
            key=lambda item: (-item[1], item[0]),
        )[:5]
        top_words.append(
            {
                "word": word,
                "count": count,
                "contributors": [
                    {
                        "user_id": user_id,
                        "nickname": latest_names.get(user_id, f"用户{user_id}"),
                        "count": user_count,
                    }
                    for user_id, user_count in contributors
                ],
            }
        )

    return {
        "group_id": str(group_id),
        "report_date": report_date.isoformat(),
        "message_count": effective_message_count,
        "participant_count": len(participant_ids),
        "frequencies": [[word, count] for word, count in sorted_frequencies],
        "top_words": top_words,
    }


async def aggregate_report(
    group_id: str,
    report_date: date,
    *,
    cutter: TokenCutter | None = None,
) -> dict[str, Any]:
    start_time, end_time = date_bounds(report_date)
    rows = await store.fetch_raw_messages(group_id, start_time, end_time)
    excluded_user_ids = set(await store.list_excluded_user_ids())
    if excluded_user_ids:
        rows = [row for row in rows if row[0] not in excluded_user_ids]
    blocked_words = set(await store.list_blocked_words())
    report = build_report(
        group_id,
        report_date,
        rows,
        blocked_words=blocked_words,
        cutter=cutter or create_jieba_cutter(blocked_words),
    )
    await store.save_report(group_id, report_date.isoformat(), report)
    return report


async def aggregate_history_report(
    group_id: str,
    report_date: date,
    *,
    excluded_user_ids: Iterable[str] = (),
    cutter: TokenCutter | None = None,
) -> dict[str, Any]:
    """Aggregate a report from the long-lived impression history database."""
    start_time, end_time = date_bounds(report_date)
    rows = await store.fetch_history_messages(group_id, start_time, end_time)
    excluded = {str(user_id) for user_id in excluded_user_ids}
    excluded.update(await store.list_excluded_user_ids())
    if excluded:
        rows = [row for row in rows if row[0] not in excluded]
    blocked_words = set(await store.list_blocked_words())
    report = build_report(
        group_id,
        report_date,
        rows,
        blocked_words=blocked_words,
        cutter=cutter or create_jieba_cutter(blocked_words),
    )
    await store.save_report(group_id, report_date.isoformat(), report)
    return report
