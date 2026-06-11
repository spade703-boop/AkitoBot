"""外部 API 封装：DeepSeek 对话 / Agent、Tavily 搜索、智谱 GLM-4V 图像识别，均带超时与降级处理；
另提供 LLM JSON 输出的提取 / 救援工具（chat 与 impression 共用的单一真相源）。"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from io import BytesIO
import json
from pathlib import Path
import re
from typing import Any

import aiohttp
from nonebot.log import logger
from PIL import Image as PILImage

from . import SILICONFLOW_API_KEY, TAVILY_API_KEY, ZHIPU_API_KEY, client, embedding_client, vision_client

# ── LLM JSON 输出的提取与救援（chat.py / impression.py 共用） ──────────────────

_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")


def extract_json_block(raw: str) -> str:
    """从 LLM 原始返回中提取最外层 ``{...}`` 片段；无匹配时原样返回（交由调用方的解析失败兜底处理）。"""
    match = _JSON_BLOCK_RE.search(raw)
    return match.group(0) if match else raw


def rescue_field(raw: str, *fields: str) -> str | None:
    """从残缺 JSON 文本中正则抠出第一个命中的字符串字段值；无匹配返回 None。

    用于 ``json.loads`` 失败后的降级救援（典型原因：字段值内裸引号未转义、输出被截断）。
    注意返回值可能是空字符串（字段存在但值为空），调用方需用 ``is not None`` 判断是否命中。
    """
    if not fields:
        return None
    names = "|".join(re.escape(f) for f in fields)
    m = re.search(r'"(?:' + names + r')"\s*:\s*"((?:[^"\\]|\\.)*)"', raw)
    return m.group(1) if m else None

# ── 查询扩散 Prompt（检索辅助，非人设，不改彰人语气） ──────────────────────────
_EXPANSION_PROMPT = """你是检索关键词助手，服务于 Project Sekai 角色【东云彰人】（毒舌、嫌麻烦但讲义气的街头歌手）。
给定用户对彰人说的一句话，提炼它**真正在聊的情境/情绪/话题**，用于检索彰人在类似情境下的台词。
规则：
1. 只输出 4-8 个中文关键词/短词，空格分隔；不要解释、不要标点、不要原样复述。
2. 游戏黑话翻成真实含义并补足联想：虾/龙/效率曲→刷高分 肝进度 重复打歌 累；沉船/吃井→抽卡没出 破防 想被安慰；车/上车→组队打歌 邀约。
3. 抓潜台词与情绪："又加班到现在"→疲惫 辛苦 深夜；"考砸了"→失落 沮丧 想安慰；"你唱得真好"→被夸 演出 音乐。
4. 日常闲聊/打招呼就提取核心词即可，别硬编情绪。"""


async def expand_query_for_retrieval(message: str) -> str | None:
    """把用户消息提炼成检索用的情境/情绪/话题关键词；失败/超时返回 None（调用方回退原文）。

    低温短输出 + 6s 短超时，不影响主链路延迟。
    """
    if not message or not message.strip():
        return None
    try:
        resp = await asyncio.wait_for(
            client.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[
                    {"role": "system", "content": _EXPANSION_PROMPT},
                    {"role": "user", "content": message[:200]},
                ],
                temperature=0.3,
                max_tokens=64,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            ),
            timeout=6.0,
        )
        text = (resp.choices[0].message.content or "").strip()
        return text or None
    except Exception as e:
        logger.debug(f"query 扩散失败，回退原文: {e}")
        return None


async def call_deepseek_api(messages: list, model_name: str = "deepseek-v4-flash", force_json: bool = False) -> str:
    """调用 DeepSeek 对话补全，带 15s 超时与降级文案；force_json=True 时强制 JSON 输出。"""
    try:
        kwargs = {
            "model": model_name,
            "messages": messages,
            "temperature": 0.85,
            "presence_penalty": 0.7,
            "frequency_penalty": 0.8,
            "max_tokens": 2048,
            "stream": False,
            "extra_body": {"thinking": {"type": "disabled"}},
        }
        if force_json:
            kwargs["response_format"] = {"type": "json_object"}
        response = await asyncio.wait_for(
            client.chat.completions.create(**kwargs),
            timeout=15.0,
        )
        return response.choices[0].message.content
    except asyncio.TimeoutError:
        logger.error("API 请求超时熔断！")
        return "（揉了揉太阳穴）……啧，头好痛，让我静一静。"
    except Exception as e:
        logger.error(f"API请求失败: {e}")
        return "（咬牙切齿）啧，网络好像有点问题……"


async def call_deepseek_api_agent(messages, tools: list, model_name="deepseek-v4-flash"):
    """带 Function Calling 的 Agent 调用。返回 ChatCompletionMessage 对象，失败返回 None。"""
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model_name,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                response_format={"type": "json_object"},
                temperature=0.85,
                presence_penalty=0.7,
                frequency_penalty=0.8,
                max_tokens=2048,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            ),
            timeout=15.0,
        )
        return response.choices[0].message
    except asyncio.TimeoutError:
        logger.error("Agent API 请求超时熔断")
        return None
    except Exception as e:
        logger.error(f"Agent API 请求失败: {e}")
        return None


async def smart_search(query: str) -> str:
    """用 Tavily 搜索 query，返回前 2 条结果摘要拼成的文本；失败或无结果返回空串。"""
    try:
        url = "https://api.tavily.com/search"
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "include_images": False,
        }
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(url, json=payload) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"❌ Tavily 搜索API报错: HTTP {resp.status} - {error_text}")
                return ""
            data = await resp.json()
            results = data.get("results", [])
            if not results:
                logger.warning("⚠️ 搜索API未返回任何有效结果")
                return ""
            summary = ""
            for item in results[:2]:
                title   = item.get("title", "无标题")
                content = item.get("content", "无内容")
                if len(content) > 150:
                    content = content[:150] + "..."
                summary += f"- {title}: {content}\n"
            logger.info(f"🔍 网络搜索成功！提取了 {len(results[:2])} 条摘要。")
            return summary
    except Exception as e:
        logger.error(f"❌ 搜索过程中发生严重错误: {e}")
        return ""


# ── GLM-4.6V 视觉识别：结构化输出 + 代码侧裁决 ─────────────────────────────

_VISION_MODEL = "glm-4.6v-flash"
_VISION_THINKING = "enabled"   # 首轮深度思考：免费档拿精度；嫌慢改 "disabled"
_OCR_THINKING = "disabled"     # OCR 轮纯转写，要速度
_VISION_TIMEOUT = 45.0         # thinking 模式需要更长预算
_OCR_TIMEOUT = 30.0
_CONFIDENCE_GATE = 0.7         # 低于此置信度的角色判定一律降级，宁可不认不许认错
_PASSTHROUGH_MAX_BYTES = 3_500_000  # 3.5MB 原图 ≈ 4.7MB base64，低于智谱单图 5MB 上限
_MAX_IMAGES = 3                # 单条消息最多识别的图片数
_MAX_PAYLOAD_ENTRIES = 4       # 多图 + 动图抽帧后的图像条目总上限
_ANIMATED_FRAMES = 3           # 动图最多抽帧数

_SCENE_LABELS = {"character_art", "merch", "screenshot_or_text", "meme", "food", "daily_photo", "unknown"}
_CHARACTER_IDS = {
    "ichika", "saki", "honami", "shiho",
    "minori", "haruka", "airi", "shizuku",
    "kohane", "an", "akito", "toya",
    "tsukasa", "emu", "nene", "rui",
    "kanade", "mafuyu", "ena", "mizuki",
    "miku", "rin", "len", "luka", "meiko", "kaito",
}
_CHAR_TAG_CN = {
    "ichika": "一歌", "saki": "咲希", "honami": "穗波", "shiho": "志步",
    "minori": "实乃理", "haruka": "遥", "airi": "爱莉", "shizuku": "雫",
    "kohane": "心羽", "an": "杏", "akito": "彰人", "toya": "冬弥",
    "tsukasa": "天马司", "emu": "笑梦", "nene": "宁宁", "rui": "类",
    "kanade": "奏", "mafuyu": "真冬", "ena": "绘名", "mizuki": "瑞希",
    "miku": "初音未来", "rin": "镜音铃", "len": "镜音连", "luka": "流歌",
    "meiko": "MEIKO", "kaito": "KAITO",
    "pair": "合照",  # 裁决标签，复用同一映射渲染标签行
}
_SCENE_TAG_CN = {
    "character_art": "二次元角色", "merch": "周边谷子", "screenshot_or_text": "截图文字",
    "meme": "梗图表情", "food": "美食", "daily_photo": "日常", "unknown": "未知",
}


@dataclass
class ImageAnalysis:
    """describe_image 的结构化结果；character_label 为代码侧裁决后的最终标签。"""

    scene_label: str = "unknown"    # character_art/merch/screenshot_or_text/meme/food/daily_photo/unknown
    character_label: str = "none"   # akito/toya/pair/kaito/none（裁决后，驱动 RP 分支与标签行）
    characters: list[str] = field(default_factory=list)  # 画面中全部确认角色（26 枚举 id，仅供描述文本）
    confidence: float = 0.0         # 模型自报置信度（粗粒度，仅用于门控与日志）
    summary: str = ""
    ocr_text: str = ""
    details: str = ""


_VISION_PROMPT = """你是一个精通《世界计划 (Project Sekai/PJSK)》与二次元文化的视觉分析专家。仔细观察图片（可能有多张图或同一动图的多帧），只输出一个 JSON 对象，不要 markdown 代码块、不要任何解释文字。字段定义如下：

