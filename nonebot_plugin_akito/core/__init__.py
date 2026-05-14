# -*- coding: utf-8 -*-
# ============================================================================
# core/__init__.py — 包入口
#
# 常量部分（原 constants.py）必须放在所有 import 之前，
# 因为下方各个子模块会在导入时执行 from . import X 来获取这些值。
# Python 的部分模块初始化保证此顺序安全。
# ============================================================================

import os
import datetime
from pathlib import Path
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()  # 显式将 .env 写入 os.environ（NoneBot2 自身不做这一步）

TZ_CN  = datetime.timezone(datetime.timedelta(hours=8))
TZ_JST = datetime.timezone(datetime.timedelta(hours=9))
DB_PATH = Path("data/impression_history.db")
IMAGE_BASE_PATH = Path("data/images")
MAX_HISTORY_LEN = 40

DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
TAVILY_API_KEY   = os.environ.get("TAVILY_API_KEY", "")
ZHIPU_API_KEY    = os.environ.get("ZHIPU_API_KEY", "")

client = AsyncOpenAI(api_key=DEEPSEEK_API_KEY, base_url="https://api.deepseek.com")
vision_client = AsyncOpenAI(api_key=ZHIPU_API_KEY, base_url="https://open.bigmodel.cn/api/paas/v4/")

TOYA_QQ_ID   = os.environ.get("TOYA_QQ_ID", "3958033212")
SUPERUSER_QQ = os.environ.get("SUPERUSER_QQ", "2403925946")
TRIGGER_NAMES = {"东云小彰", "小彰"}

ALLOWED_CHAT_GROUPS   = [1041487251, 691188576, 761599729, 740468887]
ALLOWED_CP_GROUPS     = [1041487251, 691188576, 761599729]
ALLOWED_MEMORY_GROUPS = [1041487251]
TARGET_GROUPS         = [740468887, 761599729, 691188576]

GROUP_IMAGE_PERMISSIONS = {
    1041487251: ["all"],
    691188576:  ["all"],
    761599729:  ["all"],
}


# ── 子模块导入（必须放在常量定义之后） ────────────────────────────────

from .memory import (
    MEMORY_DB, load_memory, save_memory,
    get_memory_key, get_user_memory,
    get_group_context,
)
from .data import (
    load_json_file, load_prompt_template, reload_assets,
    SCRIPT_DB, REACTIONS_DB, PROMPTS_DB, DIRECTOR_DB,
    DAILY_ROUTINE, WL2_ROUTINE,
    SONG_DATA, RELATIONSHIP_DATA, PJSK_KNOWLEDGE_BASE,
)
from .life_state import (
    AKITO_STATUS, STATE_DURATION,
    grant_safety_pass, get_safe_until, get_last_complaint, set_last_complaint,
    get_daily_activity, check_sleep_status, get_festival_buff, get_morning_run_buff,
    parse_duration_and_content, check_img_permission,
)
from .api import (
    call_deepseek_api, call_deepseek_api_agent, smart_search, describe_image, to_image_data,
)
from .context import (
    get_random_examples, get_base_persona, reload_persona, get_song_memories, get_hybrid_relationship,
)
from .time_awareness import (
    record_bot_response, build_time_gap_prompt,
)
