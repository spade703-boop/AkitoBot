"""抽派生：从派生池随机 / 模糊匹配抽取，渲染成图片（含狐兔 / 狐包等合成图）。"""

from __future__ import annotations

import asyncio
from datetime import datetime
import io
import json
import os
from pathlib import Path
import random
import time

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from PIL import Image, ImageDraw

from ...core import (
    ALLOWED_CHAT_GROUPS,
    IMAGE_BASE_PATH,
    SUPERUSER_QQ,
    TZ_CN,
    find_data_path,
    get_data_dir,
    load_json_file,
)
from .._shared import load_msyhbd_font
from .render import render_random_paro_page

DATA_FILE = "paro_pools.json"
STATS_FILE = "paro_stats.json"
EGG_LOG_FILE = "paro_egg_log.jsonl"
PARO_USE_HTML_RENDER = os.environ.get("PARO_USE_HTML_RENDER", "1").strip() not in {"0", "false", "False"}
DEFAULT_DATA = {"akito_pool": [], "toya_pool": []}

PARO_DATA: dict = load_json_file(DATA_FILE, DEFAULT_DATA)


def _save():
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(PARO_DATA, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _stats_path() -> Path:
    path = find_data_path(STATS_FILE)
    if not path:
        path = get_data_dir() / STATS_FILE
    return path


def _egg_log_path() -> Path:
    path = find_data_path(EGG_LOG_FILE)
    if not path:
        path = get_data_dir() / EGG_LOG_FILE
    return path


def _new_period_stats(*, date: str | None = None) -> dict:
    stats = {
        "total_draws": 0,
        "user_draw_counts": {},
        "akito_hits": {},
        "toya_hits": {},
        "akito_last_hit_seq": {},
        "toya_last_hit_seq": {},
        "egg_user_counts": {},
        "foxrabbit_total": 0,
        "foxbun_total": 0,
        "fox_total": 0,
        "rabbit_total": 0,
        "_seq": 0,
    }
    if date is not None:
        stats["date"] = date
    return stats


def _new_user_stats() -> dict:
    return {
        "draw_count": 0,
        "egg_count": 0,
        "foxbun_count": 0,
        "akito_hits": {},
        "toya_hits": {},
        "pair_hits": {},
        "akito_last_hit_seq": {},
        "toya_last_hit_seq": {},
        "pair_last_hit_seq": {},
        "_seq": 0,
    }


def _new_group_stats(today_str: str) -> dict:
    return {
        "profiles": {},
        "users": {},
        "daily": _new_period_stats(date=today_str),
        "history": _new_period_stats(),
    }


def _new_stats_state() -> dict:
    return {
        "schema_version": 2,
        "cooldowns": {},
        "groups": {},
    }


def _safe_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_counter(raw: object) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, int] = {}
    for key, value in raw.items():
        count = _safe_int(value)
        if count > 0:
            normalized[str(key)] = count
    return normalized