{
  "scene_label": "图片类型，只能取以下之一：character_art(动漫角色图/同人图/立绘) / merch(实体周边：徽章吧唧、亚克力立牌、毛绒、橡胶挂件等) / screenshot_or_text(游戏界面、聊天记录、网页等以文字为主的截图) / meme(梗图表情包) / food(美食) / daily_photo(现实日常照片) / unknown(无法判断)",
  "characters": ["画面中能确认在场的PJSK角色id数组，只能用下方名册里的26个id，认不出或不确定的不要列，没有就给空数组"],
  "confidence": 0到1之间的小数，表示你对 characters 判断的整体把握,
  "features": {
    "orange_hair": 画面中是否有橙色短发的男生（true/false）,
    "yellow_streak_bangs": 该橙发男生刘海处是否有明显的黄色挑染（true/false）,
    "blue_gray_split_hair": 是否有男生发色为左右双拼——半边深蓝、半边浅灰/银色（true/false）,
    "tear_mole": 该双拼发色男生左眼下方是否有泪痣（看不清就填 false）,
    "full_blue_hair": 是否有男生是纯粹的全头蓝发（通常配蓝色围巾或风衣）（true/false）,
    "two_persons": 画面主体是否为两个人物同框（true/false）
  },
  "summary": "一两句话精准描述画面主体（例如：一个画着KAITO的镭射吧唧）",
  "ocr_text": "图中显眼的配字/界面文字/数值，按阅读顺序原样提取；没有就填空字符串",
  "details": "一句话描述画面情绪氛围，或一个最吸引人注意的小细节"
}

