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

def _load_font(size: int):
    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        font_path = os.path.join(current_dir, "msyhbd.ttc")
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()


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

AVATAR_BASE = Path("data/images/paro_avatars")


def _find_avatar(character: str, name: str) -> Path | None:
    for ext in (".png", ".jpg", ".jpeg"):
        p = AVATAR_BASE / character / f"{name}{ext}"
        if p.exists():
            return p
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


# ==================== 多抽渲染 ====================

SEQS = ["①", "②", "③"]


def _render_multi(results: list, has_avatars: bool, remaining: int, nickname: str) -> bytes:
    """results: [(a, b, is_egg), ...]"""
    count = len(results)
    egg_indices = [i for i, (_, _, egg) in enumerate(results) if egg]

    avatar_size = 150
    gap = 4
    avatars_width = avatar_size * 2 + gap
    row_text_gap = 8
    result_gap = 10

    fn = _load_font(FONT_SIZE)       # 20px — 派生名称 / 普通文字
    fb = _load_font(FONT_BOLD_SIZE)  # 24px — 彩蛋汇总

    # --- 行宽：序号 + A × B ---
    def _result_line_width(aa, bb):
        prefix = fn.getbbox("① ")[2]
        a_w = fn.getbbox(aa)[2]
        b_w = fn.getbbox(bb)[2]
        x_w = fn.getbbox("×")[2]
        return prefix + a_w + x_w + b_w

    max_line_w = max(_result_line_width(a, b) for a, b, _ in results)

    # 彩蛋汇总行宽
    egg_summary_w = 0.0
    if egg_indices:
        parts_w = 0.0
        for idx in egg_indices:
            ea, eb, _ = results[idx]
            parts_w += fb.getbbox(ea)[2] + fb.getbbox("×")[2] + fb.getbbox(eb)[2]
        egg_line_w = (fb.getbbox("快来做")[2] + parts_w
                      + fb.getbbox("、")[2] * (len(egg_indices) - 1)
                      + fb.getbbox("的饭吧！")[2])
        egg_summary_w = max(fb.getbbox("恭喜你是被选中的彰冬姐！")[2], egg_line_w)

    rem_w = fn.getbbox(f"（30分钟内剩余 {remaining} 次）")[2]

    w = max(MIN_CANVAS_W, int(max_line_w) + 48,
            AVATAR_WIDTH if has_avatars else 0,
            int(egg_summary_w) + 48 if egg_summary_w else 0,
            int(rem_w) + 48)

    # --- 高度 ---
    title_h = ROW_H + 8
    result_h_per = (avatar_size + row_text_gap + ROW_H) if has_avatars else ROW_H
    egg_area_h = 0
    if egg_indices:
        egg_area_h = 12 + 2 * ROW_H
    rem_h = 8 + ROW_H
    height = TEXT_TOP_GAP + title_h + count * (result_h_per + result_gap) + egg_area_h + rem_h + TEXT_BOTTOM_PAD

    canvas = Image.new("RGB", (w, height), color="#ffffff")
    draw = ImageDraw.Draw(canvas)

    # --- 标题 ---
    y = TEXT_TOP_GAP
    draw.text((w // 2, y), "你抽取到的派生是：", font=fn, fill="#000000", anchor="ma")
    y += title_h

    # --- 每行结果：序号 + A × B（彰人橙 冬弥蓝）---
    for i, (a, b, is_egg) in enumerate(results):
        if has_avatars:
            avatars_x = (w - avatars_width) // 2
            for ch, name, x_off in [("彰人", a, avatars_x), ("冬弥", b, avatars_x + avatar_size + gap)]:
                path = _find_avatar(ch, name)
                if path:
                    im = Image.open(path).convert("RGB").resize((avatar_size, avatar_size), Image.LANCZOS)
                    canvas.paste(im, (x_off, y))
            y += avatar_size + row_text_gap

        seq = SEQS[i]
        seq_w = int(fn.getbbox(seq + " ")[2])
        a_w = int(fn.getbbox(a)[2])
        x_w_val = int(fn.getbbox("×")[2])
        b_w = int(fn.getbbox(b)[2])
        total_w = seq_w + a_w + x_w_val + b_w
        x = (w - total_w) // 2

        draw.text((x, y), seq + " ", font=fn, fill="#000000", anchor="la"); x += seq_w
        draw.text((x, y), a, font=fn, fill="#FF7722", anchor="la"); x += a_w
        draw.text((x, y), "×", font=fn, fill="#000000", anchor="la"); x += x_w_val
        draw.text((x, y), b, font=fn, fill="#0077DD", anchor="la")

        y += ROW_H + result_gap

    # --- 彩蛋汇总（24px，派生名着色）---
    if egg_indices:
        y += 12
        draw.text((w // 2, y), "恭喜你是被选中的彰冬姐！", font=fb, fill="#000000", anchor="ma")
        y += ROW_H

        # "快来做" + 着色派生对 + "的饭吧！"
        parts = [("快来做", "#000000")]
        for j, idx in enumerate(egg_indices):
            if j > 0:
                parts.append(("、", "#000000"))
            ea, eb, _ = results[idx]
            parts.append((ea, "#FF7722"))
            parts.append(("×", "#000000"))
            parts.append((eb, "#0077DD"))
        parts.append(("的饭吧！", "#000000"))

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

        # 解析参数：提取数字 token（1-3）+ 方向/名称
        raw = args.extract_plain_text().strip()
        count = 1
        directional = ""  # 剩余的方向+名称字符串
        if raw:
            tokens = raw.split()
            for i, t in enumerate(tokens):
                if t.isdigit() and 1 <= int(t) <= 3:
                    count = int(t)
                    directional = " ".join(tokens[:i] + tokens[i+1:])
                    break
            else:
                directional = raw

        fixed_a = None
        fixed_b = None
        if directional:
            dl = directional.lower()
            if dl.startswith("彰人"):
                name = directional[2:].strip()
                if not name:
                    await draw_cmd.finish("请指定彰人的派生名称，例如：抽派生 彰人 黑百合")
                match = _fuzzy_match(name, akito_pool)
                if not match:
                    await draw_cmd.finish(f"彰人的派生池里没有与「{name}」匹配的条目。")
                if isinstance(match, list):
                    await draw_cmd.finish(f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。")
                fixed_a = match
            elif dl.startswith("冬弥"):
                name = directional[2:].strip()
                if not name:
                    await draw_cmd.finish("请指定冬弥的派生名称，例如：抽派生 冬弥 王子冬")
                match = _fuzzy_match(name, toya_pool)
                if not match:
                    await draw_cmd.finish(f"冬弥的派生池里没有与「{name}」匹配的条目。")
                if isinstance(match, list):
                    await draw_cmd.finish(f"「{name}」匹配到多个条目：{' / '.join(match)}，请补充完整。")
                fixed_b = match
            else:
                await draw_cmd.finish("请指定要固定哪一方的派生，例如：抽派生 彰人 黑百合。\n彰冬不拆不逆，一方派生固定则另一方派生随机。")

        # 限频检查
        now = time.time()
        history = _DRAW_COOLDOWNS.get(user_id, [])
        history = [t for t in history if now - t < _DRAW_WINDOW]
        _DRAW_COOLDOWNS[user_id] = history
        remaining_before = _DRAW_LIMIT - len(history)

        if remaining_before < count:
            if remaining_before <= 0:
                oldest = min(history)
                wait = int(_DRAW_WINDOW - (now - oldest))
                mins, secs = wait // 60, wait % 60
                await draw_cmd.finish(
                    MessageSegment.reply(event.message_id)
                    + f"30分钟内最多抽{_DRAW_LIMIT}次，你已用完次数，请在 {mins} 分 {secs} 秒后再试。"
                )
            else:
                await draw_cmd.finish(
                    MessageSegment.reply(event.message_id)
                    + f"30分钟内仅剩 {remaining_before} 次，无法抽 {count} 次。"
                )

        nickname = event.sender.card or event.sender.nickname or f"用户{user_id}"

        # N 次独立抽取
        results = []
        for _ in range(count):
            a = fixed_a or random.choice(akito_pool)
            b = fixed_b or random.choice(toya_pool)
            is_egg = random.random() < _EASTER_EGG_RATE
            results.append((a, b, is_egg))

        for _ in range(count):
            history.append(now)
        _DRAW_COOLDOWNS[user_id] = history
        remaining = _DRAW_LIMIT - len(history)

        await asyncio.sleep(random.uniform(0.4, 0.8))

        if count == 1:
            # 单抽 — 保持原有输出
            a, b, is_egg = results[0]
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
            # 多抽
            has_av = all(_find_avatar("彰人", a) and _find_avatar("冬弥", b) for a, b, _ in results)
            img_bytes = _render_multi(results, has_av, remaining, nickname)
            await draw_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img_bytes))


# ==================== 测试做饭 ====================

test_egg_cmd = on_command("test做饭", priority=5, block=True)


@test_egg_cmd.handle()
async def _(event: Event):
    if str(event.get_user_id()) != SUPERUSER_QQ:
        return
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
    # 固定抽取 3 次，其中恰好前 2 个为彩蛋
    results = [
        (pool_a[0], pool_b[0], True),
        (pool_a[1], pool_b[1], True),
        (pool_a[0], pool_b[1], False),
    ]
    has_av = all(_find_avatar("彰人", a) and _find_avatar("冬弥", b) for a, b, _ in results)
    img = _render_multi(results, has_av, remaining=1, nickname=nickname)
    await test_multi_cmd.finish(MessageSegment.reply(event.message_id) + MessageSegment.image(img))


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
