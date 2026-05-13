import asyncio
import io
import json
import os
import random
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from nonebot import on_command
from nonebot.adapters.onebot.v11 import GroupMessageEvent, MessageSegment
from nonebot.adapters import Event, Message
from nonebot.params import CommandArg
from nonebot.log import logger

from ..core import ALLOWED_CHAT_GROUPS, SUPERUSER_QQ
from ..core.data import _find_data_path, load_json_file

DATA_FILE = "paro_pools.json"
DEFAULT_DATA = {"akito_pool": [], "toya_pool": []}

PARO_DATA: dict = load_json_file(DATA_FILE, DEFAULT_DATA)


def _save():
    path = _find_data_path(DATA_FILE)
    if not path:
        path = Path("data") / DATA_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(PARO_DATA, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def reload_paro_data():
    global PARO_DATA
    PARO_DATA = load_json_file(DATA_FILE, DEFAULT_DATA)
    logger.info("🔄 派生池数据已热重载")


# ==================== 图片渲染 ====================

def _load_font(size: int, bold: bool = False):
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        font_path = os.path.join(current_dir, "msyhbd.ttc" if bold else "font.ttf")
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


def _render_pool_image(title: str, pool: list) -> bytes:
    font_title = _load_font(28, bold=True)
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

AVATAR_BASE = Path("data/images/paro_avatars")


def _find_avatar(character: str, name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        p = AVATAR_BASE / character / f"{name}{ext}"
        if p.exists():
            return p
    return None


def _render_composite(akito_name: str, toya_name: str, line1: str, line2: str) -> bytes:
    avatar_size = 150
    gap = 4
    top_pad = 10
    width = 350
    height = 230

    canvas = Image.new("RGB", (width, height), color="#ffffff")
    font_line = _load_font(20)
    font_hint = _load_font(18)

    def _paste_avatar(character: str, name: str, x_offset: int):
        path = _find_avatar(character, name)
        if path:
            img = Image.open(path).convert("RGB")
            img = img.resize((avatar_size, avatar_size), Image.LANCZOS)
            canvas.paste(img, (x_offset, top_pad))

    avatars_width = avatar_size * 2 + gap
    avatars_x = (width - avatars_width) // 2
    _paste_avatar("彰人", akito_name, avatars_x)
    _paste_avatar("冬弥", toya_name, avatars_x + avatar_size + gap)

    draw = ImageDraw.Draw(canvas)
    text_y1 = top_pad + avatar_size + 16
    text_y2 = top_pad + avatar_size + 42
    draw.text((width // 2, text_y1), line1, font=font_line, fill="#000000", anchor="ma")
    draw.text((width // 2, text_y2), line2, font=font_hint, fill="#999999", anchor="ma")

    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


# ==================== 抽派生 ====================
_DRAW_COOLDOWNS: dict[str, list[float]] = {}
_DRAW_LOCKS: dict[str, asyncio.Lock] = {}
_DRAW_LIMIT = 3
_DRAW_WINDOW = 1800  # 30 分钟


draw_cmd = on_command("抽派生", priority=5, block=True)


@draw_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CHAT_GROUPS:
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

        # 解析参数：是否指定一方固定
        raw = args.extract_plain_text().strip()
        fixed_a = None
        fixed_b = None

        if raw:
            if raw.startswith("彰人"):
                name = raw[2:].strip()
                if not name:
                    await draw_cmd.finish("请指定彰人的派生名称，例如：抽派生 彰人 黑百合")
                if name not in akito_pool:
                    await draw_cmd.finish(f"彰人的派生池里没有「{name}」。")
                fixed_a = name
            elif raw.startswith("冬弥"):
                name = raw[2:].strip()
                if not name:
                    await draw_cmd.finish("请指定冬弥的派生名称，例如：抽派生 冬弥 王子冬")
                if name not in toya_pool:
                    await draw_cmd.finish(f"冬弥的派生池里没有「{name}」。")
                fixed_b = name
            else:
                await draw_cmd.finish("格式：抽派生 / 抽派生 彰人 XX / 抽派生 冬弥 XX")

        # 限频检查
        now = time.time()
        history = _DRAW_COOLDOWNS.get(user_id, [])
        history = [t for t in history if now - t < _DRAW_WINDOW]
        _DRAW_COOLDOWNS[user_id] = history

        if len(history) >= _DRAW_LIMIT:
            oldest = min(history)
            wait = int(_DRAW_WINDOW - (now - oldest))
            mins, secs = wait // 60, wait % 60
            await draw_cmd.finish(
                MessageSegment.reply(event.message_id)
                + f"30分钟内最多抽{_DRAW_LIMIT}次，你已用完次数，请在 {mins} 分 {secs} 秒后再试。"
            )

        a = fixed_a or random.choice(akito_pool)
        b = fixed_b or random.choice(toya_pool)
        history.append(now)
        _DRAW_COOLDOWNS[user_id] = history
        remaining = _DRAW_LIMIT - len(history)
        line1 = f"你抽到的派生是：{a}×{b}。"
        line2 = f"（30分钟内剩余 {remaining} 次）"

        await asyncio.sleep(random.uniform(0.4, 0.8))

        if _find_avatar("彰人", a) and _find_avatar("冬弥", b):
            img_bytes = _render_composite(a, b, line1, line2)
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))
        else:
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + line1 + line2)


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