【PJSK 角色名册（id：识别特征）】
Leo/need：ichika(星乃一歌：蓝灰色偏分直发，瞳色为灰色) / saki(天马咲希：奶油金色双马尾元气少女，注意跟小豆泽心羽区分，saki的双马尾更长，发尾是粉色渐变，瞳色浅红色)/ honami(望月穗波：粉棕色头发温柔，齐刘海，通常有侧马尾，瞳色为冰蓝色) / shiho(日野森志步：灰色短发，神态很酷，瞳色为绿色)
MORE MORE JUMP!：minori(花里实乃理：橙棕色中长发元气，通常右侧耳朵上方有编发，瞳色为灰色) / haruka(桐谷遥：蓝色齐肩短发，瞳色为蓝色) / airi(桃井爱莉：粉色小双马尾+长发+虎牙，瞳色为深粉色) / shizuku(日野森雫：浅灰绿色长发温柔，瞳色为青色)
Vivid BAD SQUAD：kohane(小豆泽心羽：米黄色短双马尾乖巧，瞳色为米黄+棕色) / an(白石杏：黑长发发尾蓝色，有星星发饰，瞳色橙色) / akito(东云彰人：橙色短发+刘海黄色挑染，左侧耳朵通常有耳链/双耳有耳饰，瞳色为黄绿色) / toya(青柳冬弥：深蓝×浅灰左右对半发色，瞳色为银色)
Wonderlands×Showtime：tsukasa(天马司：金黄色短发发尾红色渐变，浮夸自信，瞳色为橙黄色) / emu(凤笑梦：粉色齐刘海齐肩短发，超元气，瞳色为粉色) / nene(草薙宁宁：浅绿色长发，两鬓下方有拢起来的低马尾，瞳色为紫色) / rui(神代类：紫色短发，有明显的两撮荧光挑染，瞳色为黄色)
25时、Nightcord见。：kanade(宵崎奏：银白色长发，瞳色为蓝色) / mafuyu(朝比奈真冬：灰紫色单马尾，眼神空洞，瞳色为深紫到浅蓝过渡) / ena(东云绘名：深棕色齐肩短发，齐刘海，左侧耳朵上方有编发，彰人的姐姐，瞳色为偏浅的红棕色，注意区分清楚meiko和ena) / mizuki(晓山瑞希：浅粉色长发高侧马尾，中性可爱打扮，瞳色为粉色)
虚拟歌手：miku(初音未来：青绿色超长双马尾，瞳色为绿色) / rin(镜音铃：金色短发+白色大蝴蝶结，瞳色为墨绿色) / len(镜音连：金色短发扎小后马尾，瞳色为墨绿色) / luka(巡音流歌：粉色长直发成熟，瞳色为紫色) / meiko(MEIKO：浅棕色短发，瞳色为棕色，注意区分清楚meiko和ena) / kaito(KAITO：纯蓝短发，瞳色为蓝色)

