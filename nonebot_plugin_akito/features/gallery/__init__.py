"""图库引擎：本地图片随机抽取、手动 / 自动存图、主动发图、图库清单渲染。"""

from __future__ import annotations

import base64
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
import random
import time

import aiohttp
from nonebot import on_command, on_message
from nonebot.adapters import Event, Message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.adapters.onebot.v11 import Message as OB11Message
from nonebot.log import logger
from nonebot.params import CommandArg
from nonebot_plugin_htmlrender import html_to_pic
from PIL import Image as PILImage

from ...core import (
    GROUP_IMAGE_PERMISSIONS,
    IMAGE_BASE_PATH,
    REACTIONS_DB,
    call_deepseek_api,
    check_img_permission,
    get_base_persona,
    get_memory_key,
    get_user_memory,
    grant_safety_pass,
    sleep_block,
)

# ==============================================================================
# 模块 8：相册图库引擎 (IMAGE & GALLERY SYSTEM)
# ==============================================================================

IMAGE_CATEGORIES = ("toya", "self", "food", "groupmate", "vbs", "meme")
ITEMS_PER_PAGE = 30


def _match_category(text: str, rules: tuple[tuple[str, tuple[str, ...]], ...], default: str = "") -> str:
    """Match the first category whose keyword appears in the input text."""
    for category, keywords in rules:
        if any(keyword in text for keyword in keywords):
            return category
    return default


def _resolve_save_category_and_reply(
    text: str,
    replies_db: dict,
    chooser: Callable[[list[str]], str] = random.choice,
) -> tuple[str, str]:
    """Resolve which category a manual save request targets and pick its reply."""
    defaults = {
        "toya": ["……哦，谢了。"],
        "self": ["……发这个干嘛。"],
        "food": ["……看起来还行。"],
        "groupmate": ["又在说什么傻话？"],
        "vbs": ["……哼。"],
        "meme": ["……啧。"],
    }
    category = _match_category(
        text,
        (
            ("toya", ("冬弥", "搭档", "toya", "老婆")),
            ("self", ("你自己", "彰人", "自拍", "akito")),
            ("food", ("松饼", "吃的", "蛋糕", "甜点")),
            ("groupmate", ("群友",)),
            ("vbs", ("合照", "vbs", "队友")),
            ("meme", ("表情", "梗图", "meme")),
        ),
    )
    if not category:
        return "", ""
    return category, chooser(replies_db.get(category, defaults[category]))


def _build_collect_session_key(group_id: int | None, user_id: str) -> str:
    """Build the collecting session key for a group or private chat."""
    return f"group_{group_id}" if group_id else f"private_{user_id}"


def _resolve_collect_category(text: str) -> str:
    """Resolve the target category for collect mode, defaulting to toya."""
    return _match_category(
        text,
        (
            ("self", ("你自己", "彰人", "自拍", "akito")),
            ("food", ("松饼", "吃的", "蛋糕", "甜点")),
            ("groupmate", ("群友",)),
            ("vbs", ("合照", "vbs", "队友")),
            ("meme", ("表情", "meme", "梗图")),
        ),
        default="toya",
    )


def _resolve_send_image_request(
    text: str,
    allowed_categories: list[str],
    is_wl2_active: bool,
    chooser: Callable[[list[str]], str] = random.choice,
) -> tuple[str, str]:
    """Resolve the send-image category and prompt hint from user input."""
    explicit_rules = (
        ("toya", ("冬弥", "搭档", "toya", "老婆"), "表现：嘴上说'为什么要给你看'，但还是发了。"),
        ("self", ("你自己", "自拍", "彰人", "akito"), "表现：稍微有点自恋但又装作不在意。"),
        ("groupmate", ("群友",), ""),
        ("food", ("松饼", "吃的", "蛋糕", "甜点"), "发一张探店图并评价。"),
        ("vbs", ("合照", "vbs", "队友"), "发一张大家的日常。"),
        ("meme", ("表情", "梗图", "meme"), "随便发一张手机里存的表情。"),
    )
    for category, keywords, prompt_hint in explicit_rules:
        if any(keyword in text for keyword in keywords):
            return category, prompt_hint

    if "all" in allowed_categories:
        category = "self" if is_wl2_active else chooser(["toya", "self"])
    else:
        valid_choices = [category for category in allowed_categories if category in IMAGE_CATEGORIES]
        if is_wl2_active:
            valid_choices = [category for category in valid_choices if category not in {"toya", "vbs"}]
        if not valid_choices:
            return "", ""
        category = chooser(valid_choices)

    return category, "用户只说了看看。随机发一张，并问他想干嘛。"


