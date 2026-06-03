"""core 包入口：定义全局常量与 API 客户端，并统一导出 memory/data/life_state/api/context/time_awareness 六个子模块的公共接口。"""

# ============================================================================
# core/__init__.py — 包入口
#
# 常量部分（原 constants.py）必须放在所有 import 之前，
# 因为下方各个子模块会在导入时执行 from . import X 来获取这些值。
# Python 的部分模块初始化保证此顺序安全。
# ============================================================================

import json
import os
import datetime
from pathlib import Path
from dotenv import load_dotenv
from nonebot.log import logger
from openai import AsyncOpenAI

load_dotenv()  # 显式将 .env 写入 os.environ（NoneBot2 自身不做这一步）

TZ_CN  = datetime.timezone(datetime.timedelta(hours=8))
TZ_JST = datetime.timezone(datetime.timedelta(hours=9))
DB_PATH = Path("data/impression_history.db")
IMAGE_BASE_PATH = Path("data/images")
MAX_HISTORY_LEN = 40

DEEPSEEK_API_KEY    = os.environ.get("DEEPSEEK_API_KEY", "")
TAVILY_API_KEY      = os.environ.get("TAVILY_API_KEY", "")
ZHIPU_API_KEY       = os.environ.get("ZHIPU_API_KEY", "")
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "")

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
vision_client = AsyncOpenAI(api_key=ZHIPU_API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")
embedding_client = AsyncOpenAI(api_key=SILICONFLOW_API_KEY, base_url="https://api.siliconflow.cn/v1") if SILICONFLOW_API_KEY and "sk-" in SILICONFLOW_API_KEY else None

# numpy 守卫：未安装时置 None，检索引擎整体降级
try:
    import numpy as np
except ImportError:  # pragma: no cover — 生产环境可选依赖
    np = None

TOYA_QQ_ID   = os.environ.get("TOYA_QQ_ID", "3958033212")
SUPERUSER_QQ = os.environ.get("SUPERUSER_QQ", "2403925946")
TRIGGER_NAMES = {"东云小彰", "小彰"}

def _parse_group_list(key: str) -> list[int]:
    raw = os.environ.get(key, "")
    if not raw.strip():
        return []
    return [int(s.strip()) for s in raw.split(",") if s.strip().isdigit()]

ALLOWED_CHAT_GROUPS   = _parse_group_list("ALLOWED_CHAT_GROUPS")
ALLOWED_CP_GROUPS     = _parse_group_list("ALLOWED_CP_GROUPS")
ALLOWED_MEMORY_GROUPS = _parse_group_list("ALLOWED_MEMORY_GROUPS")
TARGET_GROUPS         = _parse_group_list("TARGET_GROUPS")

GROUP_IMAGE_PERMISSIONS = {}
_raw_img = os.environ.get("GROUP_IMAGE_PERMISSIONS", "")
if _raw_img.strip():
    try:
        GROUP_IMAGE_PERMISSIONS = {int(k): v for k, v in json.loads(_raw_img).items()}
    except Exception as e:
        logger.warning(f"⚠️ GROUP_IMAGE_PERMISSIONS 解析失败（应为 JSON），已忽略: {e}")


# ── 子模块导入（必须放在常量定义之后） ────────────────────────────────

from .memory import (
    MEMORY_DB, load_memory, save_memory,
    get_memory_key, get_user_memory,
    get_group_context,
)
from .data import (
    load_json_file, load_prompt_template, reload_assets, find_data_path,
    SCRIPT_DB, REACTIONS_DB, PROMPTS_DB, DIRECTOR_DB,
    DAILY_ROUTINE, WL2_ROUTINE,
    SONG_DATA, RELATIONSHIP_DATA, PJSK_KNOWLEDGE_BASE, SLEEP_DB,
)
from .life_state import (
    AKITO_STATUS, STATE_DURATION,
    grant_safety_pass, get_safe_until, get_last_complaint, set_last_complaint,
    get_daily_activity, check_sleep_status, get_festival_buff, get_morning_run_buff,
    get_sleep_buffer_buff,
    parse_duration_and_content, check_img_permission,
    is_sleeping, sleep_block,
)
from .api import (
    call_deepseek_api, call_deepseek_api_agent, smart_search, describe_image, to_image_data, embed_text,
    expand_query_for_retrieval,
)
from .context import (
    get_random_examples, get_base_persona, reload_persona, get_song_memories, get_hybrid_relationship,
    get_relevant_examples, get_relevant_pjsk,
)
from .time_awareness import (
    record_bot_response, build_time_gap_prompt,
)
from .retrieval import (
    retrieve, reload_indices,
)

# ── 统一公共导出面（显式声明，避免 import * 时泄漏内部名） ────────────────
__all__ = [
    # 常量 / 客户端
    "TZ_CN", "TZ_JST", "DB_PATH", "IMAGE_BASE_PATH", "MAX_HISTORY_LEN",
    "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "ZHIPU_API_KEY", "SILICONFLOW_API_KEY",
    "client", "vision_client", "embedding_client", "np",
    "TOYA_QQ_ID", "SUPERUSER_QQ", "TRIGGER_NAMES",
    "ALLOWED_CHAT_GROUPS", "ALLOWED_CP_GROUPS", "ALLOWED_MEMORY_GROUPS", "TARGET_GROUPS",
    "GROUP_IMAGE_PERMISSIONS",
    # memory
    "MEMORY_DB", "load_memory", "save_memory", "get_memory_key", "get_user_memory",
    "get_group_context",
    # data
    "load_json_file", "load_prompt_template", "reload_assets", "find_data_path",
    "SCRIPT_DB", "REACTIONS_DB", "PROMPTS_DB", "DIRECTOR_DB",
    "DAILY_ROUTINE", "WL2_ROUTINE", "SONG_DATA", "RELATIONSHIP_DATA", "PJSK_KNOWLEDGE_BASE",
    "SLEEP_DB",
    # life_state
    "AKITO_STATUS", "STATE_DURATION",
    "grant_safety_pass", "get_safe_until", "get_last_complaint", "set_last_complaint",
    "get_daily_activity", "check_sleep_status", "get_festival_buff", "get_morning_run_buff",
    "get_sleep_buffer_buff", "parse_duration_and_content", "check_img_permission",
    "is_sleeping", "sleep_block",
    # api
    "call_deepseek_api", "call_deepseek_api_agent", "smart_search", "describe_image", "to_image_data", "embed_text",
    "expand_query_for_retrieval",
    # context
    "get_random_examples", "get_base_persona", "reload_persona", "get_song_memories",
    "get_hybrid_relationship", "get_relevant_examples", "get_relevant_pjsk",
    # time_awareness
    "record_bot_response", "build_time_gap_prompt",
    # retrieval
    "retrieve", "reload_indices",
]
