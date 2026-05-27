import asyncio
import io
import json
import os
import random
from datetime import date as date_type
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.log import logger

from ..core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ, TZ_CN
from ..core.data import _find_data_path, load_json_file

DATA_FILE = "fanfic_keywords.json"
DRAWS_FILE = "keyword_draws.json"
DEFAULT_DATA: dict = {"categories": {}}

KEYWORD_DATA: dict = load_json_file(DATA_FILE, DEFAULT_DATA)

CATEGORY_ORDER = [
    "科学隐喻", "病症设定", "自然意象", "场景画面",
    "文学化用", "关系张力",
    "物理 / 天文 / 数学", "植物学 / 园艺", "气象 / 地理",
    "宗教 / 仪式", "古典 / 词章", "电影 / 摄影术语",
    "心理学 / 精神分析", "日常物 / 器物", "时间 / 节令",
    "状态 / 关系学", "同人圈通用结构梗", "关系角色定位梗",
    "设定类", "病症 / 奇幻症", "场景 / 桥段梗",
    "情绪 / 状态梗", "圈内暗号", "原作衍生类型梗",
    "动作 / 身体细节梗",
]


# ==================== 数据持久化 ====================

def _save_pool():
    path = _find_data_path(DATA_FILE)
    if not path:
        path = Path("data") / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(KEYWORD_DATA, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_draws() -> dict:
    path = _find_data_path(DRAWS_FILE)
    if not path:
        path = Path("data") / DRAWS_FILE
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logger.warning(f"读取 {DRAWS_FILE} 失败，已重置抽取记录")
        return {}


def _save_draws(data: dict):
    path = _find_data_path(DRAWS_FILE)
    if not path:
        path = Path("data") / DRAWS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def reload_keyword_data():
    global KEYWORD_DATA
    KEYWORD_DATA = load_json_file(DATA_FILE, DEFAULT_DATA)
    logger.info("🔄 关键词池数据已热重载")


def _get_non_empty_categories() -> list[tuple[str, list]]:
    categories = KEYWORD_DATA.get("categories", {})
    result = []
    for cat in CATEGORY_ORDER:
        items = categories.get(cat, [])
        if items:
            result.append((cat, items))
    for cat, items in categories.items():
        if cat not in CATEGORY_ORDER and items:
            result.append((cat, items))
    return result


# ==================== 图片渲染 ====================

def _load_font(size: int):
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        font_path = os.path.join(current_dir, "msyhbd.ttc")
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


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
    for cat_name, items in cats:
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
                if match_type == "exact" and item.lower() == name_lower:
                    candidates.append((item, cat))
                elif match_type == "prefix" and item.lower().startswith(name_lower):
                    candidates.append((item, cat))
                elif match_type == "contains" and name_lower in item.lower():
                    candidates.append((item, cat))
        if len(candidates) == 1:
            return candidates[0][0], candidates[0][1]
        if len(candidates) > 1:
            return [c[0] for c in candidates], None

    return None, None


# ==================== 今日关键词 ====================

_DRAW_LOCKS: dict[str, asyncio.Lock] = {}

draw_cmd = on_command("今日关键词", priority=5, block=True)


@draw_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
        return

    user_id = event.get_user_id()
    is_superuser = str(event.get_user_id()) == SUPERUSER_QQ

    if user_id not in _DRAW_LOCKS:
        _DRAW_LOCKS[user_id] = asyncio.Lock()

    async with _DRAW_LOCKS[user_id]:
        cats = _get_non_empty_categories()
        if not cats:
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id)
                + "关键词池还是空的，先用 /添加关键词 添加一些吧。"
            )

        today_str = datetime.now(TZ_CN).date().isoformat()

        # 普通用户每日限 1 次；超管不限制
        if not is_superuser:
            draws = _load_draws()
            user_record = draws.get(user_id)

            if user_record:
                stored_date_str = user_record.get("date", "")
                try:
                    stored_date = date_type.fromisoformat(stored_date_str)
                    today = date_type.fromisoformat(today_str)
                    if stored_date >= today:
                        prev_items = user_record.get("items", [])
                        prev = "、".join(prev_items) if prev_items else "（无记录）"
                        await draw_cmd.finish(
                            MessageSegment.reply(event.message_id)
                            + f"你今天已经领取过关键词了：{prev}，明天 0:00 刷新哦。"
                        )
                except (ValueError, TypeError):
                    logger.warning(f"用户 {user_id} 的抽取日期记录异常，已重置")

        # 随机选 N 个不同分类，每个分类随机抽 1 个
        count = random.randint(1, 3)
        count = min(count, len(cats))
        chosen_cats = random.sample(cats, k=count)
        selected: list[str] = []
        for cat_name, items in chosen_cats:
            kw = random.choice(items)
            selected.append(kw)

        # 保存普通用户记录；超管不保存
        if not is_superuser:
            draws = _load_draws()
            draws[user_id] = {"date": today_str, "count": len(selected), "items": selected}
            _save_draws(draws)

        await asyncio.sleep(random.uniform(1.0, 2.5))

        nickname = event.sender.card or event.sender.nickname or f"用户{user_id}"
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
            f"可用分类：{' / '.join(CATEGORY_ORDER)}"
        )

    parts = raw.split(None, 1)
    if len(parts) < 2:
        await add_cmd.finish(
            "请同时指定分类和关键词名称，例如：/添加关键词 科学隐喻 洛希极限\n"
            f"可用分类：{' / '.join(CATEGORY_ORDER)}"
        )

    cat_name, kw_name = parts[0], parts[1]
    categories = KEYWORD_DATA.setdefault("categories", {})

    cat_match = None
    for c in categories:
        if c.lower() == cat_name.lower():
            cat_match = c
            break
    if not cat_match:
        for c in CATEGORY_ORDER:
            if c.lower().startswith(cat_name.lower()):
                cat_match = c
                break
    if not cat_match:
        await add_cmd.finish(
            f"未找到分类「{cat_name}」。可用分类：{' / '.join(CATEGORY_ORDER)}"
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
