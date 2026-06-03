"""外部 API 封装：DeepSeek 对话 / Agent、Tavily 搜索、智谱 GLM-4V 图像识别，均带超时与降级处理。"""

import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Any

import aiohttp
from nonebot.log import logger
from PIL import Image as PILImage

from . import TAVILY_API_KEY, ZHIPU_API_KEY, client, embedding_client, vision_client

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


async def describe_image(image_data: bytes) -> str:
    """用智谱 GLM-4V 识别图片，返回结构化情报文本；未配置 key 或失败返回空串。"""
    try:
        if "请在这里" in ZHIPU_API_KEY:
            return ""

        try:
            image = PILImage.open(BytesIO(image_data))
            if getattr(image, "is_animated", False):
                image.seek(0)
            if image.mode in ("RGBA", "P", "LA"):
                image = image.convert("RGB")
            image.thumbnail((1024, 1024))
            buff = BytesIO()
            image.save(buff, format="JPEG", quality=85)
            base64_image = base64.b64encode(buff.getvalue()).decode("utf-8")
        except Exception as e:
            logger.error(f"❌ 图片清洗/转码失败 (可能是下载到了假图片): {e}")
            return ""

        vision_prompt = """
        你是一个拥有"二次元与游戏雷达"的顶级视觉分析专家。请严格按照要求，输出一张精准的情报报告。

        【🚨 核心先验知识库 (极高优先级)】：
        1. 二次元周边：遇到别人晒动漫周边时，请如实描述其真实物理材质（如：毛绒玩偶/团子、橡胶挂件等）。如果是带有角色印花的圆形金属徽章请叫它"吧唧"或"谷子"，透明亚克力材质的叫"立牌"。
        2. 常见游戏：优先识别是否为《世界计划 (PJSK)》、《明日方舟》、《绝区零》、《崩坏：星穹铁道》或《炉石传说》，并点明具体状态。

        【⚠️ 角色精准防伪鉴定（最高指令，认错会扣分！）⚠️】：
        - [彰人]：必须是【橙色短发 + 明显的黄色挑染（通常在刘海处）】的男生。
          🚫防错指南：如果是纯粹的全头橙发，或者黄发带橙色渐变（如天马司），绝对不是彰人！没有黄色挑染就绝对不能打[彰人]标签！
        - [冬弥]：必须是【明显的双拼发色：左半边深蓝色，右半边浅灰色/银色 + 左眼下方有泪痣】的男生。
          🚫防错指南（重点！）：绝对不要把 KAITO 认成冬弥！如果画面中的男生是【纯粹的全头蓝色头发】，且通常戴着蓝色围巾或穿着风衣，那他是 KAITO，绝不是冬弥！只有【蓝灰对半劈开】的发色才是冬弥！

        【输出格式强制要求】：
        请严格按照以下结构输出，不要啰嗦，直奔主题：
        【标签】：[彰人] / [冬弥] / [合照] / [KAITO] / [天马司] / [周边谷子] / [游戏截图] / [梗图表情] / [美食] / [日常] (选择最贴切的1个)
        【画面核心】：用一两句话精准描述主体（例如："一个画着KAITO的镭射吧唧"）。
        【OCR提取】：提取图中显眼的配字或游戏面板上的字（没有填无）。
        【关键细节】：简述画面的情绪氛围，或一个最能引起人注意的微小细节。
        """

        try:
            response = await vision_client.chat.completions.create(
                model="glm-4v-flash",
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": vision_prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
                    ],
                }],
                max_tokens=150,
                temperature=0.1,
            )
            ans = response.choices[0].message.content
            logger.info(f"🤖 智谱特工侦察结果: \n{ans}")
            return ans
        except Exception as api_e:
            logger.error(f"❌ 智谱 API 请求彻底失败: {api_e}")
            return ""

    except Exception as final_e:
        logger.error(f"❌ 视觉模块发生未知异常: {final_e}")
        return ""


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
