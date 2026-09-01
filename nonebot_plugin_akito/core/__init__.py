"""AkitoBot 核心包入口。

集中加载 `.env` 中的 API 密钥、群组/时区常量与 DeepSeek、GLM-4.6V-Flash、
SiliconFlow 客户端，并统一导出数据、记忆、状态机、检索和 Prompt 组装等核心接口。
该模块必须先于其他 `core` 子模块完成常量初始化；未配置可选服务时由调用方降级。
"""

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

from dotenv import load_dotenv
from nonebot.log import logger
from openai import AsyncOpenAI

from .paths import get_data_dir
from .types import (
    BaseUserRecord,
    ContextBlock,
    ConversationState,
    GameData,
    GroupRecord,
    MemorySession,
    ResponseEnvelope,
    ToolResult,
    TOOL_STATUSES,
    Turn,
)

load_dotenv()  # 显式将 .env 写入 os.environ（NoneBot2 自身不做这一步）

TZ_CN  = datetime.timezone(datetime.timedelta(hours=8))
TZ_JST = datetime.timezone(datetime.timedelta(hours=9))
DATA_DIR = get_data_dir()
DB_PATH = DATA_DIR / "impression_history.db"
IMAGE_BASE_PATH = DATA_DIR / "images"
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

# 敏感 ID 一律走 .env，代码内不留真实号码兜底（PROJECT_SPEC §15.3）
TOYA_QQ_ID   = os.environ.get("TOYA_QQ_ID", "")
SUPERUSER_QQ = os.environ.get("SUPERUSER_QQ", "")
if not SUPERUSER_QQ:
    logger.warning("⚠️ SUPERUSER_QQ 未在 .env 配置，超管指令（重置对话/热更新/WL2 等）将全部不可用")
if not TOYA_QQ_ID:
    logger.warning("⚠️ TOYA_QQ_ID 未在 .env 配置，冬弥本人识别（CP/搭档模式）将不生效")
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
WORDCLOUD_GROUPS      = _parse_group_list("WORDCLOUD_GROUPS")

GROUP_IMAGE_PERMISSIONS = {}
_raw_img = os.environ.get("GROUP_IMAGE_PERMISSIONS", "")
if _raw_img.strip():
    try:
        GROUP_IMAGE_PERMISSIONS = {int(k): v for k, v in json.loads(_raw_img).items()}
    except Exception as e:
        logger.warning(f"⚠️ GROUP_IMAGE_PERMISSIONS 解析失败（应为 JSON），已忽略: {e}")


# ── 子模块导入（必须放在常量定义之后） ────────────────────────────────

