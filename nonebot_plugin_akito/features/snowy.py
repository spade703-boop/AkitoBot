import json
import os
import ssl
from pathlib import Path
import httpx
import io
import asyncio
import time
from datetime import datetime, timezone, timedelta

_TZ_CN = timezone(timedelta(hours=8))  # 北京时间，独立定义避免循环导入
from nonebot import on_command
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from PIL import Image, ImageDraw, ImageFont

# ================= 配置 =================
CACHE_FILE = Path("data/pjsk_event_cache.json")

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.minimum_version = ssl.TLSVersion.TLSv1_2

# 多 UA 轮换，降低被风控概率
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0"
]

# =======================================

# ================= 活动缓存管理 =================
def load_event_cache():
    """加载缓存的活动信息"""
    if not CACHE_FILE.exists():
        return {"event_id": None, "event_name": "", "updated_at": 0}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"⚠️ [Snowy] 读取活动缓存失败: {e}")
        return {"event_id": None, "event_name": "", "updated_at": 0}

def save_event_cache(event_id, event_name):
    """保存活动信息到本地"""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    cache = {
        "event_id": event_id,
        "event_name": event_name,
        "updated_at": time.time()
    }
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=4, ensure_ascii=False)
    logger.info(f"[Snowy] 已更新活动缓存: {event_id} - {event_name}")

# ================= 健壮请求工具 =================
async def safe_request(client, url, max_retries=3, retry_delay=3):
    """
    健壮请求：支持 522/525 重试，自动轮换 UA
    """
    for attempt in range(max_retries):
        try:
            # 轮换 User-Agent
            headers = {
                "User-Agent": USER_AGENTS[attempt % len(USER_AGENTS)],
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "DNT": "1",
                "Connection": "keep-alive"
            }

            resp = await client.get(url, headers=headers)

            # 522/525 特殊处理（Cloudflare 连接超时）
            if resp.status_code in (522, 525):
                logger.warning(f"[Snowy] Attempt {attempt+1} Cloudflare {resp.status_code}: {url}")
                if attempt < max_retries - 1:
                    wait = retry_delay * (2 ** attempt)
                    logger.info(f"[Snowy] 等待 {wait}s 后重试...")
                    await asyncio.sleep(wait)
                    continue
                return False, None, f"Cloudflare {resp.status_code}"

            if resp.status_code != 200:
                return False, None, f"HTTP {resp.status_code}"

            # 检查是否是 JSON
            content_type = resp.headers.get("content-type", "")
            if "application/json" not in content_type:
                # 尝试解析，可能是未声明 Content-Type
                try:
                    data = resp.json()
                    return True, data, ""
                except:
                    return False, None, f"非JSON响应: {content_type}"

            try:
                data = resp.json()
                return True, data, ""
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"[Snowy] Attempt {attempt+1} 解析错误: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    continue
                return False, None, f"解析失败: {e}"

        except httpx.TimeoutException:
            logger.warning(f"[Snowy] Attempt {attempt+1} 请求超时")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay * (2 ** attempt))
                continue
            return False, None, "请求超时"
        except Exception as e:
            logger.error(f"[Snowy] Attempt {attempt+1} 异常: {e}")
            if attempt < max_retries - 1:
                await asyncio.sleep(retry_delay)
                continue
            return False, None, str(e)

    return False, None, "达到最大重试次数"

# ================= 智能活动ID获取 =================
async def get_current_event_id(client):
    """
    获取当前活动ID，带缓存容错
    返回: (event_id, event_name, is_from_cache)
    """
    cache = load_event_cache()

    # 1. 尝试从 API 获取最新活动
    success, events, error = await safe_request(
        client,
        "https://rk.exmeaning.com/public/events?region=cn",
        max_retries=3,
        retry_delay=2
    )

    if success and isinstance(events, list):
        for ev in events:
            if isinstance(ev, dict) and ev.get("status") == "active":
                event_id = ev.get("event_id")
                event_name = ev.get("name", "未知")

                # 检测活动交替
                if cache["event_id"] != event_id:
                    logger.info(f"[Snowy] 活动交替检测: {cache['event_id']} -> {event_id}")
                    save_event_cache(event_id, event_name)
                return event_id, event_name, False

    # 2. API 失败，使用缓存
    if cache["event_id"]:
        cache_age = time.time() - cache["updated_at"]
        logger.warning(f"[Snowy] API 不可用，使用缓存 ID: {cache['event_id']} (缓存年龄: {cache_age/3600:.1f}小时)")
        return cache["event_id"], cache["event_name"], True

    # 3. 彻底失败
    return None, None, False

# ================= 数据获取 =================
async def fetch_predict_data(client, event_id):
    """获取预测数据，失败返回空列表"""
    url = f"https://rk.exmeaning.com/public/event/{event_id}/latest?region=cn"
    success, data, error = await safe_request(client, url, max_retries=2)

    if not success:
        logger.error(f"[Snowy] 预测数据获取失败: {error}")
        return [], None

    if not isinstance(data, dict):
        return [], None

    items = data.get("items", [])
    if not isinstance(items, list):
        return [], None

    return items, data.get("updated_at")

