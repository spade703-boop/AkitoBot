"""今日关键词：六分类去重抽取每日关键词，群内同日不放回。"""

from __future__ import annotations

import asyncio
from datetime import date as date_type
from datetime import datetime
import io
import json
import os
import random

from nonebot import on_command
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.log import logger
from nonebot.params import CommandArg
from PIL import Image, ImageDraw

from ...core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ, TZ_CN, find_data_path, get_data_dir, load_json_file
from .._shared import load_msyhbd_font

DATA_FILE = "fanfic_keywords.json"
DRAWS_FILE = "keyword_draws.json"
DEFAULT_DATA: dict = {"categories": {}}
DRAW_STATE_VERSION = 2

KEYWORD_DATA: dict = load_json_file(DATA_FILE, DEFAULT_DATA)


def _get_category_names() -> list[str]:
    """返回当前数据文件中所有分类名（按 JSON key 顺序）。"""
    return list(KEYWORD_DATA.get("categories", {}).keys())


# ==================== 数据持久化 ====================

def _save_pool():
    path = find_data_path(DATA_FILE)
    if not path:
        path = get_data_dir() / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(KEYWORD_DATA, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _today_str() -> str:
    return datetime.now(TZ_CN).date().isoformat()


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _new_draws_state() -> dict:
    return {
        "schema_version": DRAW_STATE_VERSION,
        "users": {},
        "groups": {},
    }


def _normalize_keyword_items(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        normalized.append(text)
        seen.add(text)
    return normalized


def _normalize_user_draw_record(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None

    date_str = raw.get("date")
    if not isinstance(date_str, str) or not date_str.strip():
        return None

    items = _normalize_keyword_items(raw.get("items"))
    return {
        "date": date_str,
        "count": len(items),
        "items": items,
    }


def _normalize_group_draw_record(raw: object, today_str: str) -> dict:
    record = {"date": today_str, "drawn_items": []}
    if not isinstance(raw, dict):
        return record

    if raw.get("date") != today_str:
        return record

    record["drawn_items"] = _normalize_keyword_items(raw.get("drawn_items"))
    return record


def _normalize_draws_state(raw: object, today_str: str) -> dict:
    state = _new_draws_state()
    if not isinstance(raw, dict):
        return state

    if "users" in raw or "groups" in raw:
        raw_users = raw.get("users")
        raw_groups = raw.get("groups")
        state["schema_version"] = max(DRAW_STATE_VERSION, _safe_int(raw.get("schema_version"), DRAW_STATE_VERSION))
    else:
        raw_users = raw
        raw_groups = {}

    if isinstance(raw_users, dict):
        state["users"] = {
            str(user_id): normalized
            for user_id, record in raw_users.items()
            if (normalized := _normalize_user_draw_record(record)) is not None
        }

    if isinstance(raw_groups, dict):
        state["groups"] = {
            str(group_id): _normalize_group_draw_record(record, today_str)
            for group_id, record in raw_groups.items()
        }

    return state


def _load_draws() -> dict:
    path = find_data_path(DRAWS_FILE)
    if not path:
        path = get_data_dir() / DRAWS_FILE
    if not path.exists():
        return _new_draws_state()
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        logger.warning(f"读取 {DRAWS_FILE} 失败，已重置抽取记录")
        return _new_draws_state()
    return _normalize_draws_state(raw, _today_str())


def _save_draws(data: dict):
    path = find_data_path(DRAWS_FILE)
    if not path:
        path = get_data_dir() / DRAWS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    normalized = _normalize_draws_state(data, _today_str())
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(normalized, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def reload_keyword_data() -> None:
    """热重载关键词池数据（原地 clear+update，保持其他模块持有的引用不失效）。"""
    KEYWORD_DATA.clear()
    KEYWORD_DATA.update(load_json_file(DATA_FILE, DEFAULT_DATA))
    logger.info("🔄 关键词池数据已热重载")


def _get_non_empty_categories() -> list[tuple[str, list]]:
    """返回所有非空分类的 [(cat_name, items), ...]，直接从数据读取。"""
    categories = KEYWORD_DATA.get("categories", {})
    result = []
    for cat, items in categories.items():
        if items:
            result.append((cat, items))
    return result


# ==================== 图片渲染 ====================

def _load_font(size: int):
    return load_msyhbd_font(size)


SEQS = ["①", "②", "③"]


def _render_keyword_result(keywords: list[str]) -> bytes:
    font_title = _load_font(26)
    font_item = _load_font(22)
    font_footer = _load_font(18)

    row_height = 46
    top_pad = 36
    title_gap = 20
    sep_gap = 14
    footer_gap = 18
    bottom_pad = 30

    n = len(keywords)
    width = 580
    height = top_pad + 30 + title_gap + sep_gap + n * row_height + footer_gap + 22 + bottom_pad

    img = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(img)

    draw.text((width // 2, top_pad), "今日关键词", font=font_title, fill="#000000", anchor="ma")

    y = top_pad + 30 + title_gap
    draw.line([(60, y), (width - 60, y)], fill="#cccccc", width=1)

    for i, kw in enumerate(keywords):
        item_y = y + sep_gap + i * row_height
        draw.text((80, item_y), SEQS[i], font=font_item, fill="#555555")
        draw.text((130, item_y), kw, font=font_item, fill="#000000")

    footer_y = y + sep_gap + n * row_height + footer_gap
    draw.text((width // 2, footer_y), "已领取今日份关键词，明天再来吧！",
              font=font_footer, fill="#999999", anchor="ma")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_categories_image() -> bytes:
    font_title = _load_font(28)
    font_cat_header = _load_font(22)
    font_item = _load_font(20)
    font_footer = _load_font(17)

    cats = _get_non_empty_categories()
    if not cats:
        return _render_empty_pool()

    top_pad = 30
    title_h = 36
    title_gap = 20
    cat_header_h = 28
    cat_gap = 10
    item_row_h = 30
    between_cat_gap = 16
    footer_gap = 16
    bottom_pad = 24

    height = top_pad + title_h + title_gap
    for _cat_name, items in cats:
        height += cat_header_h + cat_gap
        height += len(items) * item_row_h
        height += between_cat_gap
    height += footer_gap + 20 + bottom_pad

    width = 620

    img = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(img)

    draw.text((width // 2, top_pad), "同人写作关键词池", font=font_title, fill="#000000", anchor="ma")

    y = top_pad + title_h + title_gap
    draw.line([(40, y), (width - 40, y)], fill="#cccccc", width=1)
    y += 10

    total_count = 0
    for cat_name, items in cats:
        draw.text((60, y), f"▎{cat_name}", font=font_cat_header, fill="#555555")
        y += cat_header_h + cat_gap

        for name in items:
            draw.text((80, y), name, font=font_item, fill="#000000")
            y += item_row_h
        y += between_cat_gap
        total_count += len(items)

    footer_y = y - between_cat_gap + footer_gap
    draw.text((width // 2, footer_y), f"共 {total_count} 个关键词，{len(cats)} 个分类",
              font=font_footer, fill="#999999", anchor="ma")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _render_empty_pool() -> bytes:
    width, height = 400, 80
    img = Image.new("RGB", (width, height), color="#ffffff")
    draw = ImageDraw.Draw(img)
    font = _load_font(20)
    draw.text((width // 2, height // 2), "关键词池目前是空的", font=font, fill="#999999", anchor="mm")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ==================== 模糊匹配 ====================

def _fuzzy_match_in_categories(name: str) -> tuple[str | list | None, str | None]:
    name_lower = name.lower()
    categories = KEYWORD_DATA.get("categories", {})

    for match_type in ("exact", "prefix", "contains"):
        candidates: list[tuple[str, str]] = []
        for cat, items in categories.items():
            for item in items:
                if match_type == "exact" and item.lower() == name_lower or match_type == "prefix" and item.lower().startswith(name_lower) or match_type == "contains" and name_lower in item.lower():
                    candidates.append((item, cat))
        if len(candidates) == 1:
            return candidates[0][0], candidates[0][1]
        if len(candidates) > 1:
            return [c[0] for c in candidates], None

    return None, None


def _resolve_keyword_category_name(cat_name: str, category_names: list[str]) -> str | None:
    """Match a category by exact name first, then by unique prefix."""
    for current in category_names:
        if current.lower() == cat_name.lower():
            return current
    for current in category_names:
        if current.lower().startswith(cat_name.lower()):
            return current
    return None


def _get_existing_keyword_draw_message(user_record: dict | None, today_str: str) -> str | None:
    """Return the 'already drawn today' message when the stored record is still valid."""
    if not user_record:
        return None

    stored_date_str = user_record.get("date", "")
    try:
        stored_date = date_type.fromisoformat(stored_date_str)
        today = date_type.fromisoformat(today_str)
    except (ValueError, TypeError):
        logger.warning("用户抽取日期记录异常，已重置")
        return None

    if stored_date < today:
        return None

    prev_items = user_record.get("items", [])
    prev = "、".join(prev_items) if prev_items else "（无记录）"
    return f"你今天已经领取过关键词了：{prev}，明天 0:00 刷新哦。"


def _get_or_create_group_draw_record(draws_state: dict, group_id: str, today_str: str) -> dict:
    groups = draws_state.setdefault("groups", {})
    record = _normalize_group_draw_record(groups.get(group_id), today_str)
    groups[group_id] = record
    return record


def _set_user_draw_record(draws_state: dict, user_id: str, today_str: str, items: list[str]) -> None:
    draws_state.setdefault("users", {})[user_id] = {
        "date": today_str,
        "count": len(items),
        "items": list(items),
    }


def _get_group_drawn_items(group_record: dict) -> set[str]:
    return set(_normalize_keyword_items(group_record.get("drawn_items")))


def _filter_categories_excluding_drawn_items(
    categories: list[tuple[str, list]],
    drawn_items: set[str],
) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for cat_name, items in categories:
        available_items = [item for item in items if item not in drawn_items]
        if available_items:
            result.append((cat_name, available_items))
    return result


def _record_group_drawn_items(group_record: dict, items: list[str]) -> None:
    drawn_items = group_record.setdefault("drawn_items", [])
    seen = set(drawn_items)
    for item in items:
        if item in seen:
            continue
        drawn_items.append(item)
        seen.add(item)


def _select_daily_keywords(
    categories: list[tuple[str, list]],
    count: int,
    *,
    sample_fn=random.sample,
    choice_fn=random.choice,
) -> list[str]:
    """Pick one keyword from each randomly chosen category."""
    chosen_cats = sample_fn(categories, k=min(count, len(categories)))
    return [choice_fn(items) for _cat_name, items in chosen_cats]


# ==================== 今日关键词 ====================

_DRAW_LOCK = asyncio.Lock()


def _resolve_group_draw_command(event: Event) -> tuple[str | None, str | None]:
    group_id = getattr(event, "group_id", None)
    if group_id is None:
        return None, "该指令仅支持群聊使用。"
    if int(group_id) not in ALLOWED_CHAT_GROUPS:
        return None, None
    return str(group_id), None


def _event_display_name(event: Event) -> str:
    sender = getattr(event, "sender", None)
    if sender:
        display_name = getattr(sender, "card", None) or getattr(sender, "nickname", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name
    return f"用户{event.get_user_id()}"

draw_cmd = on_command("今日关键词", priority=5, block=True)


@draw_cmd.handle()
async def _(event: Event):
    group_id, rejection = _resolve_group_draw_command(event)
    if rejection:
        await draw_cmd.finish(MessageSegment.reply(event.message_id) + rejection)
    if group_id is None:
        return

    user_id = event.get_user_id()
    is_superuser = user_id == SUPERUSER_QQ

    async with _DRAW_LOCK:
        cats = _get_non_empty_categories()
        if not cats:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id)
                + "关键词池还是空的，先用 /添加关键词 添加一些吧。"
            )

        today_str = _today_str()
        draws = _load_draws()

        # 普通用户每日限 1 次；超管不限制
        if not is_superuser:
            existing_message = _get_existing_keyword_draw_message(draws.get("users", {}).get(user_id), today_str)
            if existing_message:
                await draw_cmd.finish(MessageSegment.reply(event.message_id) + existing_message)

        available_categories = cats
        if not is_superuser:
            group_record = _get_or_create_group_draw_record(draws, group_id, today_str)
            drawn_items = _get_group_drawn_items(group_record)
            available_categories = _filter_categories_excluding_drawn_items(cats, drawn_items)
            if not available_categories:
                await draw_cmd.finish(
                    MessageSegment.reply(event.message_id)
                    + "本群今天的关键词已经抽完了，明天 0:00 再来吧。"
                )

        # 随机选 N 个不同分类，每个分类随机抽 1 个
        count = random.randint(1, 3)
        selected = _select_daily_keywords(available_categories, count)

        # 保存普通用户记录；超管不保存
        if not is_superuser:
            _set_user_draw_record(draws, user_id, today_str, selected)
            _record_group_drawn_items(group_record, selected)
            _save_draws(draws)

        await asyncio.sleep(random.uniform(1.0, 2.5))

        nickname = _event_display_name(event)
        lines = [f"@{nickname} 抽到的今日关键词是："]
        for kw in selected:
            lines.append(f"- {kw}")
        await draw_cmd.finish(
            MessageSegment.reply(event.message_id) + "\n".join(lines)
        )


# ==================== 查看关键词 ====================

view_cmd = on_command("查看关键词", priority=5, block=True)


@view_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return
    cats = _get_non_empty_categories()
    if not cats:
        await view_cmd.finish("关键词池目前是空的。")
    img_bytes = _render_categories_image()
    await view_cmd.finish(MessageSegment.image(img_bytes))


# ==================== 添加关键词 ====================

add_cmd = on_command("添加关键词", priority=5, block=True)


@add_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    raw = args.extract_plain_text().strip()
    if not raw:
        await add_cmd.finish(
            "请指定分类和关键词名称，例如：/添加关键词 科学隐喻 洛希极限\n"
            f"可用分类：{' / '.join(_get_category_names())}"
        )

    parts = raw.split(None, 1)
    if len(parts) < 2:
        await add_cmd.finish(
            "请同时指定分类和关键词名称，例如：/添加关键词 科学隐喻 洛希极限\n"
            f"可用分类：{' / '.join(_get_category_names())}"
        )

    cat_name, kw_name = parts[0], parts[1]
    categories = KEYWORD_DATA.setdefault("categories", {})

    cat_match = _resolve_keyword_category_name(cat_name, _get_category_names())
    if not cat_match:
        await add_cmd.finish(
            f"未找到分类「{cat_name}」。可用分类：{' / '.join(_get_category_names())}"
        )

    categories.setdefault(cat_match, []).append(kw_name)
    _save_pool()
    await add_cmd.finish(
        f"已将「{kw_name}」加入「{cat_match}」分类（当前该分类共 {len(categories[cat_match])} 个）。"
    )


# ==================== 删除关键词 ====================

del_cmd = on_command("删除关键词", priority=5, block=True)


@del_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    name = args.extract_plain_text().strip()
    if not name:
        await del_cmd.finish("请告诉我要删除的关键词名称，例如：/删除关键词 洛希极限")

    categories = KEYWORD_DATA.get("categories", {})
    if not categories:
        await del_cmd.finish("关键词池目前是空的，没有可删除的内容。")

    match, cat_name = _fuzzy_match_in_categories(name)
    if not match:
        await del_cmd.finish(f"关键词池中没有与「{name}」匹配的条目。")
    if isinstance(match, list):
        await del_cmd.finish(f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。")

    categories[cat_name].remove(match)
    if not categories[cat_name]:
        del categories[cat_name]
    _save_pool()
    await del_cmd.finish(f"已从「{cat_name}」分类中删除「{match}」。")

# ==================== 重置关键词 CD ====================

reset_cmd = on_command("重置关键词", priority=5, block=True)


@reset_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
    _save_draws(_new_draws_state())
    await reset_cmd.finish("已重置所有人的今日关键词抽取记录。")