def _resolve_gallery_category(text: str) -> str:
    """Resolve the category named in a gallery list request."""
    return _match_category(
        text,
        (
            ("toya", ("冬弥", "搭档", "toya")),
            ("self", ("你", "彰人", "自拍", "self", "akito")),
            ("food", ("吃", "食", "food", "松饼", "蛋糕", "甜点")),
            ("groupmate", ("群友", "groupmate")),
            ("vbs", ("合照", "vbs", "队友")),
            ("meme", ("表情", "meme", "梗图")),
        ),
    )


def _paginate_gallery(total_files: int, requested_page: int, items_per_page: int) -> tuple[int, int, int, int]:
    """Clamp the requested page and return (page, total_pages, start, end)."""
    total_pages = max(1, (total_files + items_per_page - 1) // items_per_page)
    page = max(1, min(requested_page, total_pages))
    start = (page - 1) * items_per_page
    end = page * items_per_page
    return page, total_pages, start, end

def get_random_local_image(category: str) -> Path | None:
    """从某分类图库随机返回一张有效图片路径；目录不存在则创建后返回 None。"""
    folder = IMAGE_BASE_PATH / category
    if not folder.exists():
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.debug(f"📁 创建图库目录 {category} 失败: {e}")
        return None
    images = list(folder.glob("*.jpg")) + list(folder.glob("*.png")) + list(folder.glob("*.gif")) + list(folder.glob("*.jpeg"))
    valid_images = [img for img in images if img.stat().st_size > 0]
    return random.choice(valid_images) if valid_images else None

# --- 1. 手动存图 ---
save_img_cmd = on_message(priority=6, block=False)
@save_img_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    group_id = getattr(event, 'group_id', None)
    if group_id and group_id not in GROUP_IMAGE_PERMISSIONS: return
    text = event.get_plaintext().strip()
    if not any(k in text for k in ["存", "收下", "投喂", "增加"]): return

    result = sleep_block("sleep_save_img", silent_chance=0.8,
                         fallback="……明天再存……zzZ")
    if result is None:
        return
    if result:
        grant_safety_pass(5)
        await bot.send(event=event, message=result)
        return

    img_url = ""
    for seg in event.message:
        if seg.type == "image": img_url = seg.data.get("url"); break
    if not img_url and event.reply and event.reply.message:
        for seg in event.reply.message:
            if seg.type == "image": img_url = seg.data.get("url"); break
    if not img_url: return

    replies_db = REACTIONS_DB.get("save_img_replies", {})
    category, save_msg = _resolve_save_category_and_reply(text, replies_db)
    if not category: return

    try:
        save_dir = IMAGE_BASE_PATH / category
        save_dir.mkdir(parents=True, exist_ok=True)
        file_name = f"{int(time.time())}_{random.randint(100, 999)}.jpg"
        async with aiohttp.ClientSession() as session, session.get(img_url) as resp:
            if resp.status == 200:
                with open(save_dir / file_name, "wb") as f: f.write(await resp.read())
                grant_safety_pass(5)
                await bot.send(event=event, message=save_msg)
    except Exception as e:
        logger.debug(f"💾 手动存图失败: {e}")

# --- 2. 自动进货模式 ---
COLLECTING_MODE = {}
collect_cmd = on_command("开始进货", aliases={"开始收图", "停止进货", "停止收图"}, priority=5, block=True)
@collect_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    group_id, user_id = getattr(event, 'group_id', None), event.get_user_id()
    if group_id:
        session_key = _build_collect_session_key(group_id, user_id)
        if group_id not in GROUP_IMAGE_PERMISSIONS: return
    else: session_key = _build_collect_session_key(group_id, user_id)

    text = event.get_plaintext()
    if "停止" in text or "结束" in text:
        if session_key in COLLECTING_MODE:
            del COLLECTING_MODE[session_key]
            await collect_cmd.finish("（合上相册）……收工。刚才发的图都存好了。")
        else: await collect_cmd.finish("哦，行。")

    target = args.extract_plain_text().strip()
    category = _resolve_collect_category(target)

    if group_id and not check_img_permission(group_id, category):
        await collect_cmd.finish("（皱眉）……这是什么图。")
        return

    COLLECTING_MODE[session_key] = category
    await collect_cmd.finish(f"""（拿出手机准备好）……行，发吧。现在开始自动存【{category}】的图。\n（发完记得说"停止进货"）""")

auto_save_monitor = on_message(priority=7, block=False)
@auto_save_monitor.handle()
async def _(bot: Bot, event: Event):
    group_id, user_id = getattr(event, 'group_id', None), event.get_user_id()
    session_key = _build_collect_session_key(group_id, user_id)
    if session_key not in COLLECTING_MODE: return
    if any(k in event.get_plaintext().strip() for k in ["存", "收下", "投喂", "增加"]): return

    img_urls = []
    try:
        for seg in event.get_message():
            if seg.type == "image": img_urls.append(seg.data.get("url"))
    except Exception as e:
        logger.debug(f"📥 提取图片 URL 失败: {e}")
    if not img_urls: return

    category = COLLECTING_MODE[session_key]
    count = 0
    try:
        save_dir = IMAGE_BASE_PATH / category
        save_dir.mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession() as session:
            for url in img_urls:
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            with open(save_dir / f"{int(time.time())}_{random.randint(1000, 9999)}.jpg", "wb") as f:
                                f.write(await resp.read())
                            count += 1
                except Exception as e:
                    logger.debug(f"📥 自动存图下载失败: {e}")
    except Exception as e:
        logger.debug(f"📥 自动进货批次失败: {e}")

    if count > 0 and random.random() < 0.3:
        grant_safety_pass(5)
        await bot.send(event=event, message="👌")

# --- 3. 主动发图 ---
send_img_cmd = on_command("看你的", aliases={"发张", "来张"}, priority=5, block=True)
@send_img_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    result = sleep_block("sleep_replies_img", silent_chance=0.0, fallback="……zzZ")
    if result:
        grant_safety_pass(5)
        await send_img_cmd.finish(result)

    group_id = getattr(event, 'group_id', None)
    if group_id and group_id not in GROUP_IMAGE_PERMISSIONS: return

    mem = get_user_memory(get_memory_key(event))
    is_wl2_active = any(item.get("id") == "WL2" for item in mem.get("temp_implants", []))

    text = args.extract_plain_text().strip()
    allowed = GROUP_IMAGE_PERMISSIONS.get(group_id, [])
    category, prompt_hint = _resolve_send_image_request(text, allowed, is_wl2_active)
    if not category:
        await send_img_cmd.finish("（摊手）……这儿没什么能发的。")

    if is_wl2_active and category in ["toya", "vbs"]:
        grant_safety_pass(5)
        await send_img_cmd.finish(random.choice([
            "……手机里没那种照片了。早就删了。",
            "（直接锁上手机屏幕）……没有可以给你看的东西。",
            "（瞥了一眼）……没有这种图可以发。"
        ]))

    if not check_img_permission(group_id, category):
        await send_img_cmd.finish("（瞥了一眼）……没有这种图可以发。" if category in ["toya", "self"] else "（摆手）……不想发这个。")

    img_path = get_random_local_image(category)
    if not img_path: await send_img_cmd.finish(f"（翻了翻相册）……啧，相册里还没存'{category}'的照片。你先发给我几张？")

    try:
        if category == "groupmate":
            caption = ""
        else:
            cat_cn_map = {
                "toya": "搭档(青柳冬弥)的照片",
                "self": "自己的帅气自拍/单人照",
                "food": "刚吃过的甜点/松饼等美食照",
                "vbs": "VBS小队成员的合照或日常",
                "meme": "手机里存的搞笑表情包/梗图"
            }
            cat_cn = cat_cn_map.get(category, "照片")
            random_angles = REACTIONS_DB.get("send_img_angles") or ["语气切入点：随意的发言，像是随手丢过去的。"]
            current_angle = random.choice(random_angles)

            img_prompt = f"""
            {get_base_persona()}
            【当前动作】：你从手机相册里翻出了一张【{cat_cn}】发送给对方。
            【导演要求】：{prompt_hint}
            【随机微表情】：{current_angle}

            【强制约束】：
            1. 根据发图类型写配文，如果是发食物或自拍的话不需要强扯到冬弥身上！
            2. 只输出发图时附带的一句简短配文（20字以内），符合男高中生口吻。
            3. 纯文本，无引号，无动作描写。
            4. ⚠️不可视警告：你不知道图里具体是啥！绝对不能描写具体物体（如猫狗风景）！用万能代词模糊评价！
            5. 🚫【降重警告】：严禁使用"喏"、"给你"、"这张图"、"看看"等老套开头！每句话必须像第一次说一样自然，强迫自己使用全新、多变的句式！
            """
            caption = await call_deepseek_api([{"role": "user", "content": img_prompt}])
    except Exception as e:
        logger.error(f"配文生成失败: {e}")
        caption = "喏，你要的照片。"

    final_msg = None
    try:
        with open(img_path, "rb") as f:
            base64_url = f"base64://{base64.b64encode(f.read()).decode()}"
        if caption:
            final_msg = OB11Message(caption.strip() + "\n") + MessageSegment.image(base64_url)
        else:
            final_msg = MessageSegment.image(base64_url)
    except Exception: await send_img_cmd.finish("（划手机）……啧，图片加载失败了。")

    if final_msg:
        grant_safety_pass(5)
        await send_img_cmd.finish(final_msg)

# --- 4. 相册清单 ---
def get_file_list_safe(category: str) -> list[Path] | None:
    """返回某分类图库的全部图片路径（按修改时间倒序）；目录不存在返回 None。"""
    folder = IMAGE_BASE_PATH / category
    if not folder.exists(): return None
    files = list(folder.glob("*.jpg")) + list(folder.glob("*.png")) + list(folder.glob("*.gif")) + list(folder.glob("*.jpeg"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files

def get_thumbnail_safe(file_path: Path) -> str:
    """将图片压成 140px 缩略图并返回 base64 字符串；失败返回空串。"""
    try:
        with PILImage.open(file_path) as img:
            if img.mode in ("RGBA", "P"): img = img.convert("RGB")
            img.thumbnail((140, 140))
            buffer = BytesIO()
            img.save(buffer, format="JPEG", quality=50)
            return base64.b64encode(buffer.getvalue()).decode()
    except Exception: return ""

gallery_cmd = on_command("图库清单", aliases={"查看图库", "库存", "相册"}, priority=5, block=True)
@gallery_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    result = sleep_block("sleep_gallery_list", silent_chance=0.0,
                         fallback="💤 (小彰正在睡觉，请早上6点后再来...)")
    if result:
        await gallery_cmd.finish(result)
        return

    if isinstance(event, GroupMessageEvent):
        gid = event.group_id
        if gid not in GROUP_IMAGE_PERMISSIONS: return
        allowed_cats = GROUP_IMAGE_PERMISSIONS[gid]
    else: return

    params = args.extract_plain_text().strip().split()
    cat_raw = params[0] if len(params) > 0 else ""
    page = int(params[1]) if len(params) > 1 and params[1].isdigit() else 1

    target_cat = _resolve_gallery_category(cat_raw)
    if not target_cat: await gallery_cmd.finish("请指定分类！例如：图库清单 表情")
    if "all" not in allowed_cats and target_cat not in allowed_cats: await gallery_cmd.finish(f"🚫 本群没有查看【{target_cat}】的权限。")

    all_files = get_file_list_safe(target_cat)
    if not all_files: await gallery_cmd.finish(f"📂 【{target_cat}】相册是空的！")

    total_files = len(all_files)
    page, total_pages, start, end = _paginate_gallery(total_files, page, ITEMS_PER_PAGE)
    current_files = all_files[start:end]

    html = f"""
    <html>
    <head>
        <style>
            body {{ font-family: "Microsoft YaHei", sans-serif; background-color: #f3f4f6; padding: 10px; }}
            .grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 5px; }}
            .card {{ background: white; border-radius: 4px; padding: 4px; text-align: center; }}
            .img-box {{ width: 100%; height: 80px; background: #eee; display: flex; align-items: center; justify-content: center; overflow: hidden; }}
            img {{ width: 100%; height: 100%; object-fit: cover; }}
            .tag {{ background: #ff9f43; color: white; padding: 0 4px; border-radius: 4px; font-size: 10px; display: inline-block; }}
        </style>
    </head>
    <body>
        <div style="text-align:center; margin-bottom:10px;">
            <b style="font-size:20px; color:#333;">📂 {target_cat} ({page}/{total_pages})</b><br>
            <span style="color:#888; font-size:12px;">共 {total_files} 张</span>
        </div>
        <div class="grid">
    """
    for i, f in enumerate(current_files):
        idx = (page-1)*ITEMS_PER_PAGE + i + 1
        _thumb = get_thumbnail_safe(f)
        src = f"data:image/jpeg;base64,{_thumb}" if _thumb else ""
        html += f'<div class="card"><div class="img-box"><img src="{src}"></div><div class="tag">#{idx}</div></div>'
    html += "</div></body></html>"

    try:
        pic = await html_to_pic(html, viewport={"width": 800, "height": 100})
        grant_safety_pass(5)
        await gallery_cmd.finish(MessageSegment.image(pic))
    except Exception as e:
        logger.error(f"渲染失败: {e}")