async def fetch_live_data(client):
    """获取实况数据（容错）"""
    url = "https://rks.exmeaning.com/api/public/cn/latest"
    success, data, error = await safe_request(client, url, max_retries=2)

    if not success:
        logger.warning(f"[Snowy] 实况数据获取失败（非致命）: {error}")
        return {}, None, False

    if not isinstance(data, dict):
        return {}, None, False

    rankings = data.get("rankings", [])
    if not isinstance(rankings, list):
        return {}, None, False

    rank_map = {item["rank"]: item for item in rankings if isinstance(item, dict) and "rank" in item}

    # 处理时间戳（毫秒级）
    raw_ts = data.get("updated_at")
    latest_ts = None
    if isinstance(raw_ts, (int, float)):
        latest_ts = raw_ts / 1000.0

    return rank_map, latest_ts, True

def merge_data(predict_items, live_map):
    """合并数据"""
    merged = []
    for p_item in predict_items:
        if not isinstance(p_item, dict):
            continue

        rank = p_item.get("rank")
        if rank is None:
            continue

        live_item = live_map.get(rank)
        score = live_item.get("score") if isinstance(live_item, dict) else None
        prediction = p_item.get("prediction") or 0

        merged.append({
            "rank": rank,
            "score": score,
            "prediction": prediction
        })
    return merged

# ================= 指令处理 =================
predict_cmd = on_command("sn预测", aliases={"cn预测"}, priority=5, block=True)
@predict_cmd.handle()
async def _(event: MessageEvent):
    await predict_cmd.send("小彰正在打开PJSK查看榜线……")

    limits = httpx.Limits(max_keepalive_connections=3, max_connections=5)

    try:
        async with httpx.AsyncClient(
            timeout=30.0,
            limits=limits,
            verify=SSL_CONTEXT
        ) as client:

            # 1. 获取活动ID（带缓存容错）
            event_id, event_name, is_cached = await get_current_event_id(client)

            if not event_id:
                await predict_cmd.finish("无法获取活动信息，且本地无缓存，请稍后再试。")
                return

            # 2. 获取预测数据（核心数据）
            predict_items, predict_updated_at = await fetch_predict_data(client, event_id)

            # 3. 如果预测数据为空，可能是活动结束了或 ID 过期
            if not predict_items:
                if is_cached:
                    # 可能是缓存过期，尝试清除缓存再试一次
                    logger.info("[Snowy] 缓存可能过期，尝试清除后重试...")
                    if CACHE_FILE.exists():
                        CACHE_FILE.unlink()
                    # 重试一次
                    event_id, event_name, _ = await get_current_event_id(client)
                    if event_id:
                        predict_items, predict_updated_at = await fetch_predict_data(client, event_id)

                if not predict_items:
                    await predict_cmd.finish("当前无活动数据，可能是活动已结束或预测网维护中。")
                    return

            # 4. 获取实况数据（可选）
            live_map, live_ts, live_ok = await fetch_live_data(client)

            # 5. 合并数据
            merged_items = merge_data(predict_items, live_map)

            # 6. 时间戳处理
            predict_ts = None
            if predict_updated_at:
                try:
                    dt = datetime.fromisoformat(predict_updated_at.replace("Z", "+00:00"))
                    predict_ts = dt.timestamp()
                except Exception as e:
                    logger.warning(f"[Snowy] ⚠️ 时间戳解析失败: {e}")

            # 7. 渲染
            event_info = {
                "id": event_id,
                "name": event_name,
                # 从预测数据或缓存估算时间（如果不准确可以去掉）
                "start": 0,  # 预测API没有start/end，可以后续补充
                "end": 0
            }
            times = {
                "predict_ts": predict_ts,
                "latest_ts": live_ts
            }

            # 如果用了缓存且没有活动结束时间，显示提示
            warning_text = ""
            if is_cached:
                warning_text = "（使用缓存数据）"

            img_bytes = render_predict_image(event_info, merged_items, times, live_ok, warning_text)
            await predict_cmd.finish(MessageSegment.image(img_bytes))

    except FinishedException:
        pass
    except Exception as e:
        await predict_cmd.finish(f"系统错误：[{type(e).__name__}] {str(e)}")

# ================= 工具函数 =================
def format_w(value, placeholder="-"):
    if not value or value == 0:
        return placeholder
    return f"{value / 10000:.4f}w"

def get_relative_time(past_timestamp):
    if not past_timestamp:
        return "-"
    now = time.time()
    diff = int(now - past_timestamp)
    if diff < 0:
        diff = 0
    if diff < 60:
        return f"{diff}秒前"
    mins = diff // 60
    if mins < 60:
        return f"{mins}分钟前"
    hours = mins // 60
    if hours < 24:
        return f"{hours}小时前"
    return f"{hours//24}天前"

