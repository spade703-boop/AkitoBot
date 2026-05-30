"""数据加载与热重载：统一从 data/ 定位并读取 JSON / 文本资源，并提供 reload_assets() 原地热更新。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot.log import logger

PJSK_KNOWLEDGE_BASE = ""


_DATA_SEARCH_DIRS = [
    "/app/akito_bot/data",
    "data",
    "/akito_bot/data",
    ".",
]


def _find_data_path(filename: str) -> Path | None:
    for base in _DATA_SEARCH_DIRS:
        p = Path(base) / filename
        if p.exists():
            return p
    return None


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
    path = _find_data_path(filename)
    if path:
        try:
            return path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(f"⚠️ 读取模板 {filename} 失败: {e}")
    return ""


SCRIPT_DB       = load_json_file("akito_scripts.json", [])
REACTIONS_DB    = load_json_file("akito_reactions.json", {
    "complaints": ["……吵死了……"],
    "sleep_replies_img": ["……困……"],
    "behavior_seeds": ["冬弥在发呆"],
    "save_img_replies": {},
    "send_img_angles": ["语气切入点：随意的发言，像是随手丢过去的。"],
    "greetings": {"morning": ["早。"], "night": ["晚安。"]},
    "fallback_poke": ["喂，别乱戳啊。"],
    "sleep_relation": ["【状态：困】\n动作：闭着眼。\n台词参考：……不知道……困……"],
    "sleep_search":   ["【状态：困】\n动作：闭着眼查手机。\n台词参考：……给你……呼……"],
})
PROMPTS_DB      = load_json_file("akito_prompts.json", {
    "system_header": "【系统级绝对指令】你是东云彰人，只输出合法JSON。",
    "reliable_mode": "", "cool_guy_filter": "",
    "toya_acting_guide": "风格：{selected}。",
    "toya_high_tension_guide": "风格：{selected}。",
    "toya_radar": "", "toya_location_guide": "",
    "vitality_guide": "", "memory_capture_rule": "", "tone_limiter": "",
    "schema_inner_os": "你的真实心理活动。",
    "schema_action": "角色的肢体动作或微表情。没有时留空。",
    "schema_dialogue": "角色实际说出的话，纯对话文本。",
    "memory_fusion_template": "【警告】特殊状态：{implant}。关系：{relationship}。",
    "memory_force_template":  "【警告】唯一真理：{implant}。",
})
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


def init_pjsk_knowledge():
    global PJSK_KNOWLEDGE_BASE
    data = load_json_file("pjsk_knowledge.json", {})
    if not data:
        return
    try:
        text = data.get("introduction", "") + "\n\n"
        for item in data.get("knowledge_list", []):
            text += f"{item['category']}\n"
            for entry in item.get("entries", []):
                text += f"   {entry}\n"
            text += "\n"
        PJSK_KNOWLEDGE_BASE = text.strip()
    except Exception as e:
        logger.error(f"❌ PJSK黑话库拼装失败: {e}")


init_pjsk_knowledge()


def reload_assets():
    """原地热更新所有 JSON 数据文件，对已导入该模块变量的引用立即生效（无需重启）。"""
    for target, filename, default in [
        (REACTIONS_DB,    "akito_reactions.json", {}),
        (PROMPTS_DB,      "akito_prompts.json",   {}),
        (DIRECTOR_DB,     "akito_director.json",  {}),
        (DAILY_ROUTINE,   "akito_routine.json",   {}),
        (WL2_ROUTINE,     "wl2_routine.json",     {}),
        (SONG_DATA,       "akito_songs.json",     {}),
    ]:
        new = load_json_file(filename, default)
        target.clear()
        target.update(new)

    new_scripts = load_json_file("akito_scripts.json", [])
    SCRIPT_DB.clear()
    SCRIPT_DB.extend(new_scripts)

    new_rels = load_json_file("akito_relationships.json", [])
    RELATIONSHIP_DATA.clear()
    RELATIONSHIP_DATA.extend(new_rels)

    try:
        from ..features.random_paro import reload_paro_data
        reload_paro_data()
    except Exception:
        pass

    try:
        from ..features.random_keyword import reload_keyword_data
        reload_keyword_data()
    except Exception:
        pass

    init_pjsk_knowledge()
    logger.info("🔄 所有 JSON 数据文件已热重载完成")
