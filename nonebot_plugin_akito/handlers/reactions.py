"""被动反应：彰冬雷达（冬弥位置推断）、戳一戳互动、深夜自我吐槽。"""

import asyncio
import datetime
import json
import random
import re
import time

from nonebot import on, on_command, on_notice
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import GroupMessageEvent, NoticeEvent, PokeNotifyEvent
from nonebot.log import logger

from ..core import (
    AKITO_STATUS,
    ALLOWED_CHAT_GROUPS,
    ALLOWED_CP_GROUPS,
    PROMPTS_DB,
    REACTIONS_DB,
    RELATIONSHIP_DATA,
    SLEEP_DB,
    TZ_CN,
    call_deepseek_api,
    get_base_persona,
    get_daily_activity,
    get_last_complaint,
    get_memory_key,
    get_safe_until,
    get_user_memory,
    grant_safety_pass,
    is_sleeping,
    set_last_complaint,
    sleep_block,
    smart_search,
)

# --- 3. 彰冬雷达 — 共享核心逻辑 ---

async def get_toya_location_reply(user_mem: dict, user_question: str = "冬弥现在在哪？") -> str:
    """
    Mode 1 共享核心：根据彰人当前 routine 推断冬弥位置。
    由 toya_status_cmd 和 chat.py 雷达拦截器共同调用。
    返回空字符串表示"无声无息"（调用方不发送任何消息）。
    """
    now = datetime.datetime.now(TZ_CN)

    # WL2 状态：直接返回决绝台词
    if any(item.get("id") == "WL2" for item in user_mem.get("temp_implants", [])):
        grant_safety_pass(5)
        return random.choice([
            "……那家伙的事，现在跟我没关系。别在我面前提他。",
            "（烦躁地抓了抓头发）不知道。他去哪、做什么，都已经是我管不着的事了。",
            "……他已经不唱歌了。你问我也没用。",
            "……他不在。以后也都只有我一个人唱。听懂了就闭嘴。",
            "他早就把音乐当儿戏放弃了。……别再跟我提他。",
        ])

    # 凌晨 0-6：睡觉，50% 概率无声无息
    result = sleep_block("sleep_toya_radar", silent_chance=0.5,
                         fallback="（正在熟睡中，完全没有听到你在问冬弥的事……）zzZ")
    if result is None:
        return ""
    if result:
        await asyncio.sleep(2)
        grant_safety_pass(5)
        return result

    # 取当前生物钟状态
    get_daily_activity(now.hour, now.weekday(), now.minute)
    raw_content = AKITO_STATUS["cached_content"]
    current_action = raw_content.get("status", raw_content) if isinstance(raw_content, dict) else raw_content

    # 搜索互动细节
    search_result = ""
    if "睡" not in current_action and "发呆" not in current_action:
        try:
            search_result = await smart_search(
                f"Project Sekai 东云彰人 青柳冬弥 {current_action} 互动细节"
            )
        except Exception as e:
            logger.debug(f"🔍 雷达搜索失败，忽略: {e}")

    # 冬弥人设档案（从内存读，不重复读文件）
    toya_profile = ""
    for entry in RELATIONSHIP_DATA:
        if "冬弥" in entry.get("keywords", []):
            toya_profile = entry.get("content", "")
            break

    # 行为骰子
    behavior_seeds = REACTIONS_DB.get("behavior_seeds", [])
    if random.random() < 0.7 and behavior_seeds:
        guidance_instruction = (
            f"本次生成的**强制核心行为**：{random.choice(behavior_seeds)}\n"
            "请基于这个行为来描写他的状态，**不要写他在喝咖啡**。"
        )
    else:
        guidance_instruction = "本次自由发挥，但请注意：**除非场景是在咖啡店，否则禁止描写冬弥在喝咖啡**。"

    # 用 toya_radar 模板（来自 PROMPTS_DB，可热更新）
    radar_template = PROMPTS_DB.get(
        "toya_radar",
        "{base_persona}\n"
        "# 【当前绝对前提】你正在做这件事：{current_action}\n"
        "# 【用户提问】{user_question}\n"
        "请合理解释冬弥的位置状态，必须和你在同一个场景或有关联！\n"
        "# 【参考资料】冬弥人设：{toya_profile}\n搜索灵感：{search_result}",
    )
    final_system_prompt = (
        radar_template
        .replace("{base_persona}", get_base_persona())
        .replace("{current_action}", current_action)
        .replace("{user_question}", user_question)
        .replace("{toya_profile}", toya_profile)
        .replace("{search_result}", search_result)
    )
    # 行为骰子追加在模板之后（保证必然生效，不依赖模板里有无 {guidance} 占位符）
    final_system_prompt += f"\n\n🎮【剧本导演指令】{guidance_instruction}"
    # 显式 JSON schema（兼容模板里没有格式说明的情况）
    final_system_prompt += (
        '\n\n【强制输出格式 (JSON)】你必须且只能输出合法 JSON，不要用 ```json 包裹：\n'
        '{"inner_os": "你的心理活动，简短", "reply": "发到群里的话，纯文本"}'
    )

    messages = [
        {"role": "system", "content": final_system_prompt},
        *user_mem.get("history", []),
        {"role": "user", "content": user_question},
    ]

    try:
        raw_result = await call_deepseek_api(messages, force_json=True)
        reply = ""
        json_match = re.search(r'\{[\s\S]*\}', raw_result)
        if json_match:
            try:
                data = json.loads(json_match.group(0))
                inner_os = data.get("inner_os", "")
                if inner_os:
                    logger.info(f"🎭【雷达OS】: {inner_os}")
                reply = data.get("reply", "")
            except Exception as e:
                logger.debug(f"🎭 雷达 JSON 解析失败，回退原文: {e}")
        if not reply:
            reply = raw_result

        grant_safety_pass(10)
        return reply.strip()

    except Exception as e:
        logger.error(f"冬弥雷达生成失败: {e}")
        action_name = current_action.split("。")[0].replace("正在", "")
        grant_safety_pass(5)
        return f"啧……那家伙？就在我旁边陪我{action_name}呢。"