判定须知（重要，认错会扣分）：
1. 不要凭印象猜角色：先如实勾选 features 里的发色特征，characters 必须与 features 自洽。
2. 橙发但没有黄色挑染的不是彰人(akito)；金黄发带红渐变的是天马司(tsukasa)。
3. 全头纯蓝发是 KAITO(kaito)，绝不是冬弥；只有蓝灰对半劈开的发色才是冬弥(toya)。
4. 金发易混：白色大蝴蝶结=镜音铃(rin)；后扎小马尾=镜音连(len)；长双马尾=咲希(saki)；米黄色短双马尾=小豆泽心羽（kohane）。
5. 粉发易混：小双马尾+虎牙=爱莉(airi)；齐刘海齐肩短发超元气=笑梦(emu)；浅粉高侧马尾=瑞希(mizuki)；粉长直成熟=流歌(luka)。
6. 周边按真实物理材质描述：带角色印花的圆形金属徽章叫"吧唧/谷子"，透明亚克力的叫"立牌"。
7. 常见游戏优先识别：《世界计划(PJSK)》《明日方舟》《绝区零》《崩坏：星穹铁道》《炉石传说》。
8. 拿不准时 characters 给空数组、scene_label 填 unknown，不要硬猜。"""

_OCR_PROMPT = """你是 OCR 文字提取助手。请按阅读顺序、逐字原样提取图片中所有可见文字（包括界面按钮、数值、水印、对话气泡里的字）。只输出提取出的文字本身，不要任何解释、概括或翻译。如果图中确实没有文字，只输出：无"""


def _sniff_image_mime(data: bytes) -> str | None:
    """纯 magic-bytes 嗅探常见图片格式；认不出返回 None。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _should_passthrough(mime: str | None, long_side: int, byte_len: int, is_animated: bool, max_side: int) -> bool:
    """静态 JPEG/PNG 且尺寸体积都达标时直接透传原图，避免重编码损失细节（截图 OCR 受益最大）。"""
    return (
        mime in ("image/jpeg", "image/png")
        and not is_animated
        and long_side <= max_side
        and byte_len <= _PASSTHROUGH_MAX_BYTES
    )


def _select_frame_indices(n_frames: int, max_frames: int = _ANIMATED_FRAMES) -> list[int]:
    """动图取帧索引：帧数不多就全取，否则首/中/尾均匀采样（保序去重）。"""
    if n_frames <= 0:
        return [0]
    if n_frames <= max_frames:
        return list(range(n_frames))
    if max_frames == 1:
        return [0]
    step = (n_frames - 1) / (max_frames - 1)
    indices: list[int] = []
    for i in range(max_frames):
        idx = round(i * step)
        if idx not in indices:
            indices.append(idx)
    return indices