from .memory import (
    MEMORY_DB, MessageRow, load_memory, save_memory,
    get_memory_key, get_user_memory,
    MessageReader, delete_group_messages, get_group_context, open_message_reader,
    parse_sqlite_timestamp, record_bot_message, record_message,
)
from .data import (
    load_json_file, load_prompt_template, reload_assets, find_data_path, get_data_dir,
    SCRIPT_DB, REACTIONS_DB, PROMPTS_DB, DIRECTOR_DB,
    DAILY_ROUTINE, WL2_ROUTINE,
    SONG_DATA, RELATIONSHIP_DATA, SLEEP_DB,
    EVENT_MEMORY_DB,
)
# 注：PJSK_KNOWLEDGE_BASE / PJSK_INTRO 是会被热重载重新赋值的 str，
# 不做模块级再导出（避免旧引用失效）；需要时经 data.get_pjsk_knowledge_base() 等 getter 取。
from .life_state import (
    AKITO_STATUS, QueryIntent, STATE_DURATION,
    grant_safety_pass, get_safe_until, get_last_complaint, set_last_complaint,
    get_daily_activity, check_sleep_status, get_festival_buff, get_morning_run_buff,
    get_sleep_buffer_buff, get_toya_anchor,
    parse_duration_and_content, check_img_permission,
    classify_query_intent, is_sleeping, sleep_block,
)
from .api import (
    ImageAnalysis,
    call_deepseek_api, call_deepseek_api_agent, smart_search, smart_search_result, smart_search_detailed,
    describe_image, to_image_data, embed_text,
    expand_query_for_retrieval, extract_json_block, format_image_analysis_for_chat, parse_json_object,
    rerank_documents, rescue_field, rescue_tail_after_field,
)
from .context import (
    RelationshipMatch, find_relationship_match, format_relationship_context,
    get_random_examples, get_base_persona, reload_persona, get_song_memories, get_song_mention,
    get_hybrid_relationship,
    get_relevant_examples, get_relevant_pjsk,
)
from .prompt_builder import (
    JsonFieldSpec, JsonSchemaSpec, PromptFrame, SharedPromptContext,
    build_shared_prompt_context, render_auto_chat_prompt, render_impression_analysis_prompt,
    render_impression_prompt, render_impression_reply_prompt,
    render_json_schema, render_main_chat_prompt, render_prompt_frame,
)
from .time_awareness import (
    record_bot_response, build_time_gap_prompt,
)
from .retrieval import (
    RetrievalContext, RetrievalResult, build_retrieval_context, retrieve, retrieve_result, reload_indices,
)
from .observability import (
    AutoReplyShadowReport, TurnTrace,
    current_request_id,
    finish_turn_trace,
    get_turn_trace,
    new_request_id,
    record_context_sources,
    record_context_shadow,
    record_auto_reply_shadow,
    evaluate_auto_reply_shadow,
    record_event_memory,
    record_fallback_reason,
    record_ambiguity_guard,
    record_intent,
    record_memory_hit,
    record_model_call,
    record_parse_result,
    record_repeat_detection,
    record_retry,
    record_tool_call,
    record_tool_route,
    record_rollout,
    reset_metrics,
    set_trace_stage,
    snapshot_metrics,
    start_turn_trace,
)
from .context_orchestrator import (
    ContextOrchestrator,
    ContextShadowReport,
    build_context_blocks,
    estimate_token_count,
    select_context_for_mode,
    shadow_context,
)
from .event_memory import (
    EventMemoryHit,
    EventMemoryResult,
    build_event_memory_context,
    format_event_memory_context,
    retrieve_event_memories,
)
from .ambiguity_guard import (
    AmbiguityGuardDecision,
    AmbiguitySignals,
    choose_clarification_template,
    detect_ambiguity,
    detect_ambiguity_signals,
    evaluate_ambiguity_guard,
    is_ambiguity_guard_enabled,
    select_clarification_template,
)
from .story_import import (
    FetchedAsset,
    StoryAssetError,
    StoryImportError,
    StoryRoute,
    capture_story,
    event_memory_from_draft,
    merge_event_memory,
    parse_story_url,
    preview_event_memory,
    save_draft,
    story_content_digest,
    story_evidence_digest,
    update_review,
    validate_story_draft,
)
from .rollout import RolloutConfig, mode_is_active, mode_is_shadowing, resolve_rollout, rollout_as_dict

