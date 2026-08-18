"""Parsing, selection, and cooldown helpers for random_paro draws."""

from __future__ import annotations

import random
import time

from .stats import _cooldown_store
from .store import PARO_DATA, _save_stats

EASTER_EGG_RATE = 0.03
FOXRABBIT_RATE = 0.02
DRAW_LIMIT = 3
DRAW_WINDOW = 1800


def fuzzy_match(name: str, pool: list[str]) -> str | list[str] | None:
    name_lower = name.lower()
    exact = [entry for entry in pool if entry.lower() == name_lower]
    if exact:
        return exact[0]
    prefix = [entry for entry in pool if entry.lower().startswith(name_lower)]
    if prefix:
        return prefix[0] if len(prefix) == 1 else prefix
    contains = [entry for entry in pool if name_lower in entry.lower()]
    if len(contains) == 1:
        return contains[0]
    return contains or None


def parse_draw_request(raw_text: str) -> tuple[int, str]:
    count = 1
    directional = ""
    raw = raw_text.strip()
    if not raw:
        return count, directional
    tokens = raw.split()
    for index, token in enumerate(tokens):
        if token.isdigit() and 1 <= int(token) <= 3:
            return int(token), " ".join(tokens[:index] + tokens[index + 1 :])
    return count, raw


def resolve_directional_draw(
    directional: str, akito_pool: list[str], toya_pool: list[str]
) -> tuple[str | None, str | None, str | None]:
    if not directional:
        return None, None, None
    lowered = directional.lower()
    if lowered.startswith("彰人"):
        name = directional[2:].strip()
        if not name:
            return None, None, "请指定彰人的派生名称，例如：抽派生 彰人 黑百合"
        match = fuzzy_match(name, akito_pool)
        if not match:
            return None, None, f"彰人的派生池里没有与「{name}」匹配的条目。"
        if isinstance(match, list):
            return None, None, f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。"
        return match, None, None
    if lowered.startswith("冬弥"):
        name = directional[2:].strip()
        if not name:
            return None, None, "请指定冬弥的派生名称，例如：抽派生 冬弥 王子冬"
        match = fuzzy_match(name, toya_pool)
        if not match:
            return None, None, f"冬弥的派生池里没有与「{name}」匹配的条目。"
        if isinstance(match, list):
            return None, None, f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。"
        return None, match, None
    return None, None, "请指定要固定哪一方的派生，例如：抽派生 彰人 黑百合。\n彰冬不拆不逆，一方派生固定则另一方派生随机。"


def prune_draw_history(history: list[float], now_ts: float, window: int = DRAW_WINDOW) -> list[float]:
    return [timestamp for timestamp in history if now_ts - timestamp < window]


def build_draw_limit_message(
    *,
    remaining_before: int,
    requested_count: int,
    history: list[float],
    now_ts: float,
    draw_limit: int = DRAW_LIMIT,
    draw_window: int = DRAW_WINDOW,
) -> str | None:
    if remaining_before >= requested_count:
        return None
    if remaining_before <= 0:
        oldest = min(history)
        wait = int(draw_window - (now_ts - oldest))
        return f"30分钟内最多抽{draw_limit}次，你已用完次数，请在 {wait // 60} 分 {wait % 60} 秒后再试。"
    return f"30分钟内仅剩 {remaining_before} 次，无法抽 {requested_count} 次。"


def get_fixed_side(fixed_a: str | None, fixed_b: str | None) -> str | None:
    if fixed_a and not fixed_b:
        return "akito"
    if fixed_b and not fixed_a:
        return "toya"
    return None


def draw_results(
    count: int,
    *,
    fixed_a: str | None = None,
    fixed_b: str | None = None,
    akito_pool: list[str] | None = None,
    toya_pool: list[str] | None = None,
) -> list[tuple[str, str, bool, str | None]]:
    akito_pool = akito_pool if akito_pool is not None else PARO_DATA.get("akito_pool", [])
    toya_pool = toya_pool if toya_pool is not None else PARO_DATA.get("toya_pool", [])
    results = []
    foxrabbit_used = False
    for _ in range(count):
        akito_name = fixed_a or random.choice(akito_pool)
        toya_name = fixed_b or random.choice(toya_pool)
        is_egg = random.random() < EASTER_EGG_RATE
        fox_type = None
        if not is_egg and not foxrabbit_used:
            roll = random.random()
            if roll < FOXRABBIT_RATE:
                fox_type = "fox"
            elif roll < FOXRABBIT_RATE * 2:
                fox_type = "rabbit"
            elif roll < FOXRABBIT_RATE * 3:
                fox_type = "foxrabbit"
            elif roll < FOXRABBIT_RATE * 4:
                fox_type = "foxbun"
            if fox_type:
                foxrabbit_used = True
        results.append((akito_name, toya_name, is_egg, fox_type))
    return results


def consume_cooldown(user_id: str, count: int, *, now_ts: float | None = None) -> tuple[int, str | None]:
    now_ts = time.time() if now_ts is None else now_ts
    cooldowns = _cooldown_store()
    previous = list(cooldowns.get(user_id, []))
    history = prune_draw_history(previous, now_ts)
    cooldowns[user_id] = history
    if history != previous:
        _save_stats()
    remaining_before = DRAW_LIMIT - len(history)
    message = build_draw_limit_message(
        remaining_before=remaining_before,
        requested_count=count,
        history=history,
        now_ts=now_ts,
    )
    if message:
        return remaining_before, message
    history.extend([now_ts] * count)
    cooldowns[user_id] = history
    return DRAW_LIMIT - len(history), None


__all__ = [name for name in globals() if not name.startswith("__")]