# ================= 渲染引擎（带缓存提示） =================
def render_predict_image(event_info, merged_items, times, live_data_available, warning_text=""):
    width = 750
    row_height = 46
    height = 140 + 50 + len(merged_items) * row_height + 100 + 60

    img = Image.new('RGB', (width, height), color='#f7eef7')
    draw = ImageDraw.Draw(img)

    try:
        current_dir = os.path.dirname(os.path.abspath(__file__))
        normal_font_path = os.path.join(current_dir, "font.ttf")
        bold_font_path = os.path.join(current_dir, "msyhbd.ttc")

        font_title = ImageFont.truetype(bold_font_path, 30)
        font_info = ImageFont.truetype(normal_font_path, 20)
        font_footer = ImageFont.truetype(normal_font_path, 16)
        font_row_normal = ImageFont.truetype(normal_font_path, 24)
        font_row_bold = ImageFont.truetype(bold_font_path, 24)
    except Exception as e:
        logger.warning(f"[Snowy] ⚠️ 字体加载失败: {e}")
        font_title = font_info = font_footer = font_row_normal = font_row_bold = ImageFont.load_default()

    # --- 顶部信息 ---
    title_text = f"【CN-{event_info['id']}】{event_info['name']}"
    if warning_text:
        title_text += f" {warning_text}"
    draw.text((40, 25), title_text, font=font_title, fill='#000000')

    # 时间显示（如果没有活动起止时间，显示当前时间）
    now_str = datetime.now(_TZ_CN).strftime('%Y-%m-%d %H:%M')
    draw.text((40, 70), f"查询时间: {now_str}", font=font_info, fill='#000000')

    # 倒计时（简化版，因为预测API没有start/end）
    draw.text((40, 100), "距离活动结束还有: 请参考游戏内显示", font=font_info, fill='#666666')

    # --- 表格 ---
    table_x1, table_y1 = 30, 140
    table_x2, table_y2 = width - 30, height - 40

    try:
        draw.rounded_rectangle([(table_x1, table_y1), (table_x2, table_y2)], radius=15, fill='#ffffff', outline='#e8dced', width=2)
    except AttributeError:
        draw.rectangle([(table_x1, table_y1), (table_x2, table_y2)], fill='#ffffff', outline='#e8dced', width=2)

    header_y = table_y1 + 15
    col1_x, col2_x, col3_x = 130, 360, 590

    draw.text((col1_x, header_y), "排名", font=font_row_bold, fill='#000000', anchor="ma")
    draw.text((col2_x, header_y), "当前榜线", font=font_row_bold, fill='#000000', anchor="ma")
    draw.text((col3_x, header_y), "Moesekai预测", font=font_row_bold, fill='#000000', anchor="ma")
    draw.line([(table_x1+2, table_y1+50), (table_x2-2, table_y1+50)], fill='#e8dced', width=1)

    current_y = table_y1 + 50
    for i, item in enumerate(merged_items):
        if i % 2 == 1:
            draw.rectangle([(table_x1+2, current_y), (table_x2-2, current_y + row_height)], fill='#fcf5fa')

        rank_str = f"{item['rank']:,}"
        score_str = format_w(item['score']) if item['score'] else "-"
        pred_str = format_w(item['prediction'])

        text_y = current_y + 10
        draw.text((col1_x, text_y), rank_str, font=font_row_bold, fill='#333333', anchor="ma")
        score_color = '#999999' if score_str == "-" else '#333333'
        draw.text((col2_x, text_y), score_str, font=font_row_normal, fill=score_color, anchor="ma")
        draw.text((col3_x, text_y), pred_str, font=font_row_normal, fill='#333333', anchor="ma")
        draw.line([(table_x1+2, current_y + row_height), (table_x2-2, current_y + row_height)], fill='#f0e6f2', width=1)
        current_y += row_height

    # --- 底部时间 ---
    pred_rel_time = get_relative_time(times['predict_ts'])
    real_rel_time = get_relative_time(times['latest_ts']) if live_data_available else "-"

    draw.rectangle([(table_x1+2, current_y), (table_x2-2, current_y + row_height)], fill='#fcf5fa')
    draw.text((col1_x, current_y + 10), "预测时间", font=font_row_bold, fill='#000000', anchor="ma")
    draw.text((col2_x, current_y + 10), "-", font=font_row_normal, fill='#333333', anchor="ma")
    draw.text((col3_x, current_y + 10), pred_rel_time, font=font_row_normal, fill='#333333', anchor="ma")
    draw.line([(table_x1+2, current_y + row_height), (table_x2-2, current_y + row_height)], fill='#f0e6f2', width=1)
    current_y += row_height

    draw.text((col1_x, current_y + 10), "获取时间", font=font_row_bold, fill='#000000', anchor="ma")
    time_color = '#999999' if real_rel_time == "-" else '#333333'
    draw.text((col2_x, current_y + 10), real_rel_time, font=font_row_normal, fill=time_color, anchor="ma")
    draw.text((col3_x, current_y + 10), pred_rel_time, font=font_row_normal, fill='#333333', anchor="ma")
    draw.text((width/2, height - 25), "Generated by 寒星，数据来源 Moesekai", font=font_footer, fill='#999999', anchor="ma")

    img_byte_arr = io.BytesIO()
    img.save(img_byte_arr, format='JPEG', quality=95)
    return img_byte_arr.getvalue()