# ── 统一公共导出面（显式声明，避免 import * 时泄漏内部名） ────────────────
__all__ = [
    # 常量 / 客户端
    "TZ_CN", "TZ_JST", "DATA_DIR", "DB_PATH", "IMAGE_BASE_PATH", "MAX_HISTORY_LEN",
    "DEEPSEEK_API_KEY", "TAVILY_API_KEY", "ZHIPU_API_KEY", "SILICONFLOW_API_KEY",
    "client", "vision_client", "embedding_client", "np",
    "TOYA_QQ_ID", "SUPERUSER_QQ", "TRIGGER_NAMES",
    "ALLOWED_CHAT_GROUPS", "ALLOWED_CP_GROUPS", "ALLOWED_MEMORY_GROUPS", "TARGET_GROUPS", "WORDCLOUD_GROUPS",
    "GROUP_IMAGE_PERMISSIONS",
    # memory
    "BaseUserRecord", "GameData", "GroupRecord", "MemorySession",
    "Turn", "ConversationState", "ContextBlock", "ToolResult", "TOOL_STATUSES", "ResponseEnvelope",
    "MEMORY_DB", "MessageRow", "load_memory", "save_memory", "get_memory_key", "get_user_memory",
    "MessageReader", "delete_group_messages", "get_group_context", "open_message_reader",
    "parse_sqlite_timestamp", "record_bot_message", "record_message",
    # data
    "load_json_file", "load_prompt_template", "reload_assets", "find_data_path", "get_data_dir",
    "SCRIPT_DB", "REACTIONS_DB", "PROMPTS_DB", "DIRECTOR_DB",
    "DAILY_ROUTINE", "WL2_ROUTINE", "SONG_DATA", "RELATIONSHIP_DATA",
    "SLEEP_DB", "EVENT_MEMORY_DB",
    # life_state
    "AKITO_STATUS", "STATE_DURATION",
    "grant_safety_pass", "get_safe_until", "get_last_complaint", "set_last_complaint",
    "QueryIntent", "classify_query_intent",
    "get_daily_activity", "check_sleep_status", "get_festival_buff", "get_morning_run_buff",
    "get_sleep_buffer_buff", "get_toya_anchor", "parse_duration_and_content", "check_img_permission",
    "is_sleeping", "sleep_block",
    # api
    "ImageAnalysis",
    "call_deepseek_api", "call_deepseek_api_agent", "smart_search", "smart_search_result", "smart_search_detailed",
    "describe_image", "to_image_data", "embed_text",
    "expand_query_for_retrieval", "extract_json_block", "format_image_analysis_for_chat", "parse_json_object",
    "rerank_documents", "rescue_field", "rescue_tail_after_field",
    # context
    "RelationshipMatch", "find_relationship_match", "format_relationship_context",
    "get_random_examples", "get_base_persona", "reload_persona", "get_song_memories", "get_song_mention",
    "get_hybrid_relationship", "get_relevant_examples", "get_relevant_pjsk",
    # prompt_builder
    "PromptFrame", "JsonFieldSpec", "JsonSchemaSpec", "SharedPromptContext",
    "build_shared_prompt_context", "render_json_schema", "render_prompt_frame",
    "render_main_chat_prompt", "render_impression_analysis_prompt", "render_impression_reply_prompt",
    "render_impression_prompt", "render_auto_chat_prompt",
    # context orchestration
    "ContextOrchestrator", "ContextShadowReport", "build_context_blocks", "estimate_token_count", "select_context_for_mode", "shadow_context",
    "EventMemoryHit", "EventMemoryResult", "build_event_memory_context", "format_event_memory_context", "retrieve_event_memories",
    "AmbiguitySignals", "AmbiguityGuardDecision", "detect_ambiguity", "detect_ambiguity_signals",
    "evaluate_ambiguity_guard", "is_ambiguity_guard_enabled", "select_clarification_template",
    "choose_clarification_template",
    "FetchedAsset", "StoryRoute", "StoryImportError", "StoryAssetError", "parse_story_url", "capture_story",
    "validate_story_draft", "save_draft", "update_review", "event_memory_from_draft", "merge_event_memory",
    "preview_event_memory", "story_content_digest", "story_evidence_digest",
    "RolloutConfig", "mode_is_active", "mode_is_shadowing", "resolve_rollout", "rollout_as_dict",
    # time_awareness
    "record_bot_response", "build_time_gap_prompt",
    # retrieval
    "RetrievalContext", "RetrievalResult", "build_retrieval_context", "retrieve", "retrieve_result", "reload_indices",
    # observability
    "TurnTrace", "AutoReplyShadowReport", "new_request_id", "start_turn_trace", "get_turn_trace", "finish_turn_trace", "current_request_id",
    "record_intent", "record_context_sources", "record_context_shadow", "record_auto_reply_shadow", "evaluate_auto_reply_shadow", "record_event_memory", "record_fallback_reason", "record_ambiguity_guard", "record_rollout", "record_model_call", "record_parse_result",
    "record_repeat_detection", "record_memory_hit", "record_retry", "record_tool_call", "record_tool_route",
    "set_trace_stage", "snapshot_metrics", "reset_metrics",
]