# --- 3b. 彰冬雷达指令（薄壳，调用共享函数）---
toya_status_cmd = on_command("冬弥呢", aliases={"搭档呢", "冬弥在哪", "冬弥在干嘛", "冬弥去哪了"}, priority=5, block=True)
@toya_status_cmd.handle()
async def _(event: Event):
    if isinstance(event, GroupMessageEvent) and event.group_id not in ALLOWED_CP_GROUPS:
        return
    mem = get_user_memory(get_memory_key(event))
    reply = await get_toya_location_reply(mem)
    if reply:
        await toya_status_cmd.finish(reply)


# --- 4. 戳一戳互动 ---
poke = on_notice()
@poke.handle()
async def _(bot: Bot, event: NoticeEvent):
    is_poke = isinstance(event, PokeNotifyEvent) or getattr(event, "sub_type", "") == "poke"
    if not is_poke or str(getattr(event, "target_id", "")) != str(bot.self_id): return
    group_id = getattr(event, "group_id", None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS: return

    mem = get_user_memory(get_memory_key(event))
    if any(item.get("id") == "WL2" for item in mem.get("temp_implants", [])):
        grant_safety_pass(5)
        await poke.finish(random.choice([
            "……别碰我，我现在没心情理人。",
            "……干什么？",
            "有事快说。",
            "啧，你很闲吗？"
        ]))

    result = sleep_block("sleep_poke", silent_chance=0.0,
                         fallback="（这只松饼正在睡觉，没有理会你的戳）")
    if result:
        grant_safety_pass(5)
        await poke.finish(result)
        return

    now_full = datetime.datetime.now(TZ_CN)
    get_daily_activity(now_full.hour, now_full.weekday(), now_full.minute)
    current_state = AKITO_STATUS.get("cached_content", "")
    reactions = current_state.get("poke", []) if isinstance(current_state, dict) else []
    if not reactions: reactions = REACTIONS_DB.get("fallback_poke", ["喂，别乱戳啊。"])

    await asyncio.sleep(random.uniform(0.5, 1.5))
    grant_safety_pass(5)
    await poke.finish(random.choice(reactions))


# --- 5. 自身消息监控 (自我吐槽) ---
async def is_self_message(bot: Bot, event: Event) -> bool:
    """规则：判断是否是 bot 自己发出的消息（post_type == message_sent）。"""
    return getattr(event, "post_type", "") == "message_sent"

self_monitor = on(rule=is_self_message, priority=99, block=False)
@self_monitor.handle()
async def _(bot: Bot, event: Event):
    group_id = getattr(event, "group_id", None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS:
        return

    if not is_sleeping() or time.time() < get_safe_until() or time.time() - get_last_complaint() < 10:
        return
    # 该群30秒内由超管触发过回复，跳过抱怨（防止测试时刷抱怨）
    # 使用 per-group 时间戳，避免超管在 A 群说话后误压制 B 群的抱怨
    _su_group_id = str(getattr(event, "group_id", "private"))
    _su_times = AKITO_STATUS.get("last_superuser_trigger_time", {})
    if time.time() - _su_times.get(_su_group_id, 0) < 30:
        return
    set_last_complaint(time.time())
    await asyncio.sleep(random.uniform(2.0, 4.0))
    grant_safety_pass(10)
    try:
        group_id = getattr(event, "group_id", None)
        if group_id: await bot.send_group_msg(group_id=group_id, message=random.choice(SLEEP_DB.get("complaints", ["……困……"])))
    except Exception as e:
        logger.debug(f"😴 自我吐槽发送失败，忽略: {e}")