def _normalize_period_stats(raw: object, *, date: str | None = None) -> dict:
    stats = _new_period_stats(date=date)
    if not isinstance(raw, dict):
        return stats

    if date is not None and isinstance(raw.get("date"), str):
        stats["date"] = raw["date"]

    stats["total_draws"] = max(0, _safe_int(raw.get("total_draws")))
    stats["_seq"] = max(0, _safe_int(raw.get("_seq")))
    for key in ("user_draw_counts", "akito_hits", "toya_hits", "egg_user_counts", "akito_last_hit_seq", "toya_last_hit_seq"):
        stats[key] = _normalize_counter(raw.get(key))
    for key in ("foxrabbit_total", "foxbun_total", "fox_total", "rabbit_total"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    return stats


def _normalize_user_stats(raw: object) -> dict:
    stats = _new_user_stats()
    if not isinstance(raw, dict):
        return stats

    for key in ("draw_count", "egg_count", "foxbun_count", "_seq"):
        stats[key] = max(0, _safe_int(raw.get(key)))
    for key in ("akito_hits", "toya_hits", "pair_hits", "akito_last_hit_seq", "toya_last_hit_seq", "pair_last_hit_seq"):
        stats[key] = _normalize_counter(raw.get(key))
    return stats


def _normalize_group_stats(raw: object, today_str: str) -> dict:
    stats = _new_group_stats(today_str)
    if not isinstance(raw, dict):
        return stats

    if isinstance(raw.get("profiles"), dict):
        stats["profiles"] = {
            str(user_id): str(display_name)
            for user_id, display_name in raw["profiles"].items()
            if str(display_name).strip()
        }
    if isinstance(raw.get("users"), dict):
        stats["users"] = {
            str(user_id): _normalize_user_stats(user_stats)
            for user_id, user_stats in raw["users"].items()
        }
    stats["daily"] = _normalize_period_stats(raw.get("daily"), date=today_str)
    stats["history"] = _normalize_period_stats(raw.get("history"))
    return stats


def _load_stats() -> dict:
    path = _stats_path()
    if not path.exists():
        return _new_stats_state()

    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.warning(f"读取 {STATS_FILE} 失败，已重置派生统计数据")
        return _new_stats_state()

    today_str = datetime.now(TZ_CN).date().isoformat()
    stats = _new_stats_state()
    if isinstance(raw, dict):
        stats["schema_version"] = max(2, _safe_int(raw.get("schema_version"), 2) or 2)

        raw_cooldowns = raw.get("cooldowns")
        if isinstance(raw_cooldowns, dict):
            cooldowns: dict[str, list[float]] = {}
            for user_id, history in raw_cooldowns.items():
                if not isinstance(history, list):
                    continue
                valid_history = []
                for ts in history:
                    try:
                        valid_history.append(float(ts))
                    except (TypeError, ValueError):
                        continue
                cooldowns[str(user_id)] = valid_history
            stats["cooldowns"] = cooldowns

        raw_groups = raw.get("groups")
        if isinstance(raw_groups, dict):
            stats["groups"] = {
                str(group_id): _normalize_group_stats(group_stats, today_str)
                for group_id, group_stats in raw_groups.items()
            }

    return stats


def _save_stats() -> None:
    path = _stats_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(PARO_STATS, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _append_egg_log(entry: dict) -> None:
    path = _egg_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


PARO_STATS: dict = _load_stats()


def reload_paro_data() -> None:
    """热重载派生池与排行榜数据（原地 clear+update，保持其他模块持有的引用不失效）。"""
    PARO_DATA.clear()
    PARO_DATA.update(load_json_file(DATA_FILE, DEFAULT_DATA))
    PARO_STATS.clear()
    PARO_STATS.update(_load_stats())
    logger.info("🔄 派生池与排行榜数据已热重载")


# ==================== 图片渲染 ====================

def _load_font(size: int):
    return load_msyhbd_font(size)


def _render_pool_image(title: str, pool: list) -> bytes:
    font_title = _load_font(28)
    font_item = _load_font(24)
    font_footer = _load_font(18)

    row_height = 38
    top_pad = 30
    title_gap = 22
    footer_gap = 16
    bottom_pad = 24

    n = len(pool)
    height = top_pad + 32 + title_gap + n * row_height + footer_gap + 22 + bottom_pad
    width = 600

    img = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(img)

    # 标题
    draw.text((width // 2, top_pad), title, font=font_title, fill="#000000", anchor="ma")

    # 分隔线
    y = top_pad + 32 + title_gap
    draw.line([(40, y), (width - 40, y)], fill="#cccccc", width=1)

    # 列表项
    for i, name in enumerate(pool, 1):
        item_y = y + 8 + (i - 1) * row_height
        draw.text((60, item_y), f"{i}.", font=font_item, fill="#333333")
        draw.text((100, item_y), name, font=font_item, fill="#000000")

    # 底部统计
    footer_y = y + 8 + n * row_height + footer_gap
    footer_text = f"共 {n} 个派生"
    draw.text((width // 2, footer_y), footer_text, font=font_footer, fill="#999999", anchor="ma")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ==================== 头像拼合 ====================

AVATAR_BASE = IMAGE_BASE_PATH / "paro_avatars"


def _find_avatar(character: str, name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        p = AVATAR_BASE / character / f"{name}{ext}"
        if p.exists():
            return p
    return None


FOXRABBIT_DIR = AVATAR_BASE / "fox&rabbit"


def _load_foxrabbit_image(kind: str) -> Image.Image | None:
    """加载狐/兔图片，缩放到 150×150。"""
    for ext in (".png", ".jpg", ".jpeg"):
        p = FOXRABBIT_DIR / f"{kind}{ext}"
        if p.exists():
            im = Image.open(p).convert("RGB")
            return im.resize((150, 150), Image.LANCZOS)
    return None


def _load_foxbun_image() -> Image.Image | None:
    """加载狐&兔.png，保持原尺寸不缩放。"""
    for ext in (".png", ".jpg", ".jpeg"):
        p = FOXRABBIT_DIR / f"狐&兔{ext}"
        if p.exists():
            return Image.open(p).convert("RGB")
    return None


FONT_SIZE = 20
FONT_BOLD_SIZE = 24
ROW_H = 32
TEXT_TOP_GAP = 22
TEXT_BOTTOM_PAD = 10
AVATAR_WIDTH = 304
MIN_CANVAS_W = 380


def _measure_line_width(line) -> float:
    """预测量一行的总宽度（不依赖 draw 对象）。"""
    fn = _load_font(FONT_SIZE)
    fb = _load_font(FONT_BOLD_SIZE)
    if isinstance(line, list):
        return sum(
            (fb if bold else fn).getbbox(txt)[2]
            for txt, _, bold in line
        )
    return fn.getbbox(line)[2]


def _canvas_width(text_lines: list, has_avatars: bool) -> int:
    max_w = max(_measure_line_width(line) for line in text_lines)
    target = AVATAR_WIDTH if has_avatars else 0
    return max(MIN_CANVAS_W, int(max_w) + 32, target)


def _draw_segmented_line(draw, y: int, segments: list, canvas_w: int):
    font_normal = _load_font(FONT_SIZE)
    font_bold = _load_font(FONT_BOLD_SIZE)
    total_w = 0.0
    for txt, _, bold in segments:
        f = font_bold if bold else font_normal
        total_w += draw.textlength(txt, font=f)
    x = (canvas_w - total_w) // 2
    for txt, color, bold in segments:
        f = font_bold if bold else font_normal
        y_off = (FONT_SIZE - (FONT_BOLD_SIZE if bold else FONT_SIZE)) // 2
        draw.text((x, y + y_off), txt, font=f, fill=color, anchor="la")
        x += draw.textlength(txt, font=f)


def _render_text_only(text_lines: list) -> bytes:
    line_count = len(text_lines)
    w = _canvas_width(text_lines, has_avatars=False)
    height = TEXT_TOP_GAP + line_count * ROW_H + TEXT_BOTTOM_PAD
    canvas = Image.new("RGB", (w, height), color="#ffffff")
    font = _load_font(FONT_SIZE)
    draw = ImageDraw.Draw(canvas)
    for i, line in enumerate(text_lines):
        y = TEXT_TOP_GAP + i * ROW_H
        if isinstance(line, list):
            _draw_segmented_line(draw, y, line, w)
        else:
            draw.text((w // 2, y), line, font=font, fill="#000000", anchor="ma")
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _render_composite(akito_name: str, toya_name: str, text_lines: list) -> bytes:
    avatar_size = 150
    gap = 4
    top_pad = 10
    line_count = len(text_lines)
    w = _canvas_width(text_lines, has_avatars=True)
    text_area = TEXT_TOP_GAP + line_count * ROW_H + TEXT_BOTTOM_PAD
    height = top_pad + avatar_size + text_area

    canvas = Image.new("RGB", (w, height), color="#ffffff")

    def _paste_avatar(character: str, name: str, x_offset: int):
        path = _find_avatar(character, name)
        if path:
            img = Image.open(path).convert("RGB")
            img = img.resize((avatar_size, avatar_size), Image.LANCZOS)
            canvas.paste(img, (x_offset, top_pad))

    avatars_width = avatar_size * 2 + gap
    avatars_x = (w - avatars_width) // 2
    _paste_avatar("彰人", akito_name, avatars_x)
    _paste_avatar("冬弥", toya_name, avatars_x + avatar_size + gap)

    draw = ImageDraw.Draw(canvas)
    font = _load_font(FONT_SIZE)
    for i, line in enumerate(text_lines):
        y = top_pad + avatar_size + TEXT_TOP_GAP + i * ROW_H
        if isinstance(line, list):
            _draw_segmented_line(draw, y, line, w)
        else:
            draw.text((w // 2, y), line, font=font, fill="#000000", anchor="ma")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


# ==================== 做饭彩蛋 ====================

_EASTER_EGG_RATE = 0.03
_FOXRABBIT_RATE = 0.02  # 每种 2%


# ==================== 模糊匹配 ====================

def _fuzzy_match(name: str, pool: list) -> str | list | None:
    """在池子中模糊匹配，返回匹配到的原始条目名。
    - str:  唯一匹配
    - list: 多个匹配（歧义），返回候选列表供调用方提示
    - None: 无匹配
    """
    name_lower = name.lower()

    exact = [e for e in pool if e.lower() == name_lower]
    if exact:
        return exact[0]

    prefix = [e for e in pool if e.lower().startswith(name_lower)]
    if prefix:
        return prefix[0] if len(prefix) == 1 else prefix

    contains = [e for e in pool if name_lower in e.lower()]
    if len(contains) == 1:
        return contains[0]
    if len(contains) > 1:
        return contains

    return None


def _parse_draw_request(raw_text: str) -> tuple[int, str]:
    """Parse the draw count and any directional request from user input."""
    count = 1
    directional = ""
    raw = raw_text.strip()
    if not raw:
        return count, directional

    tokens = raw.split()
    for i, token in enumerate(tokens):
        if token.isdigit() and 1 <= int(token) <= 3:
            return int(token), " ".join(tokens[:i] + tokens[i + 1 :])
    return count, raw


def _resolve_directional_draw(
    directional: str,
    akito_pool: list[str],
    toya_pool: list[str],
) -> tuple[str | None, str | None, str | None]:
    """Resolve a fixed Akito/Toya draw request into chosen entries or an error message."""
    if not directional:
        return None, None, None

    directional_lower = directional.lower()
    if directional_lower.startswith("彰人"):
        name = directional[2:].strip()
        if not name:
            return None, None, "请指定彰人的派生名称，例如：抽派生 彰人 黑百合"
        match = _fuzzy_match(name, akito_pool)
        if not match:
            return None, None, f"彰人的派生池里没有与「{name}」匹配的条目。"
        if isinstance(match, list):
            return None, None, f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。"
        return match, None, None

    if directional_lower.startswith("冬弥"):
        name = directional[2:].strip()
        if not name:
            return None, None, "请指定冬弥的派生名称，例如：抽派生 冬弥 王子冬"
        match = _fuzzy_match(name, toya_pool)
        if not match:
            return None, None, f"冬弥的派生池里没有与「{name}」匹配的条目。"
        if isinstance(match, list):
            return None, None, f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。"
        return None, match, None

    return None, None, "请指定要固定哪一方的派生，例如：抽派生 彰人 黑百合。\n彰冬不拆不逆，一方派生固定则另一方派生随机。"


def _prune_draw_history(history: list[float], now_ts: float, window: int) -> list[float]:
    """Drop expired draw timestamps within the rolling window."""
    return [ts for ts in history if now_ts - ts < window]


def _build_draw_limit_message(
    *,
    remaining_before: int,
    requested_count: int,
    history: list[float],
    now_ts: float,
    draw_limit: int,
    draw_window: int,
) -> str | None:
    """Return the cooldown message when the request exceeds the current allowance."""
    if remaining_before >= requested_count:
        return None
    if remaining_before <= 0:
        oldest = min(history)
        wait = int(draw_window - (now_ts - oldest))
        mins, secs = wait // 60, wait % 60
        return f"30分钟内最多抽{draw_limit}次，你已用完次数，请在 {mins} 分 {secs} 秒后再试。"
    return f"30分钟内仅剩 {remaining_before} 次，无法抽 {requested_count} 次。"


def _today_str() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def _cooldown_store() -> dict[str, list[float]]:
    cooldowns = PARO_STATS.setdefault("cooldowns", {})
    if not isinstance(cooldowns, dict):
        cooldowns = {}
        PARO_STATS["cooldowns"] = cooldowns
    return cooldowns


def _bump_counter(counter: dict[str, int], key: str, amount: int = 1) -> None:
    if amount <= 0:
        return
    counter[key] = counter.get(key, 0) + amount


_PAIR_KEY_SEPARATOR = "|||"


def _make_pair_key(akito_name: str, toya_name: str) -> str:
    return f"{akito_name}{_PAIR_KEY_SEPARATOR}{toya_name}"


def _split_pair_key(pair_key: str) -> tuple[str, str]:
    akito_name, separator, toya_name = pair_key.partition(_PAIR_KEY_SEPARATOR)
    if not separator:
        return pair_key, ""
    return akito_name, toya_name


def _record_period_hit(period_stats: dict, counter_key: str, order_key: str, name: str) -> None:
    period_stats["_seq"] += 1
    _bump_counter(period_stats[counter_key], name)
    period_stats[order_key][name] = period_stats["_seq"]


def _record_user_hit(user_stats: dict, counter_key: str, order_key: str, name: str) -> None:
    user_stats["_seq"] += 1
    _bump_counter(user_stats[counter_key], name)
    user_stats[order_key][name] = user_stats["_seq"]


def _record_user_draw_stats(
    user_stats: dict,
    *,
    results: list[tuple[str, str, bool, str | None]],
) -> None:
    user_stats["draw_count"] += len(results)

    for akito_name, toya_name, is_egg, fox_type in results:
        if is_egg or fox_type == "foxbun":
            user_stats["egg_count"] += 1
        if fox_type == "foxbun":
            user_stats["foxbun_count"] += 1
        if fox_type is not None:
            continue

        pair_key = _make_pair_key(akito_name, toya_name)
        _record_user_hit(user_stats, "pair_hits", "pair_last_hit_seq", pair_key)
        _record_user_hit(user_stats, "akito_hits", "akito_last_hit_seq", akito_name)
        _record_user_hit(user_stats, "toya_hits", "toya_last_hit_seq", toya_name)


def _get_fixed_side(fixed_a: str | None, fixed_b: str | None) -> str | None:
    if fixed_a:
        return "akito"
    if fixed_b:
        return "toya"
    return None


def _roll_daily_stats(group_stats: dict, today_str: str) -> bool:
    daily = group_stats.get("daily")
    if isinstance(daily, dict) and daily.get("date") == today_str:
        group_stats["daily"] = _normalize_period_stats(daily, date=today_str)
        return False

    group_stats["daily"] = _new_period_stats(date=today_str)
    return True


def _get_or_create_group_stats(group_id: str, today_str: str) -> tuple[dict, bool]:
    groups = PARO_STATS.setdefault("groups", {})
    group_stats = groups.get(group_id)
    if not isinstance(group_stats, dict):
        group_stats = _new_group_stats(today_str)
        groups[group_id] = group_stats
        return group_stats, True

    normalized = _normalize_group_stats(group_stats, today_str)
    groups[group_id] = normalized
    rolled = _roll_daily_stats(normalized, today_str)
    return normalized, rolled


def _record_draw_stats_for_period(
    period_stats: dict,
    *,
    user_id: str,
    results: list[tuple[str, str, bool, str | None]],
    fixed_side: str | None,
) -> None:
    draw_count = len(results)
    period_stats["total_draws"] += draw_count
    _bump_counter(period_stats["user_draw_counts"], user_id, draw_count)

    for akito_name, toya_name, is_egg, fox_type in results:
        if fox_type is None:
            if fixed_side != "akito":
                _record_period_hit(period_stats, "akito_hits", "akito_last_hit_seq", akito_name)
            if fixed_side != "toya":
                _record_period_hit(period_stats, "toya_hits", "toya_last_hit_seq", toya_name)

        if is_egg or fox_type == "foxbun":
            _bump_counter(period_stats["egg_user_counts"], user_id)

        if fox_type == "foxrabbit":
            period_stats["foxrabbit_total"] += 1
        elif fox_type == "foxbun":
            period_stats["foxbun_total"] += 1
        elif fox_type == "fox":
            period_stats["fox_total"] += 1
        elif fox_type == "rabbit":
            period_stats["rabbit_total"] += 1


def _record_group_draw_stats(
    *,
    group_id: int,
    user_id: str,
    display_name: str,
    results: list[tuple[str, str, bool, str | None]],
    fixed_side: str | None,
    fixed_name: str | None,
    requested_count: int,
    now_ts: float,
) -> None:
    today_str = _today_str()
    group_stats, _rolled = _get_or_create_group_stats(str(group_id), today_str)
    group_stats["profiles"][user_id] = display_name
    user_stats = _normalize_user_stats(group_stats["users"].get(user_id))
    group_stats["users"][user_id] = user_stats

    _record_draw_stats_for_period(
        group_stats["daily"],
        user_id=user_id,
        results=results,
        fixed_side=fixed_side,
    )
    _record_draw_stats_for_period(
        group_stats["history"],
        user_id=user_id,
        results=results,
        fixed_side=fixed_side,
    )
    _record_user_draw_stats(user_stats, results=results)

    for idx, (akito_name, toya_name, is_egg, fox_type) in enumerate(results, 1):
        if not is_egg and fox_type != "foxbun":
            continue
        _append_egg_log(
            {
                "ts": now_ts,
                "date": today_str,
                "group_id": str(group_id),
                "user_id": user_id,
                "display_name": display_name,
                "egg_type": "cooking" if is_egg else "foxbun",
                "akito": akito_name,
                "toya": toya_name,
                "draw_index": idx,
                "requested_count": requested_count,
                "fixed_side": fixed_side,
                "fixed_name": fixed_name,
            }
        )

    _save_stats()


def _sorted_counter_items(counter: dict[str, int]) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda item: (-item[1], item[0]))


def _build_user_rows(counter: dict[str, int], profiles: dict[str, str], *, limit: int = 5) -> list[dict]:
    rows = []
    for index, (user_id, count) in enumerate(_sorted_counter_items(counter)[:limit], 1):
        display_name = profiles.get(user_id) or f"用户{user_id}"
        rows.append({"left": f"{index}. {display_name}", "right": f"{count}次"})
    if not rows:
        rows.append({"left": "暂无", "right": "0次"})
    return rows


def _sorted_ranked_items(counter: dict[str, int], last_hit_seq: dict[str, int] | None = None) -> list[tuple[str, int]]:
    if last_hit_seq is None:
        last_hit_seq = {}
    return sorted(
        counter.items(),
        key=lambda item: (-item[1], last_hit_seq.get(item[0], 10**9), item[0]),
    )


def _build_character_rows(
    counter: dict[str, int],
    *,
    limit: int = 3,
    character: str,
    last_hit_seq: dict[str, int] | None = None,
) -> list[dict]:
    grouped: list[tuple[list[str], int]] = []
    for name, count in _sorted_ranked_items(counter, last_hit_seq):
        if grouped and grouped[-1][1] == count:
            grouped[-1][0].append(name)
        else:
            grouped.append(([name], count))

    rows = []
    for rank, (names, count) in enumerate(grouped[:limit], 1):
        visible_names = names[:3]
        display_text = " / ".join(visible_names)
        if len(names) > 3:
            display_text += " / ..."
        rows.append(
            {
                "left": f"TOP{rank} {display_text}",
                "right": f"{count}次",
                "suffix_avatar_names": visible_names,
                "suffix_character": character,
            }
        )
    if not rows:
        rows.append({"left": "暂无", "right": "0次"})
    return rows


def _build_fox_rows(period_stats: dict) -> list[dict]:
    entries = [
        ("foxrabbit", "狐兔", period_stats["foxrabbit_total"]),
        ("foxbun", "狐兔饭", period_stats["foxbun_total"]),
        ("fox", "狐狸", period_stats["fox_total"]),
        ("rabbit", "兔子", period_stats["rabbit_total"]),
    ]
    rows = []
    for _idx, (fox_type, label, count) in sorted(
        enumerate(entries),
        key=lambda item: (-item[1][2], item[0]),
    ):
        rows.append({"left": label, "right": f"{count}次", "icon_kind": "fox", "fox_type": fox_type})
    return rows


# ==================== 多抽渲染 ====================

SEQS = ["①", "②", "③"]


def _render_multi(results: list, remaining: int, nickname: str) -> bytes:
    """results: [(a, b, is_cooking, fox_type), ...]"""
    count = len(results)
    cooking_indices = [i for i, (_, _, egg, _) in enumerate(results) if egg]
    foxbun_idx = next((i for i, (_, _, _, ft) in enumerate(results) if ft == "foxbun"), None)

    IMG_SZ = 150  # 狐兔图片尺寸
    avatar_size = 150
    gap = 4
    avatars_width = avatar_size * 2 + gap
    result_gap = 10

    fn = _load_font(FONT_SIZE)       # 20px
    fb = _load_font(FONT_BOLD_SIZE)  # 24px — 彩蛋汇总

    FR_TEXTS = {
        "fox": "一只得意的狐狸赶走了这里的派生。",
        "rabbit": "一只圆圆的兔子挡住了这里的派生。",
        "foxrabbit": "一对眼熟的狐兔出现在了这里……",
        "foxbun": "发现了一对正在贴贴的狐兔！",
    }

    # --- 逐行计算宽度 ---
    emoji_w = fn.getbbox(" ★")[2]

    PREFIX = "你抽到了："

    def _row_width(idx):
        _, _, is_egg, fox_type = results[idx]
        if fox_type:
            txt_w = int(fn.getbbox(SEQS[idx] + FR_TEXTS[fox_type])[2])
            if fox_type == "foxbun":
                bun = _load_foxbun_image()
                return max(txt_w, bun.size[0]) if bun else txt_w
            return txt_w
        a, b = results[idx][0], results[idx][1]
        a_w = int(fn.getbbox(a)[2])
        b_w = int(fn.getbbox(b)[2])
        x_w = int(fn.getbbox("×")[2])
        return int(fn.getbbox(PREFIX)[2]) + a_w + x_w + b_w + (emoji_w if is_egg else 0)

    max_line_w = max(_row_width(i) for i in range(count))

    # 做饭彩蛋汇总行宽
    egg_summary_w = 0.0
    if cooking_indices or foxbun_idx is not None:
        if cooking_indices:
            parts_w = 0.0
            for idx in cooking_indices:
                ea, eb = results[idx][0], results[idx][1]
                parts_w += fb.getbbox(ea)[2] + fb.getbbox("×")[2] + fb.getbbox(eb)[2]
            sep_count = len(cooking_indices) - 1 + (1 if foxbun_idx is not None else 0)
            if foxbun_idx is not None:
                parts_w += fb.getbbox("狐")[2] + fb.getbbox("兔")[2]
            egg_line_w = (fb.getbbox("快来做")[2] + parts_w
                          + fb.getbbox("、")[2] * sep_count
                          + fb.getbbox("的饭吧！")[2])
            egg_summary_w = max(fb.getbbox("恭喜你是被选中的彰冬姐！")[2], egg_line_w)
        else:
            egg_summary_w = fb.getbbox("快来做狐兔饭吧！")[2]

    rem_w = fn.getbbox(f"（30分钟内剩余 {remaining} 次）")[2]

    w = max(MIN_CANVAS_W, int(max_line_w) + 48,
            int(egg_summary_w) + 48 if egg_summary_w else 0,
            int(rem_w) + 48)

    # --- 高度：逐行累加 ---
    title_h = ROW_H + 8
    height = TEXT_TOP_GAP + title_h
    for i in range(count):
        _, _, _, fox_type = results[i]
        if fox_type:
            if fox_type == "foxbun":
                bun = _load_foxbun_image()
                h = bun.size[1] + 8 if bun else IMG_SZ + 8
            else:
                h = IMG_SZ + 8
            height += h + ROW_H + result_gap  # 狐兔行：图片 + 文字
        else:
            has_av = _find_avatar("彰人", results[i][0]) and _find_avatar("冬弥", results[i][1])
            if has_av:
                height += avatar_size + 8 + ROW_H + result_gap  # 头像 + 文字
            else:
                height += ROW_H + result_gap  # 纯文字
    has_cook = bool(cooking_indices)
    has_foxbun = foxbun_idx is not None
    egg_area_h = 12 + (2 if has_cook else 1) * ROW_H if (has_cook or has_foxbun) else 0
    # 有做饭时 2 行（恭喜+快来做），仅狐×兔时 1 行
    height += egg_area_h + 8 + ROW_H + TEXT_BOTTOM_PAD

    canvas = Image.new("RGB", (w, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    # --- 标题 ---
    y = TEXT_TOP_GAP
    draw.text((w // 2, y), f"本次共计抽取了{count}个派生", font=fn, fill="#000000", anchor="ma")
    y += title_h

    # --- 每行结果 ---
    for i in range(count):
        a, b, is_egg, fox_type = results[i]
        seq = SEQS[i]
        seq_w = int(fn.getbbox(seq + " ")[2])

        if fox_type:
            # 狐兔行：图片居中 + 文字在下
            if fox_type == "foxbun":
                bun_im = _load_foxbun_image()
                if bun_im:
                    bw, bh = bun_im.size
                    canvas.paste(bun_im, ((w - bw) // 2, y))
                    y += bh + 8
            elif fox_type == "foxrabbit":
                fox_im = _load_foxrabbit_image("狐")
                rab_im = _load_foxrabbit_image("兔")
                if fox_im and rab_im:
                    imgs_w = IMG_SZ * 2 + gap
                    fx = (w - imgs_w) // 2
                    canvas.paste(fox_im, (fx, y))
                    canvas.paste(rab_im, (fx + IMG_SZ + gap, y))
                y += IMG_SZ + 8
            else:
                single_im = _load_foxrabbit_image("狐" if fox_type == "fox" else "兔")
                if single_im:
                    canvas.paste(single_im, ((w - IMG_SZ) // 2, y))
                y += IMG_SZ + 8
            # 狐兔文字：狐橙兔蓝（单抽不带序号）
            pre = seq + " " if count > 1 else ""
            if fox_type == "foxrabbit":
                segs = [(pre, "#000000", False), ("一对眼熟的", "#000000", False), ("狐", "#FF7722", False), ("兔", "#0077DD", False), ("出现在了这里……", "#000000", False)]
            elif fox_type == "foxbun":
                segs = [(pre, "#000000", False), ("发现了一对正在贴贴的", "#000000", False), ("狐", "#FF7722", False), ("兔", "#0077DD", False), ("！", "#000000", False)]
            elif fox_type == "fox":
                segs = [(pre, "#000000", False), ("一只得意的", "#000000", False), ("狐狸", "#FF7722", False), ("赶走了这里的派生。", "#000000", False)]
            else:
                segs = [(pre, "#000000", False), ("一只圆圆的", "#000000", False), ("兔子", "#0077DD", False), ("挡住了这里的派生。", "#000000", False)]
            _draw_segmented_line(draw, y, segs, w)
            y += ROW_H + result_gap
        else:
            has_av = _find_avatar("彰人", a) and _find_avatar("冬弥", b)
            if has_av:
                avatars_x = (w - avatars_width) // 2
                for ch, name, x_off in [("彰人", a, avatars_x), ("冬弥", b, avatars_x + avatar_size + gap)]:
                    path = _find_avatar(ch, name)
                    if path:
                        im = Image.open(path).convert("RGB").resize((avatar_size, avatar_size), Image.LANCZOS)
                        canvas.paste(im, (x_off, y))
                y += avatar_size + 8

            a_w = int(fn.getbbox(a)[2])
            x_w_val = int(fn.getbbox("×")[2])
            b_w = int(fn.getbbox(b)[2])
            pre_w = int(fn.getbbox(PREFIX)[2])
            total_w = seq_w + pre_w + a_w + x_w_val + b_w + (emoji_w if is_egg else 0)
            x = (w - total_w) // 2
            draw.text((x, y), seq + " ", font=fn, fill="#000000", anchor="la"); x += seq_w
            draw.text((x, y), PREFIX, font=fn, fill="#000000", anchor="la"); x += pre_w
            draw.text((x, y), a, font=fn, fill="#FF7722", anchor="la"); x += a_w
            draw.text((x, y), "×", font=fn, fill="#000000", anchor="la"); x += x_w_val
            draw.text((x, y), b, font=fn, fill="#0077DD", anchor="la")
            if is_egg:
                x += b_w
                draw.text((x, y), " ★", font=fn, fill="#000000", anchor="la")
            y += ROW_H + result_gap

    # --- 做饭彩蛋汇总 & 狐×兔联动（24px）---
    if cooking_indices or foxbun_idx is not None:
        y += 12
        if cooking_indices:
            draw.text((w // 2, y), "恭喜你是被选中的彰冬姐！", font=fb, fill="#000000", anchor="ma")
            y += ROW_H
            parts = [("快来做", "#000000")]
            for j, idx in enumerate(cooking_indices):
                if j > 0:
                    parts.append(("、", "#000000"))
                ea, eb = results[idx][0], results[idx][1]
                parts.append((ea, "#FF7722"))
                parts.append(("×", "#000000"))
                parts.append((eb, "#0077DD"))
            if foxbun_idx is not None:
                parts.append(("、", "#000000"))
                parts.append(("狐", "#FF7722"))
                parts.append(("兔", "#0077DD"))
            parts.append(("的饭吧！", "#000000"))
            total_w2 = sum(fb.getbbox(t)[2] for t, _ in parts)
            x2 = (w - total_w2) // 2
            for txt, clr in parts:
                w_t = int(fb.getbbox(txt)[2])
                draw.text((x2, y), txt, font=fb, fill=clr, anchor="la")
                x2 += w_t
            y += ROW_H
        else:
            # 狐×兔单独触发（无做饭）单行：快来做狐兔饭吧！
            parts = [("快来做", "#000000"), ("狐", "#FF7722"), ("兔", "#0077DD"), ("饭吧！", "#000000")]
            total_w2 = sum(fb.getbbox(t)[2] for t, _ in parts)
            x2 = (w - total_w2) // 2
            for txt, clr in parts:
                w_t = int(fb.getbbox(txt)[2])
                draw.text((x2, y), txt, font=fb, fill=clr, anchor="la")
                x2 += w_t
            y += ROW_H

    # --- 剩余次数 ---
    y += 8
    draw.text((w // 2, y), f"（30分钟内剩余 {remaining} 次）", font=fn, fill="#999999", anchor="ma")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _resize_to_fit(image: Image.Image, *, max_w: int, max_h: int) -> Image.Image:
    width, height = image.size
    if width <= max_w and height <= max_h:
        return image.copy()
    ratio = min(max_w / width, max_h / height)
    size = (max(1, int(width * ratio)), max(1, int(height * ratio)))
    return image.resize(size, Image.LANCZOS)


def _load_avatar_thumb(character: str, name: str, size: int = 56) -> Image.Image | None:
    path = _find_avatar(character, name)
    if not path:
        return None
    image = Image.open(path).convert("RGB")
    return image.resize((size, size), Image.LANCZOS)


def _load_fox_stat_icon(fox_type: str) -> Image.Image | None:
    if fox_type == "fox":
        image = _load_foxrabbit_image("狐")
        return _resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "rabbit":
        image = _load_foxrabbit_image("兔")
        return _resize_to_fit(image, max_w=56, max_h=56) if image else None
    if fox_type == "foxbun":
        image = _load_foxbun_image()
        return _resize_to_fit(image, max_w=96, max_h=56) if image else None
    if fox_type == "foxrabbit":
        fox = _load_foxrabbit_image("狐")
        rabbit = _load_foxrabbit_image("兔")
        if not fox or not rabbit:
            return None
        fox = _resize_to_fit(fox, max_w=56, max_h=56)
        rabbit = _resize_to_fit(rabbit, max_w=56, max_h=56)
        canvas = Image.new("RGB", (fox.width + rabbit.width + 6, max(fox.height, rabbit.height)), "#ffffff")
        canvas.paste(fox, (0, (canvas.height - fox.height) // 2))
        canvas.paste(rabbit, (fox.width + 6, (canvas.height - rabbit.height) // 2))
        return canvas
    return None


def _text_width(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str, fallback_size: int = FONT_SIZE) -> int:
    try:
        bbox = font.getbbox(text)
        width = bbox[2]
        if isinstance(width, (int, float)):
            return int(width)
    except Exception:
        pass
    return max(len(text), 1) * fallback_size


def _text_height(font: ImageFont.FreeTypeFont | ImageFont.ImageFont, text: str = "Hg", fallback_size: int = FONT_SIZE) -> int:
    try:
        bbox = font.getbbox(text)
        height = bbox[3] - bbox[1]
        if isinstance(height, (int, float)) and height > 0:
            return int(height)
    except Exception:
        pass
    return fallback_size


def _truncate_text(text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if _text_width(font, text) <= max_width:
        return text

    suffix = "..."
    trimmed = text
    while trimmed and _text_width(font, trimmed + suffix) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed + suffix) if trimmed else suffix


AKITO_ACCENT = "#FF7722"
TOYA_ACCENT = "#0077DD"
SECTION_BAR_BG = "#8c9198"


def _draw_section_label(
    draw: ImageDraw.ImageDraw,
    *,
    left: int,
    right: int,
    y: int,
    text: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    fill: str = "#333333",
    bg_fill: str | None = None,
    height: int = 34,
) -> int:
    if bg_fill:
        draw.rectangle([(left, y), (right, y + height)], fill=bg_fill)
        draw.text((left + 12, y + height // 2), text, font=font, fill=fill, anchor="lm")
        return height

    draw.text((left, y + height // 2), text, font=font, fill=fill, anchor="lm")
    return height


def _resolve_row_icon(row: dict) -> Image.Image | None:
    if row.get("icon_kind") == "fox":
        fox_type = row.get("fox_type")
        if isinstance(fox_type, str):
            return _load_fox_stat_icon(fox_type)
    return None


def _resolve_row_suffix_icons(row: dict) -> list[Image.Image]:
    names = row.get("suffix_avatar_names")
    character = row.get("suffix_character")
    if not isinstance(names, list) or not character:
        return []

    icons = []
    for name in names:
        if not isinstance(name, str):
            continue
        icon = _load_avatar_thumb(character, name, size=40)
        if icon:
            icons.append(icon)
    return icons


def _prepare_display_rows(rows: list[dict], *, min_row_height: int = 44) -> list[tuple[dict, Image.Image | None, list[Image.Image], int]]:
    prepared_rows = []
    for row in rows:
        prefix_icon = _resolve_row_icon(row)
        suffix_icons = _resolve_row_suffix_icons(row)
        icon_heights = []
        if prefix_icon:
            icon_heights.append(prefix_icon.height)
        if suffix_icons:
            icon_heights.extend(icon.height for icon in suffix_icons)
        row_height = max(min_row_height, max(icon_heights, default=0) + 12)
        prepared_rows.append((row, prefix_icon, suffix_icons, row_height))
    return prepared_rows


def _build_placeholder_avatar(label: str, *, size: int, bg_color: str) -> Image.Image:
    canvas = Image.new("RGB", (size, size), color=bg_color)
    draw = ImageDraw.Draw(canvas)
    font = _load_font(max(18, size // 2))
    draw.rectangle([(0, 0), (size - 1, size - 1)], outline="#dddddd", width=1)
    draw.text((size // 2, size // 2), label, font=font, fill="#ffffff", anchor="mm")
    return canvas


def _build_pair_thumb(akito_name: str, toya_name: str, *, size: int = 52, gap: int = 4) -> Image.Image:
    akito_thumb = _load_avatar_thumb("彰人", akito_name, size=size) or _build_placeholder_avatar(
        "彰", size=size, bg_color="#f08a5d"
    )
    toya_thumb = _load_avatar_thumb("冬弥", toya_name, size=size) or _build_placeholder_avatar(
        "冬", size=size, bg_color="#5d8df0"
    )
    canvas = Image.new("RGB", (size * 2 + gap, size), color="#ffffff")
    canvas.paste(akito_thumb, (0, 0))
    canvas.paste(toya_thumb, (size + gap, 0))
    return canvas


def _build_personal_pair_items(user_stats: dict) -> list[dict]:
    items = []
    for pair_key, count in _sorted_ranked_items(user_stats["pair_hits"], user_stats.get("pair_last_hit_seq")):
        akito_name, toya_name = _split_pair_key(pair_key)
        items.append(
            {
                "pair_key": pair_key,
                "akito_name": akito_name,
                "toya_name": toya_name,
                "count": count,
            }
        )
    return items


def _new_user_egg_history() -> dict:
    return {
        "cooking_count": 0,
        "foxbun_count": 0,
        "cooking_pair_hits": {},
        "cooking_pair_last_hit_seq": {},
        "_seq": 0,
    }


def _record_user_egg_history_entry(
    egg_history: dict,
    *,
    akito_name: str,
    toya_name: str,
    egg_type: str,
) -> None:
    if egg_type == "cooking":
        egg_history["cooking_count"] += 1
        pair_key = _make_pair_key(akito_name, toya_name)
        egg_history["_seq"] += 1
        _bump_counter(egg_history["cooking_pair_hits"], pair_key)
        egg_history["cooking_pair_last_hit_seq"][pair_key] = egg_history["_seq"]
        return
    if egg_type == "foxbun":
        egg_history["foxbun_count"] += 1


def _collect_user_egg_history(group_id: int, user_id: str) -> dict:
    egg_history = _new_user_egg_history()
    path = _egg_log_path()
    if not path.exists():
        return egg_history

    target_group_id = str(group_id)
    target_user_id = str(user_id)
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if str(entry.get("group_id")) != target_group_id or str(entry.get("user_id")) != target_user_id:
                    continue
                egg_type = str(entry.get("egg_type") or "")
                if egg_type not in {"cooking", "foxbun"}:
                    continue
                _record_user_egg_history_entry(
                    egg_history,
                    akito_name=str(entry.get("akito") or ""),
                    toya_name=str(entry.get("toya") or ""),
                    egg_type=egg_type,
                )
    except Exception:
        logger.warning(f"读取 {EGG_LOG_FILE} 失败，无法构建个人做饭彩蛋历史")
    return egg_history


def _build_personal_cooking_pair_items(egg_history: dict) -> list[dict]:
    items = []
    for pair_key, count in _sorted_ranked_items(
        egg_history["cooking_pair_hits"],
        egg_history.get("cooking_pair_last_hit_seq"),
    ):
        akito_name, toya_name = _split_pair_key(pair_key)
        items.append(
            {
                "pair_key": pair_key,
                "akito_name": akito_name,
                "toya_name": toya_name,
                "count": count,
            }
        )
    return items


def _count_total_cooking_hits(egg_history: dict) -> int:
    return int(egg_history.get("cooking_count", 0)) + int(egg_history.get("foxbun_count", 0))


_HTML_PAGE_CACHE: dict[str, tuple[str, bytes]] = {}


def _run_async_render_sync(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("不能在运行中的事件循环里同步执行 HTML 渲染")


def _path_to_uri(path: Path | None) -> str:
    if not path:
        return ""
    try:
        return path.resolve().as_uri()
    except Exception:
        return ""


def _find_foxrabbit_asset(name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        candidate = FOXRABBIT_DIR / f"{name}{ext}"
        if candidate.exists():
            return candidate
    return None


def _avatar_uri(character: str, name: str) -> str:
    return _path_to_uri(_find_avatar(character, name))


def _fox_icon_uris(fox_type: str) -> list[str]:
    if fox_type == "fox":
        return [_path_to_uri(_find_foxrabbit_asset("狐"))] if _find_foxrabbit_asset("狐") else []
    if fox_type == "rabbit":
        return [_path_to_uri(_find_foxrabbit_asset("兔"))] if _find_foxrabbit_asset("兔") else []
    if fox_type == "foxrabbit":
        icons = []
        fox_path = _find_foxrabbit_asset("狐")
        rabbit_path = _find_foxrabbit_asset("兔")
        if fox_path:
            icons.append(_path_to_uri(fox_path))
        if rabbit_path:
            icons.append(_path_to_uri(rabbit_path))
        return icons
    if fox_type == "foxbun":
        foxbun_path = _find_foxrabbit_asset("狐&兔")
        return [_path_to_uri(foxbun_path)] if foxbun_path else []
    return []


def _footer_text() -> str:
    return datetime.now(TZ_CN).strftime("%Y-%m-%d %H:%M")


def _subtitle_for_scope(period_stats: dict, scope: str) -> str:
    return f"{period_stats.get('date')} 00:00 起累计" if scope == "daily" else "功能上线后累计"


def _build_user_contract(counter: dict[str, int], profiles: dict[str, str], *, limit: int = 5) -> list[dict]:
    users = []
    for user_id, count in _sorted_counter_items(counter)[:limit]:
        users.append({"name": profiles.get(user_id) or f"用户{user_id}", "count": count})
    if not users:
        users.append({"name": "暂无", "count": 0})
    return users


def _build_character_contract(
    counter: dict[str, int],
    *,
    title: str,
    cls: str,
    last_hit_seq: dict[str, int] | None = None,
    limit: int = 3,
) -> dict:
    items = []
    grouped: list[tuple[list[str], int]] = []
    for name, count in _sorted_ranked_items(counter, last_hit_seq):
        if grouped and grouped[-1][1] == count:
            grouped[-1][0].append(name)
        else:
            grouped.append(([name], count))

    for names, count in grouped[:limit]:
        visible_names = names[:3]
        items.append(
            {
                "names": visible_names,
                "count": count,
                "more": len(names) > 3,
                "icons": [_avatar_uri("彰人" if cls == "akito" else "冬弥", name) for name in visible_names if name != "暂无"],
            }
        )

    if not items:
        items.append({"names": ["暂无"], "count": 0, "more": False, "icons": []})

    return {
        "cls": cls,
        "title": title,
        "en": "AKITO" if cls == "akito" else "TOYA",
        "items": items,
    }


def _build_fox_rows_contract(period_stats: dict) -> list[dict]:
    entries = [
        ("foxrabbit", "狐兔", period_stats["foxrabbit_total"], ["狐", "兔"]),
        ("foxbun", "狐兔饭", period_stats["foxbun_total"], ["狐", "兔"]),
        ("fox", "狐狸", period_stats["fox_total"], ["狐"]),
        ("rabbit", "兔子", period_stats["rabbit_total"], ["兔"]),
    ]
    rows = []
    for _idx, (fox_type, label, count, kinds) in sorted(
        enumerate(entries),
        key=lambda item: (-item[1][2], item[0]),
    ):
        rows.append({"name": label, "kinds": kinds, "count": count, "icons": _fox_icon_uris(fox_type)})
    return rows


def _build_profile_pair_contract(egg_history: dict) -> list[dict]:
    items = []
    for pair_item in _build_personal_cooking_pair_items(egg_history):
        items.append(
            {
                "count": pair_item["count"],
                "akito_img": _avatar_uri("彰人", pair_item["akito_name"]),
                "toya_img": _avatar_uri("冬弥", pair_item["toya_name"]),
                "akito_initial": pair_item["akito_name"][:1] or "彰",
                "toya_initial": pair_item["toya_name"][:1] or "冬",
                "akito_name": pair_item["akito_name"],
                "toya_name": pair_item["toya_name"],
            }
        )
    return items


def _build_paro_rank_page_data_from_stats(group_stats: dict, period_stats: dict, scope: str) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "DAILY DRAW REPORT" if scope == "daily" else "HISTORY DRAW REPORT",
        "title": "每日派生排行榜" if scope == "daily" else "历史派生排行榜",
        "pill": _subtitle_for_scope(period_stats, scope),
        "total": period_stats["total_draws"],
        "users_title": "抽取次数最多的前 5 人",
        "users": _build_user_contract(period_stats["user_draw_counts"], group_stats["profiles"], limit=5),
        "characters": [
            _build_character_contract(
                period_stats["akito_hits"],
                title="被抽到最多次的彰人 TOP 3",
                cls="akito",
                last_hit_seq=period_stats.get("akito_last_hit_seq"),
            ),
            _build_character_contract(
                period_stats["toya_hits"],
                title="被抽到最多次的冬弥 TOP 3",
                cls="toya",
                last_hit_seq=period_stats.get("toya_last_hit_seq"),
            ),
        ],
        "footer_right": "",
    }


def _build_egg_rank_page_data_from_stats(group_stats: dict, period_stats: dict, scope: str) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "DAILY COOKING REPORT" if scope == "daily" else "HISTORY COOKING REPORT",
        "title": "每日做饭排行榜" if scope == "daily" else "历史做饭排行榜",
        "pill": _subtitle_for_scope(period_stats, scope),
        "users_title": "做饭 + 狐兔饭触发最多的前 5 人",
        "users": _build_user_contract(period_stats["egg_user_counts"], group_stats["profiles"], limit=5),
        "eggs": _build_fox_rows_contract(period_stats),
        "footer_right": "",
    }


def _build_personal_paro_page_data_from_user_stats(user_id: str, display_name: str, user_stats: dict, egg_history: dict) -> dict:
    return {
        "theme": "dark",
        "page_width": 680,
        "eyebrow_tail": "PLAYER PROFILE",
        "title": display_name or f"用户{user_id}",
        "pill": "",
        "stats": [
            {"label": "累计抽取派生次数", "value": user_stats["draw_count"]},
            {"label": "累计抽到做饭的次数", "value": _count_total_cooking_hits(egg_history)},
        ],
        "characters": [
            _build_character_contract(
                user_stats["akito_hits"],
                title="抽到最多的彰人派生 TOP 3",
                cls="akito",
                last_hit_seq=user_stats.get("akito_last_hit_seq"),
            ),
            _build_character_contract(
                user_stats["toya_hits"],
                title="抽到最多的冬弥派生 TOP 3",
                cls="toya",
                last_hit_seq=user_stats.get("toya_last_hit_seq"),
            ),
        ],
        "pending_dishes": _build_profile_pair_contract(egg_history),
        "dish_empty_text": "还没有抽到做饭彩蛋",
        "fox_rabbit_count": egg_history["foxbun_count"],
        "fox_rabbit_icons": _fox_icon_uris("foxbun"),
        "footer_right": "",
    }


def _build_draw_result_page_data(
    results: list[tuple[str, str, bool, str | None]],
    remaining: int,
    nickname: str,
) -> dict:
    items = []
    dishes = []
    foxbun_hit = False

    for akito_name, toya_name, is_egg, fox_type in results:
        if fox_type:
            items.append(
                {
                    "type": "fox",
                    "fox_type": fox_type,
                    "imgs": _fox_icon_uris(fox_type),
                }
            )
            foxbun_hit = foxbun_hit or fox_type == "foxbun"
            continue

        items.append(
            {
                "type": "pair",
                "akito": akito_name,
                "toya": toya_name,
                "akito_img": _avatar_uri("彰人", akito_name),
                "toya_img": _avatar_uri("冬弥", toya_name),
                "cooking": is_egg,
            }
        )
        if is_egg:
            dishes.append({"akito": akito_name, "toya": toya_name})

    if dishes and len(results) == 1:
        summary = {
            "mode": "single",
            "nickname": nickname,
            "akito": dishes[0]["akito"],
            "toya": dishes[0]["toya"],
        }
    elif dishes:
        summary = {
            "mode": "multi",
            "dishes": dishes,
            "with_foxbun": foxbun_hit,
        }
    elif foxbun_hit:
        summary = {"mode": "foxbun_only"}
    else:
        summary = None

    return {
        "theme": "light",
        "page_width": 620,
        "eyebrow_tail": "GACHA RESULT",
        "title": "派生抽取结果",
        "pill": f"本次共 {len(results)} 抽",
        "results": items,
        "cooking_summary": summary,
        "quota_text": f"30 分钟内剩余 {remaining} 次",
        "footer_right": "",
    }


async def _render_html_page(
    template_name: str,
    data: dict,
    *,
    cache_key: str | None = None,
    fallback=None,
) -> bytes:
    if cache_key:
        signature = json.dumps(data, ensure_ascii=False, sort_keys=True)
        cached = _HTML_PAGE_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]
        try:
            pic = await render_random_paro_page(template_name, data)
        except Exception:
            logger.exception(f"{template_name} HTML 渲染失败")
            if fallback is not None:
                return fallback()
            raise
        _HTML_PAGE_CACHE[cache_key] = (signature, pic)
        return pic
    try:
        return await render_random_paro_page(template_name, data)
    except Exception:
        logger.exception(f"{template_name} HTML 渲染失败")
        if fallback is not None:
            return fallback()
        raise


def _get_group_stats(group_id: int) -> dict:
    today_str = _today_str()
    group_stats, rolled = _get_or_create_group_stats(str(group_id), today_str)
    if rolled:
        _save_stats()
    return group_stats


def _get_group_period_stats(group_id: int, scope: str) -> tuple[dict, dict]:
    group_stats = _get_group_stats(group_id)
    period_key = "daily" if scope == "daily" else "history"
    return group_stats, group_stats[period_key]


def _render_leaderboard_card(title: str, subtitle: str, sections: list[dict]) -> bytes:
    width = 760
    pad_x = 34
    pad_y = 26
    row_gap = 8
    section_gap = 12

    font_title = _load_font(30)
    font_subtitle = _load_font(18)
    font_section = _load_font(22)
    font_row = _load_font(20)
    font_value = _load_font(20)

    prepared_sections = []
    height = pad_y + 38 + 28
    for section in sections:
        rows = []
        title_gap_after = int(section.get("title_gap_after", 0) or 0)
        height += 34 + title_gap_after
        for row in section["rows"]:
            prefix_icon = _resolve_row_icon(row)
            suffix_icons = _resolve_row_suffix_icons(row)
            icon_heights = []
            if prefix_icon:
                icon_heights.append(prefix_icon.height)
            if suffix_icons:
                icon_heights.extend(icon.height for icon in suffix_icons)
            row_height = max(44, max(icon_heights, default=0) + 12)
            rows.append((row, prefix_icon, suffix_icons, row_height))
            height += row_height + row_gap
        height += section_gap
        prepared_sections.append(
            {
                "title": section["title"],
                "title_fill": section.get("title_fill", "#333333"),
                "title_bg": section.get("title_bg"),
                "title_gap_after": title_gap_after,
                "rows": rows,
            }
        )

    height += pad_y - section_gap
    canvas = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    y = pad_y
    draw.text((width // 2, y), title, font=font_title, fill="#000000", anchor="ma")
    y += 38
    draw.text((width // 2, y), subtitle, font=font_subtitle, fill="#888888", anchor="ma")
    y += 28

    for section in prepared_sections:
        y += _draw_section_label(
            draw,
            left=pad_x,
            right=width - pad_x,
            y=y,
            text=section["title"],
            font=font_section,
            fill=section["title_fill"],
            bg_fill=section["title_bg"],
        )
        y += section.get("title_gap_after", 0)
        rows = section["rows"]
        for row, prefix_icon, suffix_icons, row_height in rows:
            text_x = pad_x
            if prefix_icon:
                icon_y = y + (row_height - prefix_icon.height) // 2
                canvas.paste(prefix_icon, (pad_x, icon_y))
                text_x += prefix_icon.width + 14

            value_text = row.get("right", "")
            value_width = _text_width(font_value, value_text)
            suffix_width = 0
            if suffix_icons:
                suffix_width = sum(icon.width for icon in suffix_icons) + 8 * len(suffix_icons)
            available_width = width - pad_x - value_width - 18 - text_x - suffix_width
            left_text = _truncate_text(row.get("left", ""), font_row, available_width)
            row_center_y = y + row_height // 2
            draw.text((text_x, row_center_y), left_text, font=font_row, fill="#000000", anchor="lm")

            left_text_width = _text_width(font_row, left_text)
            suffix_x = text_x + left_text_width + 8
            for icon in suffix_icons:
                icon_y = y + (row_height - icon.height) // 2
                canvas.paste(icon, (suffix_x, icon_y))
                suffix_x += icon.width + 8

            draw.text((width - pad_x, row_center_y), value_text, font=font_value, fill="#555555", anchor="rm")
            y += row_height + row_gap
        y += section_gap

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _render_personal_paro_card(user_id: str, display_name: str, user_stats: dict, egg_history: dict) -> bytes:
    width = 760
    pad_x = 30
    pad_y = 26
    row_gap = 6
    section_gap = 10
    pair_tile_w = 132
    pair_tile_h = 86
    pair_gap = 10
    pair_columns = 4
    pair_section_top_gap = 8
    fox_section_gap = 24

    font_section = _load_font(21)
    font_row = _load_font(19)
    font_value = _load_font(19)
    font_pair = _load_font(17)
    font_name = _load_font(38)
    name_text = display_name or f"用户{user_id}"
    name_text = _truncate_text(name_text, font_name, width - pad_x * 2)
    name_height = max(42, _text_height(font_name, name_text, fallback_size=38))

    summary_rows = [
        {"left": "累计抽取派生次数", "right": f"{user_stats['draw_count']}次"},
        {"left": "累计抽到做饭的次数", "right": f"{_count_total_cooking_hits(egg_history)}次"},
    ]
    prepared_sections = [
        {
            "title": "累计记录",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "rows": _prepare_display_rows(summary_rows, min_row_height=42),
        },
        {
            "title": "抽到最多的彰人派生 TOP 3",
            "title_fill": AKITO_ACCENT,
            "rows": _prepare_display_rows(
                _build_character_rows(
                    user_stats["akito_hits"],
                    limit=3,
                    character="彰人",
                    last_hit_seq=user_stats.get("akito_last_hit_seq"),
                )
            ),
        },
        {
            "title": "抽到最多的冬弥派生 TOP 3",
            "title_fill": TOYA_ACCENT,
            "rows": _prepare_display_rows(
                _build_character_rows(
                    user_stats["toya_hits"],
                    limit=3,
                    character="冬弥",
                    last_hit_seq=user_stats.get("toya_last_hit_seq"),
                )
            ),
        },
    ]

    pair_items = _build_personal_cooking_pair_items(egg_history)
    content_width = width - pad_x * 2
    pair_row_count = (len(pair_items) + pair_columns - 1) // pair_columns if pair_items else 0
    pair_grid_width = pair_columns * pair_tile_w + max(0, pair_columns - 1) * pair_gap
    pair_grid_x = pad_x + max(0, (content_width - pair_grid_width) // 2)

    foxbun_icon = _load_fox_stat_icon("foxbun")
    foxbun_text = f"狐兔饭：累计触发 {egg_history['foxbun_count']} 次。"
    fox_line_height = max(56, (foxbun_icon.height if foxbun_icon else 0) + 8)

    height = pad_y + name_height + 18
    for section in prepared_sections:
        height += 34
        for _row, _prefix_icon, _suffix_icons, row_height in section["rows"]:
            height += row_height + row_gap
        height += section_gap

    height += 34
    if pair_row_count:
        height += pair_section_top_gap
        height += pair_row_count * pair_tile_h + max(0, pair_row_count - 1) * pair_gap
    else:
        height += 44
    height += section_gap

    height += fox_section_gap + fox_line_height
    height += pad_y

    canvas = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    y = pad_y
    draw.text((pad_x, y), name_text, font=font_name, fill="#111111", anchor="la")
    y += name_height + 18

    for section in prepared_sections:
        y += _draw_section_label(
            draw,
            left=pad_x,
            right=width - pad_x,
            y=y,
            text=section["title"],
            font=font_section,
            fill=section.get("title_fill", "#333333"),
            bg_fill=section.get("title_bg"),
        )
        rows = section["rows"]
        for row, prefix_icon, suffix_icons, row_height in rows:
            text_x = pad_x
            if prefix_icon:
                icon_y = y + (row_height - prefix_icon.height) // 2
                canvas.paste(prefix_icon, (pad_x, icon_y))
                text_x += prefix_icon.width + 14

            value_text = row.get("right", "")
            value_width = _text_width(font_value, value_text)
            suffix_width = 0
            if suffix_icons:
                suffix_width = sum(icon.width for icon in suffix_icons) + 8 * len(suffix_icons)
            available_width = width - pad_x - value_width - 18 - text_x - suffix_width
            left_text = _truncate_text(row.get("left", ""), font_row, available_width)
            row_center_y = y + row_height // 2
            draw.text((text_x, row_center_y), left_text, font=font_row, fill="#000000", anchor="lm")

            left_text_width = _text_width(font_row, left_text)
            suffix_x = text_x + left_text_width + 8
            for icon in suffix_icons:
                icon_y = y + (row_height - icon.height) // 2
                canvas.paste(icon, (suffix_x, icon_y))
                suffix_x += icon.width + 8

            draw.text((width - pad_x, row_center_y), value_text, font=font_value, fill="#555555", anchor="rm")
            y += row_height + row_gap
        y += section_gap

    y += _draw_section_label(
        draw,
        left=pad_x,
        right=width - pad_x,
        y=y,
        text="你还没有做的派生饭……",
        font=font_section,
        fill="#ffffff",
        bg_fill=SECTION_BAR_BG,
    )
    if pair_items:
        y += pair_section_top_gap
        for index, item in enumerate(pair_items):
            row_index = index // pair_columns
            col_index = index % pair_columns
            tile_x = pair_grid_x + col_index * (pair_tile_w + pair_gap)
            tile_y = y + row_index * (pair_tile_h + pair_gap)
            draw.rectangle(
                [(tile_x, tile_y), (tile_x + pair_tile_w, tile_y + pair_tile_h)],
                fill="#fafafa",
                outline="#dddddd",
                width=1,
            )
            thumb = _build_pair_thumb(item["akito_name"], item["toya_name"], size=54)
            thumb_x = tile_x + (pair_tile_w - thumb.width) // 2
            canvas.paste(thumb, (thumb_x, tile_y + 9))
            draw.text(
                (tile_x + 10, tile_y + pair_tile_h - 12),
                f"x{item['count']}",
                font=font_pair,
                fill="#666666",
                anchor="ld",
            )
        y += pair_row_count * pair_tile_h + max(0, pair_row_count - 1) * pair_gap
    else:
        draw.text((pad_x, y + 20), "还没有抽到做饭彩蛋", font=font_row, fill="#888888", anchor="la")
        y += 44
    y += fox_section_gap

    line_y = y + fox_line_height // 2
    text_x = pad_x
    if foxbun_icon:
        icon_y = y + (fox_line_height - foxbun_icon.height) // 2
        canvas.paste(foxbun_icon, (pad_x, icon_y))
        text_x += foxbun_icon.width + 14
    draw.text((text_x, line_y), foxbun_text, font=font_section, fill="#333333", anchor="lm")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def _build_paro_rank_pil_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    subtitle = _subtitle_for_scope(period_stats, scope)
    sections = [
        {
            "title": "本群累计抽取总次数",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": [{"left": "总计", "right": f"{period_stats['total_draws']}次"}],
        },
        {
            "title": "抽取次数最多的前 5 人",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_user_rows(period_stats["user_draw_counts"], group_stats["profiles"], limit=5),
        },
        {
            "title": "被抽到最多次的彰人 TOP 3",
            "title_fill": AKITO_ACCENT,
            "rows": _build_character_rows(
                period_stats["akito_hits"],
                limit=3,
                character="彰人",
                last_hit_seq=period_stats.get("akito_last_hit_seq"),
            ),
        },
        {
            "title": "被抽到最多次的冬弥 TOP 3",
            "title_fill": TOYA_ACCENT,
            "rows": _build_character_rows(
                period_stats["toya_hits"],
                limit=3,
                character="冬弥",
                last_hit_seq=period_stats.get("toya_last_hit_seq"),
            ),
        },
    ]
    title = "每日派生排行榜" if scope == "daily" else "历史派生排行榜"
    return _render_leaderboard_card(title, subtitle, sections)


def _build_egg_rank_pil_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    subtitle = _subtitle_for_scope(period_stats, scope)
    sections = [
        {
            "title": "做饭 + 狐兔饭触发最多的前 5 人",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_user_rows(period_stats["egg_user_counts"], group_stats["profiles"], limit=5),
        },
        {
            "title": "狐兔彩蛋触发次数",
            "title_fill": "#ffffff",
            "title_bg": SECTION_BAR_BG,
            "title_gap_after": 10,
            "rows": _build_fox_rows(period_stats),
        },
    ]
    title = "每日做饭排行榜" if scope == "daily" else "历史做饭排行榜"
    return _render_leaderboard_card(title, subtitle, sections)


def _build_personal_paro_pil_image_from_user_stats(
    user_id: str,
    display_name: str,
    user_stats: dict,
    egg_history: dict | None = None,
) -> bytes:
    return _render_personal_paro_card(
        user_id,
        display_name,
        _normalize_user_stats(user_stats),
        egg_history or _new_user_egg_history(),
    )


async def _render_paro_rank_image_from_stats(group_stats: dict, period_stats: dict, scope: str, *, cache_key: str | None = None) -> bytes:
    data = _build_paro_rank_page_data_from_stats(group_stats, period_stats, scope)
    return await _render_html_page(
        "ranking.html",
        data,
        cache_key=cache_key,
        fallback=lambda: _build_paro_rank_pil_image_from_stats(group_stats, period_stats, scope),
    )


def _build_paro_rank_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    return _run_async_render_sync(_render_paro_rank_image_from_stats(group_stats, period_stats, scope))


async def _render_paro_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return await _render_paro_rank_image_from_stats(
        group_stats,
        period_stats,
        scope,
        cache_key=f"paro_rank:{group_id}:{scope}",
    )


def _build_paro_rank_image(group_id: int, scope: str) -> bytes:
    return _run_async_render_sync(_render_paro_rank_image(group_id, scope))


async def _render_egg_rank_image_from_stats(group_stats: dict, period_stats: dict, scope: str, *, cache_key: str | None = None) -> bytes:
    data = _build_egg_rank_page_data_from_stats(group_stats, period_stats, scope)
    return await _render_html_page(
        "cook_rank.html",
        data,
        cache_key=cache_key,
        fallback=lambda: _build_egg_rank_pil_image_from_stats(group_stats, period_stats, scope),
    )


def _build_egg_rank_image_from_stats(group_stats: dict, period_stats: dict, scope: str) -> bytes:
    return _run_async_render_sync(_render_egg_rank_image_from_stats(group_stats, period_stats, scope))


async def _render_egg_rank_image(group_id: int, scope: str) -> bytes:
    group_stats, period_stats = _get_group_period_stats(group_id, scope)
    return await _render_egg_rank_image_from_stats(
        group_stats,
        period_stats,
        scope,
        cache_key=f"egg_rank:{group_id}:{scope}",
    )


def _build_egg_rank_image(group_id: int, scope: str) -> bytes:
    return _run_async_render_sync(_render_egg_rank_image(group_id, scope))


async def _render_personal_paro_image_from_user_stats(
    user_id: str,
    display_name: str,
    user_stats: dict,
    egg_history: dict | None = None,
) -> bytes:
    normalized = _normalize_user_stats(user_stats)
    history = egg_history or _new_user_egg_history()
    data = _build_personal_paro_page_data_from_user_stats(user_id, display_name, normalized, history)
    return await _render_html_page(
        "profile.html",
        data,
        fallback=lambda: _build_personal_paro_pil_image_from_user_stats(user_id, display_name, normalized, history),
    )


def _build_personal_paro_image_from_user_stats(
    user_id: str,
    display_name: str,
    user_stats: dict,
    egg_history: dict | None = None,
) -> bytes:
    return _build_personal_paro_pil_image_from_user_stats(user_id, display_name, user_stats, egg_history)


async def _render_personal_paro_image(group_id: int, user_id: str, display_name: str) -> bytes:
    group_stats = _get_group_stats(group_id)
    user_stats = _normalize_user_stats(group_stats.get("users", {}).get(user_id))
    egg_history = _collect_user_egg_history(group_id, user_id)
    return await _render_personal_paro_image_from_user_stats(user_id, display_name, user_stats, egg_history)


def _build_personal_paro_image(group_id: int, user_id: str, display_name: str) -> bytes:
    return _run_async_render_sync(_render_personal_paro_image(group_id, user_id, display_name))


def _build_rank_preview_stats(scope: str) -> tuple[dict, dict]:
    today_str = _today_str()
    group_stats = _new_group_stats(today_str)
    group_stats["profiles"] = {
        "10001": "测试群友甲甲甲甲甲甲甲",
        "10002": "测试群友乙乙乙乙乙乙乙",
        "10003": "测试群友丙丙丙丙丙丙丙",
        "10004": "测试群友丁丁丁丁丁丁丁",
        "10005": "测试群友戊戊戊戊戊戊戊",
        "10006": "测试群友己己己己己己己",
    }

    if scope == "daily":
        period_stats = _new_period_stats(date=today_str)
        period_stats.update(
            {
                "total_draws": 36,
                "user_draw_counts": {
                    "10001": 12,
                    "10002": 8,
                    "10003": 8,
                    "10004": 4,
                    "10005": 3,
                    "10006": 1,
                },
                "akito_hits": {
                    "Callboy彰": 9,
                    "白骑": 8,
                    "王子彰": 8,
                    "WL2彰": 8,
                    "白恶魔": 8,
                    "法师彰": 8,
                },
                "akito_last_hit_seq": {
                    "Callboy彰": 15,
                    "白骑": 21,
                    "王子彰": 24,
                    "WL2彰": 29,
                    "白恶魔": 32,
                    "法师彰": 38,
                },
                "toya_hits": {
                    "Callboy冬": 11,
                    "白百合": 10,
                    "王子冬": 10,
                    "WL2冬": 10,
                    "黑骑": 10,
                    "青鸟": 10,
                },
                "toya_last_hit_seq": {
                    "Callboy冬": 18,
                    "白百合": 20,
                    "王子冬": 23,
                    "WL2冬": 28,
                    "黑骑": 31,
                    "青鸟": 36,
                },
                "egg_user_counts": {
                    "10001": 4,
                    "10002": 3,
                    "10003": 3,
                    "10004": 2,
                },
                "foxrabbit_total": 3,
                "foxbun_total": 5,
                "fox_total": 4,
                "rabbit_total": 1,
            }
        )
        period_stats["egg_user_counts"]["10005"] = 1
    else:
        period_stats = _new_period_stats()
        period_stats.update(
            {
                "total_draws": 128,
                "user_draw_counts": {
                    "10001": 48,
                    "10002": 30,
                    "10003": 30,
                    "10004": 10,
                    "10005": 7,
                    "10006": 3,
                },
                "akito_hits": {
                    "Callboy彰": 26,
                    "白骑": 25,
                    "王子彰": 25,
                    "WL2彰": 25,
                    "白恶魔": 25,
                },
                "akito_last_hit_seq": {
                    "Callboy彰": 41,
                    "白骑": 52,
                    "王子彰": 58,
                    "WL2彰": 64,
                    "白恶魔": 71,
                },
                "toya_hits": {
                    "Callboy冬": 31,
                    "白百合": 29,
                    "王子冬": 29,
                    "WL2冬": 29,
                    "黑骑": 29,
                },
                "toya_last_hit_seq": {
                    "Callboy冬": 44,
                    "白百合": 50,
                    "王子冬": 59,
                    "WL2冬": 68,
                    "黑骑": 74,
                },
                "egg_user_counts": {
                    "10001": 10,
                    "10002": 8,
                    "10003": 8,
                    "10004": 4,
                    "10005": 2,
                },
                "foxrabbit_total": 7,
                "foxbun_total": 9,
                "fox_total": 11,
                "rabbit_total": 18,
            }
        )

    return group_stats, period_stats


async def _render_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = _build_rank_preview_stats(scope)
    return await _render_paro_rank_image_from_stats(group_stats, period_stats, scope)


def _build_rank_preview_image(scope: str) -> bytes:
    return _run_async_render_sync(_render_rank_preview_image(scope))


async def _render_egg_rank_preview_image(scope: str) -> bytes:
    group_stats, period_stats = _build_rank_preview_stats(scope)
    return await _render_egg_rank_image_from_stats(group_stats, period_stats, scope)


def _build_egg_rank_preview_image(scope: str) -> bytes:
    return _run_async_render_sync(_render_egg_rank_preview_image(scope))


async def _render_personal_preview_image() -> bytes:
    user_stats = _new_user_stats()
    egg_history = _new_user_egg_history()
    results = [
        ("Callboy彰", "Callboy冬", True, None),
        ("白骑", "王子冬", True, None),
        ("白骑", "王子冬", False, None),
        ("王子彰", "白百合", True, None),
        ("WL2彰", "WL2冬", False, None),
        ("Callboy彰", "Callboy冬", False, None),
        ("法师彰", "青鸟", True, None),
        ("白恶魔", "黑骑", False, None),
        ("白骑", "黑骑", False, None),
        ("王子彰", "白百合", False, None),
        ("Callboy彰", "Callboy冬", False, "foxbun"),
        ("白骑", "王子冬", False, "foxrabbit"),
        ("向日葵彰", "向日葵冬", False, None),
        ("野营彰", "野营冬", False, None),
        ("模特彰", "模特冬", False, None),
        ("厨子", "2星厨冬", True, None),
    ]
    _record_user_draw_stats(user_stats, results=results)
    for akito_name, toya_name, is_egg, fox_type in results:
        if is_egg:
            _record_user_egg_history_entry(
                egg_history,
                akito_name=akito_name,
                toya_name=toya_name,
                egg_type="cooking",
            )
        elif fox_type == "foxbun":
            _record_user_egg_history_entry(
                egg_history,
                akito_name=akito_name,
                toya_name=toya_name,
                egg_type="foxbun",
            )
    return await _render_personal_paro_image_from_user_stats("10001", "测试群友甲甲甲甲", user_stats, egg_history)


def _build_personal_preview_image() -> bytes:
    return _run_async_render_sync(_render_personal_preview_image())


# ==================== 抽派生 ====================
async def _render_draw_result_image(
    results: list[tuple[str, str, bool, str | None]],
    remaining: int,
    nickname: str,
) -> bytes:
    data = _build_draw_result_page_data(results, remaining, nickname)
    return await _render_html_page(
        "draw_result.html",
        data,
        fallback=lambda: _render_multi(results, remaining, nickname),
    )


async def _render_draw_result_preview_image(
    results: list[tuple[str, str, bool, str | None]],
    remaining: int,
    nickname: str,
) -> bytes:
    if PARO_USE_HTML_RENDER:
        return await _render_draw_result_image(results, remaining, nickname)
    return _render_multi(results, remaining, nickname)


_DRAW_LOCKS: dict[str, asyncio.Lock] = {}
_DRAW_LIMIT = 3
_DRAW_WINDOW = 1800  # 30 分钟


def _resolve_group_command(event: Event) -> tuple[int | None, str | None]:
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None, "该指令仅支持群聊使用。"
    if group_id not in ALLOWED_CHAT_GROUPS:
        return None, None
    return int(group_id), None


def _event_display_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        display_name = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name
    return f"用户{event.get_user_id()}"


draw_cmd = on_command("抽派生", priority=5, block=True)


@draw_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await draw_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    if user_id not in _DRAW_LOCKS:
        _DRAW_LOCKS[user_id] = asyncio.Lock()

    async with _DRAW_LOCKS[user_id]:
        akito_pool = PARO_DATA.get("akito_pool", [])
        toya_pool = PARO_DATA.get("toya_pool", [])

        if not akito_pool:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id) + "彰人的派生池还是空的，先用 /添加彰人派生 添加一些吧。"
            )
        if not toya_pool:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id) + "冬弥的派生池还是空的，先用 /添加冬弥派生 添加一些吧。"
            )

        # 解析参数：提取数字 token（1-3）+ 方向/名称
        count, directional = _parse_draw_request(args.extract_plain_text())
        fixed_a, fixed_b, directional_error = _resolve_directional_draw(directional, akito_pool, toya_pool)
        if directional_error:
            await draw_cmd.finish(directional_error)

        # 限频检查
        now = time.time()
        cooldowns = _cooldown_store()
        previous_history = list(cooldowns.get(user_id, []))
        history = _prune_draw_history(previous_history, now, _DRAW_WINDOW)
        cooldowns[user_id] = history
        if history != previous_history:
            _save_stats()
        remaining_before = _DRAW_LIMIT - len(history)
        limit_message = _build_draw_limit_message(
            remaining_before=remaining_before,
            requested_count=count,
            history=history,
            now_ts=now,
            draw_limit=_DRAW_LIMIT,
            draw_window=_DRAW_WINDOW,
        )
        if limit_message:
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + limit_message)

        nickname = _event_display_name(event)

        # N 次独立抽取（做饭 > 狐兔，多抽时狐兔最多一次）
        results = []
        foxrabbit_used = False
        for _ in range(count):
            a = fixed_a or random.choice(akito_pool)
            b = fixed_b or random.choice(toya_pool)
            is_egg = random.random() < _EASTER_EGG_RATE
            fox_type = None
            if not is_egg and not foxrabbit_used:
                rr = random.random()
                if rr < _FOXRABBIT_RATE:
                    fox_type = "fox"
                    foxrabbit_used = True
                elif rr < _FOXRABBIT_RATE * 2:
                    fox_type = "rabbit"
                    foxrabbit_used = True
                elif rr < _FOXRABBIT_RATE * 3:
                    fox_type = "foxrabbit"
                    foxrabbit_used = True
                elif rr < _FOXRABBIT_RATE * 4:
                    fox_type = "foxbun"
                    foxrabbit_used = True
            results.append((a, b, is_egg, fox_type))

        for _ in range(count):
            history.append(now)
        cooldowns[user_id] = history
        remaining = _DRAW_LIMIT - len(history)

        fixed_side = _get_fixed_side(fixed_a, fixed_b)
        fixed_name = fixed_a or fixed_b
        _record_group_draw_stats(
            group_id=group_id,
            user_id=user_id,
            display_name=nickname,
            results=results,
            fixed_side=fixed_side,
            fixed_name=fixed_name,
            requested_count=count,
            now_ts=now,
        )

        await asyncio.sleep(random.uniform(0.4, 0.8))

        if PARO_USE_HTML_RENDER:
            img_bytes = await _render_draw_result_image(results, remaining, nickname)
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))

        has_any_fr = any(ft for _, _, _, ft in results)
        if count == 1 and not has_any_fr:
            # 单抽无狐兔 — 保持原有输出
            a, b, is_egg, _ = results[0]
            if is_egg:
                text_lines = [
                    f"@{nickname}：",
                    "对，就是你，你是被选中的彰冬姐，",
                    [
                        ("奖励你现在来做", "#000000", True),
                        (a, "#FF7722", True),
                        ("×", "#000000", True),
                        (b, "#0077DD", True),
                        ("的饭！", "#000000", True),
                    ],
                ]
            else:
                text_lines = [
                    [
                        ("你抽到的派生是：", "#000000", False),
                        (a, "#FF7722", True),
                        ("×", "#000000", True),
                        (b, "#0077DD", True),
                        ("。", "#000000", False),
                    ],
                    f"（30分钟内剩余 {remaining} 次）",
                ]
            has_av = _find_avatar("彰人", a) and _find_avatar("冬弥", b)
            if has_av:
                img_bytes = _render_composite(a, b, text_lines)
                await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))
            elif is_egg:
                img_bytes = _render_text_only(text_lines)
                await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))
            else:
                plain = f"你抽到的派生是：{a}×{b}。（30分钟内剩余 {remaining} 次）"
                await draw_cmd.finish(MessageSegment.reply(event.message_id) + plain)
        else:
            # 多抽 或 单抽含狐兔 → 走 _render_multi
            img_bytes = _render_multi(results, remaining, nickname)
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


my_paro_cmd = on_command("我的派生", priority=5, block=True)


@my_paro_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await my_paro_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_personal_paro_image(group_id, event.get_user_id(), _event_display_name(event))
    await my_paro_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


daily_rank_cmd = on_command("每日排行", priority=5, block=True)


@daily_rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await daily_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_paro_rank_image(group_id, "daily")
    await daily_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


history_rank_cmd = on_command("历史排行", priority=5, block=True)


@history_rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await history_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_paro_rank_image(group_id, "history")
    await history_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


daily_egg_rank_cmd = on_command("每日做饭排行", priority=5, block=True)


@daily_egg_rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await daily_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_egg_rank_image(group_id, "daily")
    await daily_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


history_egg_rank_cmd = on_command("历史做饭排行", priority=5, block=True)


@history_egg_rank_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await history_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_egg_rank_image(group_id, "history")
    await history_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


# ==================== 测试排行榜 ====================

test_daily_rank_cmd = on_command("测试每日排行", priority=5, block=True)


@test_daily_rank_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await test_daily_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_rank_preview_image("daily")
    await test_daily_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


test_history_rank_cmd = on_command("测试历史排行", priority=5, block=True)


@test_history_rank_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await test_history_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_rank_preview_image("history")
    await test_history_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


test_daily_egg_rank_cmd = on_command("测试每日做饭排行", priority=5, block=True)


@test_daily_egg_rank_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await test_daily_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_egg_rank_preview_image("daily")
    await test_daily_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


test_history_egg_rank_cmd = on_command("测试历史做饭排行", priority=5, block=True)


@test_history_egg_rank_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await test_history_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_egg_rank_preview_image("history")
    await test_history_egg_rank_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


test_my_paro_cmd = on_command("测试我的派生", priority=5, block=True)


@test_my_paro_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    group_id, rejection = _resolve_group_command(event)
    if rejection:
        await test_my_paro_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return
    img_bytes = await _render_personal_preview_image()
    await test_my_paro_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


# ==================== 测试做饭 ====================

test_egg_cmd = on_command("test做饭", priority=5, block=True)


@test_egg_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    sender = getattr(event, "sender", None)
    nickname = getattr(sender, "card", None) or getattr(sender, "nickname", None) or "测试者"
    results = [("Callboy彰", "Callboy冬", True, None)]
    img = await _render_draw_result_preview_image(results, remaining=2, nickname=nickname)
    await test_egg_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))
    nickname = event.sender.card or event.sender.nickname or "测试者"
    a, b = "Callboy彰", "Callboy冬"
    text_lines = [
        f"@{nickname}：",
        "对，就是你，你是被选中的彰冬姐，",
        [
            ("奖励你现在来做", "#000000", True),
            (a, "#FF7722", True),
            ("×", "#000000", True),
            (b, "#0077DD", True),
            ("的饭！", "#000000", True),
        ],
    ]
    if _find_avatar("彰人", a) and _find_avatar("冬弥", b):
        img = _render_composite(a, b, text_lines)
    else:
        img = _render_text_only(text_lines)
    await test_egg_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))


# ==================== 测试多派生 ====================

test_multi_cmd = on_command("test多派生", priority=5, block=True)


@test_multi_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    nickname = event.sender.card or event.sender.nickname or "测试者"
    pool_a = PARO_DATA.get("akito_pool", ["Callboy彰", "黑百合"])
    pool_b = PARO_DATA.get("toya_pool", ["Callboy冬", "王子冬"])
    # 固定抽取 3 次，其中恰好前 2 个为做饭彩蛋
    results = [
        (pool_a[0], pool_b[0], True, None),
        (pool_a[1], pool_b[1], True, None),
        (pool_a[0], pool_b[1], False, None),
    ]
    img = await _render_draw_result_preview_image(results, remaining=1, nickname=nickname)
    await test_multi_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))


# ==================== 测试狐兔彩蛋 ====================

test_fr_cmd = on_command("test狐兔彩蛋", priority=5, block=True)


@test_fr_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    nickname = event.sender.card or event.sender.nickname or "测试者"
    pool_a = PARO_DATA.get("akito_pool", ["黑百合", "白骑"])
    pool_b = PARO_DATA.get("toya_pool", ["王子冬", "黑骑"])
    results = [
        (pool_a[0], pool_b[0], False, "fox"),
        (pool_a[1], pool_b[1], False, None),
        (pool_a[0], pool_b[1], False, "foxrabbit"),
    ]
    img = await _render_draw_result_preview_image(results, remaining=2, nickname=nickname)
    await test_fr_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))


# ==================== 测试狐兔饭 ====================

test_foxbun_cmd = on_command("test狐兔饭", priority=5, block=True)


@test_foxbun_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    nickname = event.sender.card or event.sender.nickname or "测试者"
    pool_a = PARO_DATA.get("akito_pool", ["黑百合", "白骑"])
    pool_b = PARO_DATA.get("toya_pool", ["王子冬", "黑骑"])
    results = [
        (pool_a[0], pool_b[0], True, None),
        (pool_a[1], pool_b[1], False, "foxbun"),
        (pool_a[0], pool_b[1], False, None),
    ]
    img = await _render_draw_result_preview_image(results, remaining=2, nickname=nickname)
    await test_foxbun_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))


# ==================== 添加派生 ====================
add_akito_cmd = on_command("添加彰人派生", priority=5, block=True)


@add_akito_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = args.extract_plain_text().strip()
    if not name:
        await add_akito_cmd.finish("请告诉我要添加的派生名称，例如：/添加彰人派生 黑百合")
    PARO_DATA.setdefault("akito_pool", []).append(name)
    _save()
    await add_akito_cmd.finish(f"已将「{name}」加入彰人的派生池（当前共 {len(PARO_DATA['akito_pool'])} 个）。")


add_toya_cmd = on_command("添加冬弥派生", priority=5, block=True)


@add_toya_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = args.extract_plain_text().strip()
    if not name:
        await add_toya_cmd.finish("请告诉我要添加的派生名称，例如：/添加冬弥派生 王子")
    PARO_DATA.setdefault("toya_pool", []).append(name)
    _save()
    await add_toya_cmd.finish(f"已将「{name}」加入冬弥的派生池（当前共 {len(PARO_DATA['toya_pool'])} 个）。")


# ==================== 删除派生 ====================
del_akito_cmd = on_command("删除彰人派生", priority=5, block=True)


@del_akito_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = args.extract_plain_text().strip()
    if not name:
        await del_akito_cmd.finish("请告诉我要删除的派生名称，例如：/删除彰人派生 黑百合")
    pool = PARO_DATA.get("akito_pool", [])
    if name not in pool:
        await del_akito_cmd.finish(f"彰人的派生池里没有「{name}」这个条目。")
    pool.remove(name)
    _save()
    await del_akito_cmd.finish(f"已从彰人的派生池中删除「{name}」。")



del_toya_cmd = on_command("删除冬弥派生", priority=5, block=True)


@del_toya_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = args.extract_plain_text().strip()
    if not name:
        await del_toya_cmd.finish("请告诉我要删除的派生名称，例如：/删除冬弥派生 王子")
    pool = PARO_DATA.get("toya_pool", [])
    if name not in pool:
        await del_toya_cmd.finish(f"冬弥的派生池里没有「{name}」这个条目。")
    pool.remove(name)
    _save()
    await del_toya_cmd.finish(f"已从冬弥的派生池中删除「{name}」。")



# ==================== 查看派生 ====================
view_akito_cmd = on_command("查看彰人派生", priority=5, block=True)


@view_akito_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    pool = PARO_DATA.get("akito_pool", [])
    if not pool:
        await view_akito_cmd.finish("彰人的派生池目前是空的。")
    img_bytes = _render_pool_image("彰人派生池", pool)
    await view_akito_cmd.finish(MessageSegment.image(img_bytes))


view_toya_cmd = on_command("查看冬弥派生", priority=5, block=True)


@view_toya_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    pool = PARO_DATA.get("toya_pool", [])
    if not pool:
        await view_toya_cmd.finish("冬弥的派生池目前是空的。")
    img_bytes = _render_pool_image("冬弥派生池", pool)
    await view_toya_cmd.finish(MessageSegment.image(img_bytes))
