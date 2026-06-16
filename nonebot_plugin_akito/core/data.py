"""数据加载与热重载：统一从 data/ 定位并读取 JSON / 文本资源，并提供 reload_assets() 原地热更新。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot.log import logger

from .paths import find_data_path as _find_data_path
from .paths import get_data_dir as _get_data_dir
from .retrieval_assets import build_pjsk_prompt_text, flatten_pjsk_knowledge

PJSK_KNOWLEDGE_BASE = ""
PJSK_INTRO = ""
PJSK_ENTRIES: list[dict] = []


def get_data_dir() -> Path:
    """返回第一个存在的数据根目录（写回文件的统一落点）；都不存在时回退 "data"。

    memory / time_awareness 等需要写文件的模块共用此函数，避免各自维护一份路径搜索逻辑。
    """
    return _get_data_dir()


# 公共别名：features/handlers 统一通过 from ..core import find_data_path 调用，避免直引 core 子模块
find_data_path = _find_data_path


def load_json_file(filename: str, default_data: Any = None) -> Any:
    """加载 JSON 数据文件；未找到或解析失败时回落到 default_data。"""
    path = _find_data_path(filename)
    if path:
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f)
            logger.info(f"✅ 成功加载 {filename}")
            return data
        except Exception as e:
            logger.error(f"❌ 加载 {filename} 失败: {e}")
    logger.warning(f"⚠️ 未找到 {filename}，已使用默认兜底数据。")
    return default_data


def load_prompt_template(filename: str) -> str:
    """读取文本模板文件内容；未找到或读取失败返回空串。"""
    path = _find_data_path(filename)
    if path:
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"⚠️ 读取模板 {filename} 失败: {e}")
    return ""


def _load_optional_json(filename: str) -> Any:
    """加载可选的拆分数据文件；不存在时静默返回 None（不打"未找到"警告）。"""
    if _find_data_path(filename) is None:
        return None
    return load_json_file(filename, None)


SCRIPT_DB       = load_json_file("akito_scripts.json", [])

# reactions 内容已拆分：gallery_text + greetings；
# fallback_poke 已移入 routine.json。akito_reactions.json 仅作旧 flat 布局兼容读取。
REACTIONS_DEFAULTS = {
    "save_img_replies": {},
    "send_img_angles": ["语气切入点：随意的发言，像是随手丢过去的。"],
    "greetings": {"morning": ["早。"], "night": ["晚安。"]},
}


def _load_reactions() -> dict:
    """合并加载 → 单一 REACTIONS_DB：gallery_text + greetings（akito_reactions.json 仅旧布局兼容）。"""
    return {
        **REACTIONS_DEFAULTS,
        **(_load_optional_json("akito_reactions.json") or {}),
        **(_load_optional_json("gallery_text.json") or {}),
        **(_load_optional_json("greetings.json") or {}),
    }


REACTIONS_DB    = _load_reactions()
SLEEP_DB        = load_json_file("akito_sleep.json", {
    "complaints": ["……吵死了……"],
    "sleep_replies_img": ["……困……"],
    "sleep_relation": ["【状态：困】\n动作：闭着眼。\n台词参考：……不知道……困……"],
    "sleep_search":   ["【状态：困】\n动作：闭着眼查手机。\n台词参考：……给你……呼……"],
    "sleep_mumbles":  ["……zzZ……"],
    "sleep_toya_radar": ["（正在熟睡中……）zzZ"],
    "sleep_save_img": ["……明天再存……zzZ"],
    "sleep_poke": ["（正在睡觉，完全没有反应）"],
    "sleep_inject_memory": ["（呼……呼……完全没听见……）zzZ"],
    "sleep_gallery_list": ["💤 正在睡觉，早上再来……"],
})
# prompts 已按用途拆分为 prompts_system + prompts_character，合并加载回单一 PROMPTS_DB（兼容未拆的旧单文件）
PROMPTS_DEFAULTS = {
    "system_header": "【系统级绝对指令】你是东云彰人，只输出合法JSON。",
    "reliable_mode": "", "cool_guy_filter": "",
    "toya_acting_guide": "风格：{selected}。",
    "toya_high_tension_guide": "风格：{selected}。",
    "vitality_guide": "", "memory_capture_rule": "", "tone_limiter": "",
    "schema_inner_os": "你的真实心理活动。",
    "schema_action": "角色的肢体动作或微表情。没有时留空。",
    "schema_dialogue": "角色实际说出的话，纯对话文本。",
    "memory_fusion_template": "【警告】特殊状态：{implant}。关系：{relationship}。",
    "memory_force_template":  "【警告】唯一真理：{implant}。",
}


def _load_prompts() -> dict:
    """合并加载 → 单一 PROMPTS_DB：prompts_system + prompts_character；都缺时回落旧单文件 akito_prompts.json。"""
    _sys = _load_optional_json("prompts_system.json")
    _char = _load_optional_json("prompts_character.json")
    if _sys is None and _char is None:
        return load_json_file("akito_prompts.json", PROMPTS_DEFAULTS)
    return {**PROMPTS_DEFAULTS, **(_sys or {}), **(_char or {})}


PROMPTS_DB      = _load_prompts()
DIRECTOR_DB     = load_json_file("akito_director.json", {
    "toya_directions": ["【侧重沉默】"],
    "dynamic_lexicon": {},
})
DAILY_ROUTINE   = load_json_file("akito_routine.json", {
    "late_night": [{"status": "正在睡觉。", "poke": ["……"]}],
})
WL2_ROUTINE     = load_json_file("wl2_routine.json", {"late_night": ["独自一人，在沉默中发呆。"]})
SONG_DATA         = load_json_file("akito_songs.json", {})
RELATIONSHIP_DATA = load_json_file("akito_relationships.json", [])


def get_pjsk_knowledge_base() -> str:
    """返回拼接好的 PJSK 黑话知识库全文（检索不可用时的兜底全量注入文本）。

    str 是不可变类型，热重载重新赋值后，其他模块在 import 时拿到的旧引用不会更新
    （见 PROJECT_SPEC §11 模式 B），因此消费方必须通过本函数在调用时读取最新值。
    """
    return PJSK_KNOWLEDGE_BASE


def get_pjsk_intro() -> str:
    """返回 PJSK 语境锁前言（检索结果注入时始终在最前）。热重载理由同 get_pjsk_knowledge_base。"""
    return PJSK_INTRO


def init_pjsk_knowledge() -> None:
    """加载并拼装 PJSK 黑话知识库到 PJSK_KNOWLEDGE_BASE + PJSK_INTRO + PJSK_ENTRIES（缺文件则保持空串/空列表）。"""
    global PJSK_KNOWLEDGE_BASE, PJSK_INTRO
    data = load_json_file("pjsk_knowledge.json", {})
    if not data:
        return
    try:
        intro = str(data.get("introduction", "") or "").strip()
        flat = flatten_pjsk_knowledge(data, include_drafts=False)

        lines: list[str] = []
        if intro:
            lines.append(intro)
        if flat:
            lines.append("")
            for entry in flat:
                lines.append(build_pjsk_prompt_text(entry))

        # 全部解析成功后才更新，异常时保留旧数据；
        # PJSK_ENTRIES 是 list → 模式 A 原地更新，已持有引用的模块（context/retrieval）即时生效
        PJSK_KNOWLEDGE_BASE = "\n".join(lines).strip()
        PJSK_INTRO = intro
        PJSK_ENTRIES.clear()
        PJSK_ENTRIES.extend(flat)
    except Exception as e:
        logger.error(f"❌ PJSK黑话库拼装失败: {e}")


init_pjsk_knowledge()


def reload_assets() -> int:
    """原地热更新所有数据资源，对已导入该模块变量的引用立即生效（无需重启）。

    Returns:
        成功重载的配置组数（供 `重载配置` 指令回执动态显示，避免写死过时数字）。
    """
    count = 0
    for target, filename, default in [
        (DIRECTOR_DB,     "akito_director.json",  {}),
        (DAILY_ROUTINE,   "akito_routine.json",   {}),
        (WL2_ROUTINE,     "wl2_routine.json",     {}),
        (SONG_DATA,       "akito_songs.json",     {}),
    ]:
        new = load_json_file(filename, default)
        target.clear()
        target.update(new)
        count += 1

    # reactions / prompts 走合并加载（拆分文件 → 合回单一 DB），保持 §11 模式 A
    REACTIONS_DB.clear()
    REACTIONS_DB.update(_load_reactions())
    PROMPTS_DB.clear()
    PROMPTS_DB.update(_load_prompts())
    count += 2

    new_scripts = load_json_file("akito_scripts.json", [])
    SCRIPT_DB.clear()
    SCRIPT_DB.extend(new_scripts)
    count += 1

    new_rels = load_json_file("akito_relationships.json", [])
    RELATIONSHIP_DATA.clear()
    RELATIONSHIP_DATA.extend(new_rels)
    count += 1

    new_sleep = load_json_file("akito_sleep.json", {})
    SLEEP_DB.clear()
    SLEEP_DB.update(new_sleep)
    count += 1

    try:
        from ..features.random_paro import reload_paro_data
        reload_paro_data()
        count += 1
    except Exception as e:
        logger.debug(f"🔄 random_paro 热重载跳过: {e}")

    try:
        from ..features.random_keyword import reload_keyword_data
        reload_keyword_data()
        count += 1
    except Exception as e:
        logger.debug(f"🔄 random_keyword 热重载跳过: {e}")

    init_pjsk_knowledge()
    count += 1

    try:
        from .retrieval import reload_indices
        loaded = reload_indices()
        count += 1
        logger.debug(f"🔄 检索引擎索引已刷新（{loaded} 语料可用）")
    except Exception as e:
        logger.debug(f"🔄 检索索引重载跳过（无 numpy/无配置等，属正常降级）: {e}")

    logger.info(f"🔄 所有数据资源已热重载完成（{count} 组）")
    return count