def _encode_as_jpeg(image: Any, max_side: int, quality: int) -> tuple[str, str]:
    """PIL 图像 → RGB → 限边长 → JPEG base64，返回 (b64, mime)。"""
    if image.mode != "RGB":
        image = image.convert("RGB")
    image.thumbnail((max_side, max_side))
    buff = BytesIO()
    image.save(buff, format="JPEG", quality=quality)
    return base64.b64encode(buff.getvalue()).decode("utf-8"), "image/jpeg"


def _prepare_image_payloads(
    images: list[bytes], max_side: int = 1536, quality: int = 90, animated_frames: int = _ANIMATED_FRAMES
) -> list[tuple[str, str]]:
    """逐图清洗转码为 (base64, mime) 列表：静态小图透传原编码，动图均匀抽帧，超限缩放重编码。

    单图失败跳过不拖累整批；全部失败返回空列表（调用方降级）。
    """
    payloads: list[tuple[str, str]] = []
    for image_data in images[:_MAX_IMAGES]:
        if len(payloads) >= _MAX_PAYLOAD_ENTRIES:
            break
        try:
            image = PILImage.open(BytesIO(image_data))
            if getattr(image, "is_animated", False):
                n_frames = int(getattr(image, "n_frames", 1) or 1)
                for idx in _select_frame_indices(n_frames, animated_frames):
                    if len(payloads) >= _MAX_PAYLOAD_ENTRIES:
                        break
                    image.seek(idx)
                    payloads.append(_encode_as_jpeg(image.convert("RGB"), max_side, quality))
                continue
            mime = _sniff_image_mime(image_data)
            if _should_passthrough(mime, max(image.size), len(image_data), False, max_side):
                payloads.append((base64.b64encode(image_data).decode("utf-8"), mime))
                continue
            payloads.append(_encode_as_jpeg(image, max_side, quality))
        except Exception as e:
            logger.error(f"❌ 图片清洗/转码失败 (可能是下载到了假图片): {e}")
    return payloads


def _parse_vision_reply(raw: str) -> dict:
    """三级降级解析视觉模型返回：完整 JSON → rescue_field 抠字段 → 原文包装为 unknown。"""
    try:
        parsed = json.loads(extract_json_block(raw))
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    summary = rescue_field(raw, "summary")
    ocr = rescue_field(raw, "ocr_text")
    if summary is not None or ocr is not None:
        return {"summary": summary or "", "ocr_text": ocr or "", "scene_label": "unknown"}
    return {"summary": raw.strip()[:200], "scene_label": "unknown"}


def _adjudicate(parsed: dict) -> ImageAnalysis:
    """把模型输出裁决为最终 ImageAnalysis：RP 角色标签只认布尔特征证据，证据不足一律降级 none。

    彰人=橙发+黄挑染缺一不可；纯蓝发硬裁 KAITO 杜绝认成冬弥；置信度低于门槛全部降级。
    characters 数组仅做枚举过滤，供描述文本展示，不驱动 RP 分支。
    """
    raw_features = parsed.get("features")
    features = raw_features if isinstance(raw_features, dict) else {}

    def flag(name: str) -> bool:
        return bool(features.get(name))

    raw_chars = parsed.get("characters")
    characters: list[str] = []
    for c in raw_chars if isinstance(raw_chars, list) else []:
        if isinstance(c, str) and c in _CHARACTER_IDS and c not in characters:
            characters.append(c)
    characters = characters[:6]

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    is_akito = flag("orange_hair") and flag("yellow_streak_bangs")
    if flag("two_persons") and is_akito and flag("blue_gray_split_hair"):
        label = "pair"
    elif flag("full_blue_hair"):
        label = "kaito"  # 纯蓝发硬裁 KAITO，绝不落冬弥
    elif is_akito:
        label = "akito"
    elif flag("blue_gray_split_hair"):
        label = "toya"
    else:
        label = "none"
    if label != "none" and confidence < _CONFIDENCE_GATE:
        label = "none"

    scene = parsed.get("scene_label")
    if scene not in _SCENE_LABELS:
        scene = "unknown"

    def text_of(key: str) -> str:
        value = parsed.get(key)
        return value.strip() if isinstance(value, str) else ""

    return ImageAnalysis(
        scene_label=scene,
        character_label=label,
        characters=characters,
        confidence=confidence,
        summary=text_of("summary"),
        ocr_text=text_of("ocr_text")[:1500],
        details=text_of("details"),
    )


