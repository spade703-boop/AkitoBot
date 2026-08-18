"""Original PIL fallbacks for random_paro draw results."""

from __future__ import annotations

import io

from PIL import Image, ImageDraw

from .assets import (
    AVATAR_WIDTH,
    FONT_BOLD_SIZE,
    FONT_SIZE,
    MIN_CANVAS_W,
    ROW_H,
    TEXT_BOTTOM_PAD,
    TEXT_TOP_GAP,
)
from .assets import (
    find_avatar as _find_avatar,
)
from .assets import (
    load_font as _load_font,
)
from .assets import (
    load_foxbun_image as _load_foxbun_image,
)
from .assets import (
    load_foxrabbit_image as _load_foxrabbit_image,
)

SEQS = ["①", "②", "③"]


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


def _measure_line_width(line) -> float:
    """预测量一行的总宽度（不依赖 draw 对象）。"""
    fn = _load_font(FONT_SIZE)
    fb = _load_font(FONT_BOLD_SIZE)
    if isinstance(line, list):
        return sum((fb if bold else fn).getbbox(txt)[2] for txt, _, bold in line)
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

    fn = _load_font(FONT_SIZE)  # 20px
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
            egg_line_w = fb.getbbox("快来做")[2] + parts_w + fb.getbbox("、")[2] * sep_count + fb.getbbox("的饭吧！")[2]
            egg_summary_w = max(fb.getbbox("恭喜你是被选中的彰冬姐！")[2], egg_line_w)
        else:
            egg_summary_w = fb.getbbox("快来做狐兔饭吧！")[2]

    rem_w = fn.getbbox(f"（30分钟内剩余 {remaining} 次）")[2]

    w = max(MIN_CANVAS_W, int(max_line_w) + 48, int(egg_summary_w) + 48 if egg_summary_w else 0, int(rem_w) + 48)

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
                segs = [
                    (pre, "#000000", False),
                    ("一对眼熟的", "#000000", False),
                    ("狐", "#FF7722", False),
                    ("兔", "#0077DD", False),
                    ("出现在了这里……", "#000000", False),
                ]
            elif fox_type == "foxbun":
                segs = [
                    (pre, "#000000", False),
                    ("发现了一对正在贴贴的", "#000000", False),
                    ("狐", "#FF7722", False),
                    ("兔", "#0077DD", False),
                    ("！", "#000000", False),
                ]
            elif fox_type == "fox":
                segs = [
                    (pre, "#000000", False),
                    ("一只得意的", "#000000", False),
                    ("狐狸", "#FF7722", False),
                    ("赶走了这里的派生。", "#000000", False),
                ]
            else:
                segs = [
                    (pre, "#000000", False),
                    ("一只圆圆的", "#000000", False),
                    ("兔子", "#0077DD", False),
                    ("挡住了这里的派生。", "#000000", False),
                ]
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
            draw.text((x, y), seq + " ", font=fn, fill="#000000", anchor="la")
            x += seq_w
            draw.text((x, y), PREFIX, font=fn, fill="#000000", anchor="la")
            x += pre_w
            draw.text((x, y), a, font=fn, fill="#FF7722", anchor="la")
            x += a_w
            draw.text((x, y), "×", font=fn, fill="#000000", anchor="la")
            x += x_w_val
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


render_pool_image = _render_pool_image
render_text_only = _render_text_only
render_composite = _render_composite
render_multi = _render_multi

__all__ = [
    "SEQS",
    "render_pool_image",
    "render_text_only",
    "render_composite",
    "render_multi",
]