def _should_run_ocr_pass(scene_label: str, finish_reason: str | None) -> bool:
    """截图/文字为主的图，或首轮输出被截断时，追加一次高清专项 OCR。"""
    return scene_label == "screenshot_or_text" or finish_reason == "length"


def _build_vision_content(prompt: str, payloads: list[tuple[str, str]]) -> list[dict]:
    """拼多模态消息体：1 个文本指令 + N 个图像条目。"""
    content: list[dict] = [{"type": "text", "text": prompt}]
    content.extend(
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}
        for b64, mime in payloads
    )
    return content


async def _run_ocr_pass(images: list[bytes]) -> str:
    """对原图做高分辨率重编码（2048/q95，动图只取首帧）后专项 OCR；失败返回空串保留首轮结果。"""
    try:
        payloads = _prepare_image_payloads(images, max_side=2048, quality=95, animated_frames=1)
        if not payloads:
            return ""
        response = await asyncio.wait_for(
            vision_client.chat.completions.create(
                model=_VISION_MODEL,
                messages=[{"role": "user", "content": _build_vision_content(_OCR_PROMPT, payloads)}],
                max_tokens=8192,
                temperature=0.1,
                extra_body={"thinking": {"type": _OCR_THINKING}},
            ),
            timeout=_OCR_TIMEOUT,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text or text == "无":
            return ""
        logger.info("🔍 二次 OCR 增强成功")
        return text[:1500]
    except Exception as e:
        logger.warning(f"⚠️ 二次 OCR 增强失败，保留首轮结果: {e}")
        return ""


async def describe_image(images: list[bytes]) -> ImageAnalysis | None:
    """用智谱 GLM-4.6V 识别图片（支持多张、动图自动抽帧），返回裁决后的 ImageAnalysis。

    未配置 key、预处理全军覆没或调用失败返回 None（fail-silent，不阻断主对话）；
    截图类/输出截断时自动追加一次高清 OCR 调用并合并结果。
    """
    try:
        if "请在这里" in ZHIPU_API_KEY:
            return None
        payloads = _prepare_image_payloads(images)
        if not payloads:
            return None

        try:
            response = await asyncio.wait_for(
                vision_client.chat.completions.create(
                    model=_VISION_MODEL,
                    messages=[{"role": "user", "content": _build_vision_content(_VISION_PROMPT, payloads)}],
                    max_tokens=4096,
                    temperature=0.1,
                    extra_body={"thinking": {"type": _VISION_THINKING}},
                ),
                timeout=_VISION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.error(f"❌ 智谱视觉调用超时（{_VISION_TIMEOUT:.0f}s）")
            return None
        except Exception as api_e:
            logger.error(f"❌ 智谱 API 请求彻底失败: {api_e}")
            return None

        choice = response.choices[0]
        analysis = _adjudicate(_parse_vision_reply(choice.message.content or ""))
        logger.info(
            f"🤖 智谱特工侦察结果: scene={analysis.scene_label} char={analysis.character_label} "
            f"chars={analysis.characters} conf={analysis.confidence:.2f}"
        )

        if _should_run_ocr_pass(analysis.scene_label, getattr(choice, "finish_reason", None)):
            ocr = await _run_ocr_pass(images)
            if ocr:
                analysis.ocr_text = ocr
        return analysis

    except Exception as final_e:
        logger.error(f"❌ 视觉模块发生未知异常: {final_e}")
        return None


def format_image_analysis_for_chat(analysis: ImageAnalysis) -> str:
    """把 ImageAnalysis 渲染成历史/LLM 注入用文本（与旧版四段式兼容，新增识别角色行）。

    各段截断上限防 history 膨胀（同一串会进入最多 40 条的群记忆）。
    """
    tag = _CHAR_TAG_CN.get(analysis.character_label) or _SCENE_TAG_CN.get(analysis.scene_label, "未知")
    chars = "、".join(_CHAR_TAG_CN[c] for c in analysis.characters if c in _CHAR_TAG_CN) or "无"
    summary = analysis.summary.strip()[:150] or "（看不清的画面）"
    ocr = analysis.ocr_text.strip()[:500] or "无"
    details = analysis.details.strip()[:100] or "无"
    return (
        f"【标签】：[{tag}]\n【识别角色】：{chars}\n【画面核心】：{summary}\n"
        f"【OCR提取】：{ocr}\n【关键细节】：{details}"
    )


async def to_image_data(image: Any) -> bytes:
    """从消息图片段取原始字节：优先 raw，其次本地 path，最后下载 url。"""
    if image.raw is not None:
        return image.raw
    if image.path is not None:
        return Path(image.path).read_bytes()
    if image.url is not None:
        logger.info(f"📥 开始下载图片 URL: {image.url[:60]}...")
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        ) as session, session.get(image.url) as resp:
            if resp.status != 200:
                logger.error(f"❌ 图片下载失败，HTTP 状态码: {resp.status}")
                raise ValueError(f"下载失败: {resp.status}")
            content_type = resp.headers.get("Content-Type", "")
            if not any(t in content_type for t in ("image/", "application/octet-stream")):
                logger.error(f"❌ 非图片类型，Content-Type: {content_type}")
                raise ValueError(f"非图片内容: {content_type}")
            data = await resp.read()
            logger.info(f"✅ 图片下载成功，文件大小: {len(data)} 字节")
            return data
    raise ValueError("无法获取图片数据")


async def embed_text(text: str) -> list[float] | None:
    """BGE-M3 单条 embedding；失败返回 None（绝不抛到调用方）。"""
    if not embedding_client:
        return None
    if not text or not text.strip():
        return None
    try:
        r = await embedding_client.embeddings.create(model="BAAI/bge-m3", input=text)
        return r.data[0].embedding
    except Exception as e:
        logger.warning(f"embed_text 失败，降级: {e}")
        return None


# ── SiliconFlow 重排序（bge-reranker-v2-m3，检索精排用） ──────────────────────

_RERANK_URL = "https://api.siliconflow.cn/v1/rerank"
_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
_RERANK_TIMEOUT = 5  # 秒；超时即降级（调用方回退 cosine 顺序）
_RERANK_DOC_MAX_CHARS = 512  # 单文档截断，防 payload 超限
_RERANK_QUERY_MAX_CHARS = 512  # query（含扩散 blend）截断


def _parse_rerank_response(data: Any, n_docs: int) -> list[tuple[int, float]] | None:
    """解析 rerank API 返回 → [(候选下标, 相关分)]，按分降序；结构异常返回 None。

    空 results / 全部条目非法也返回 None——解析层的「空」一律视为异常，调用方回退 cosine 顺序；
    合法的「无相关命中」空列表由 retrieval 层的阈值过滤产生，不在此处。
    """
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list) or not results:
        return None
    out: list[tuple[int, float]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or not (0 <= index < n_docs):
            continue
        try:
            out.append((index, float(item.get("relevance_score"))))
        except (TypeError, ValueError):
            continue
    if not out:
        return None
    out.sort(key=lambda pair: pair[1], reverse=True)  # 不信任 API 返回顺序
    return out


async def rerank_documents(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]] | None:
    """bge-reranker-v2-m3 重排序：返回 [(documents 下标, 相关分 0~1)] 按分降序；失败返回 None（绝不抛到调用方）。

    与 embed_text 共用 SILICONFLOW_API_KEY 门控：embedding_client 为 None（未配置 key）→ 直接降级。
    """
    if not embedding_client:
        return None
    if not query or not query.strip() or not documents:
        return None
    try:
        payload = {
            "model": _RERANK_MODEL,
            "query": query[:_RERANK_QUERY_MAX_CHARS],
            "documents": [d[:_RERANK_DOC_MAX_CHARS] for d in documents],
            "top_n": min(top_n, len(documents)),
            "return_documents": False,
        }
        headers = {"Authorization": f"Bearer {SILICONFLOW_API_KEY}"}
        timeout = aiohttp.ClientTimeout(total=_RERANK_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session, session.post(
            _RERANK_URL, json=payload, headers=headers
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.warning(f"⚠️ rerank API 报错，降级: HTTP {resp.status} - {error_text[:120]}")
                return None
            data = await resp.json()
        return _parse_rerank_response(data, len(documents))
    except Exception as e:
        logger.warning(f"⚠️ rerank 失败，降级: {e}")
        return None
