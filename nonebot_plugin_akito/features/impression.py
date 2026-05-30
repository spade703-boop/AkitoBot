import asyncio
import datetime
import json
import random
import re
import sqlite3
import time

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger

from ..core import (
    ALLOWED_CP_GROUPS,
    DB_PATH,
    PJSK_KNOWLEDGE_BASE,
    PROMPTS_DB,
    RELATIONSHIP_DATA,
    TZ_CN,
    client,
    get_base_persona,
    get_group_context,
    get_safe_until,
    get_user_memory,
    load_prompt_template,
)

# ================= 配置区域 =================

MODEL_NAME = "deepseek-v4-flash"

AUTO_CHAT_GROUPS = ALLOWED_CP_GROUPS

CHAT_PROBABILITY = 0.03

BLOCK_PREFIXES = ["/", "#", ".", "!", "！", "*", "-", "@"]
BLOCK_KEYWORDS = [
    "签到", "打卡", "个人信息", "日速", "时速", "help",
    "pjsk", "抽签", "娶群友", "透群友", "看看",
    "cn", "sn", "绑定", "解绑", "倍率",
    "存", "收下", "这是", "投喂", "增加",
    "开始进货", "停止进货", "开始收图",
    "看你的", "发张", "来张",
    "图库", "清单", "库存",
    "冬弥呢", "搭档呢", "冬弥在哪",
    "植入", "清除", "遗忘", "重置"
]

# ===========================================


async def is_in_auto_group(event: GroupMessageEvent) -> bool:
    return event.group_id in AUTO_CHAT_GROUPS

def save_my_response(group_id: str, bot_qq: str, content: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (group_id, user_id, nickname, content) VALUES (?, ?, ?, ?)",
            (group_id, bot_qq, "东云彰人", content)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"保存Bot回复失败: {e}")

# ================= 功能 1：默默记录群聊 =================
recorder = on_message(priority=1, block=False)
@recorder.handle()
async def _(event: GroupMessageEvent):
    if event.group_id not in AUTO_CHAT_GROUPS: return
    msg = event.get_plaintext().strip()
    if not msg or msg.startswith("/"): return
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO messages (group_id, user_id, nickname, content) VALUES (?, ?, ?, ?)",
        (str(event.group_id), str(event.user_id), event.sender.card or event.sender.nickname, msg))
    conn.commit()
    conn.close()

# ================= 功能 2：生成印象 =================
um_cmd = on_command("群印象", aliases={"评价我", "说说印象", "我的印象"}, rule=is_in_auto_group, priority=5, block=True)

@um_cmd.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    sender_id = str(event.user_id)
    group_id = str(event.group_id)
    sender_name = event.sender.card or event.sender.nickname

    target_id = sender_id
    target_name = sender_name
    is_querying_other = False

    for seg in event.original_message:
        if seg.type == "at":
            target_id = str(seg.data["qq"])
            if target_id != "all":
                is_querying_other = True
            break

    if is_querying_other and target_id == str(bot.self_id):
        reply_segment = MessageSegment.reply(event.message_id)
        refusals = [
            "喂，别想着查我对自己的印象。",
            "……啧，查我干嘛，你现在没事干？",
            "无可奉告。"
        ]
        await um_cmd.finish(reply_segment + random.choice(refusals))
        return

    if is_querying_other:
        try:
            member_info = await bot.get_group_member_info(group_id=int(group_id), user_id=int(target_id))
            target_name = member_info.get("card") or member_info.get("nickname") or f"用户{target_id}"
        except Exception as e:
            logger.error(f"获取被艾特成员信息失败: {e}")
            target_name = "那家伙"

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT COUNT(*) FROM messages WHERE group_id=? AND user_id=? AND length(content) > 2",
        (group_id, target_id)
    )
    target_msg_count = cursor.fetchone()[0]

    reply_segment = MessageSegment.reply(event.message_id)

    if target_msg_count < 10:
        conn.close()
        if is_querying_other:
            await um_cmd.finish(reply_segment + f"对 {target_name} 还没什么印象……让他多说点话吧。")
        else:
            await um_cmd.finish(reply_segment + "对你还没什么印象……多说点话吧。")
        return

    cursor.execute(
        "SELECT content FROM messages WHERE group_id=? AND user_id=? AND length(content) > 2 ORDER BY id DESC LIMIT 50",
        (group_id, target_id)
    )
    rows = cursor.fetchall()
    conn.close()

    history_text = "\n".join([f"【{target_name}】: {row[0]}" for row in rows[::-1]])

    persona = get_base_persona()
    is_wl2_active = False
    try:
        mem = get_user_memory(f"group_{group_id}")
        if any(item.get("id") == "WL2" for item in mem.get("temp_implants", [])):
            is_wl2_active = True
    except Exception as e:
        logger.error(f"WL2 状态获取失败: {e}")

    if is_wl2_active:
        logger.info("🔥 [Impression] 判定当前处于 WL2 模式，正在注入绝望剧本...")
        wl2_text = load_prompt_template("wl2_persona.txt")
        if wl2_text:
            persona += "\n" + wl2_text
        persona += "\n🎬【导演附加指导】：请基于上述 WL2 设定进行评价。你不关心群友的状态，评价可以体现一些冷漠和距离感。"

    system_prompt = f"""
    {persona}
    【任务目标】评价用户。阅读最近 **50条** 发言，给出符合人设的侧写评价。
    【回复要求】
    1. 符合"东云彰人"盐系男高中生人设。
    2. 用"对{target_name}的印象是..."开头。

    【================ 强制输出格式 (JSON) ================】
    {{
      "inner_os": "在这里先回忆一下这50条记录，吐槽一下这个人的日常表现。",
      "reply": "⚠️【强制开头】必须以"对{target_name}的印象是"起头，不得以任何其他词语开场。80字以内，纯对话文本，严禁夹带（括号动作描写）。语感随意慵懒、短句连贯，符合盐系男高中生口吻。"
    }}
    """

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"以下是群聊记录，其中【{target_name}】是你要评价的对象，[其他名字]是群里其他人。请严格基于【{target_name}】的实际发言内容来评价，不要把其他人说的话算在他头上。\n\n{history_text}"}
                ],
                temperature=1.1,
                presence_penalty=0.4,
                frequency_penalty=0.4,
                max_tokens=2048,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            ),
            timeout=60.0,
        )
        raw_result = response.choices[0].message.content
        final_reply = ""

        clean_json_str = raw_result
        json_match = re.search(r'\{[\s\S]*\}', raw_result)
        if json_match:
            clean_json_str = json_match.group(0)

        try:
            response_data = json.loads(clean_json_str)
            inner_os = response_data.get("inner_os", "")
            if inner_os:
                logger.info(f"📝【小彰评价OS】: {inner_os}")
            final_reply = response_data.get("reply", "")
            if not final_reply:
                final_reply = "（打量了你一下）……没什么好说的。"
        except json.JSONDecodeError:
            logger.warning(f"⚠️ 评价系统未输出标准JSON: {raw_result[:120]}")
            # 救援①：broken JSON 中提取 reply 字段（内部引号未转义场景）
            rescue = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_result)
            # 救援②：模型输出纯文本 "reply：..." 格式
            if not rescue:
                rescue = re.search(r'reply\s*[：:]\s*(.+)', raw_result, re.DOTALL)
            if rescue:
                final_reply = rescue.group(1).strip().strip('"')
                logger.info(f"🔧 评价救援成功: {final_reply[:60]}")
            else:
                final_reply = "（上下打量了你一下）……啧，没什么特别的印象。"

        save_my_response(group_id, str(bot.self_id), final_reply)

        thinking_time = random.uniform(3.0, 5.0)
        await asyncio.sleep(thinking_time)
        await um_cmd.finish(reply_segment + final_reply)
    except FinishedException:
        raise
    except Exception:
        await um_cmd.finish(reply_segment + "脑子短路了...")

# ================= 功能 3：随机插嘴 (AutoChat) =================
AUTO_CHAT_COOLDOWN = {}

random_chat = on_message(rule=is_in_auto_group, priority=99, block=False)

@random_chat.handle()
async def _(bot: Bot, event: GroupMessageEvent):
    now_ts = time.time()
    now = datetime.datetime.now(TZ_CN)
    if 0 <= now.hour < 6: return

    msg = event.get_plaintext().strip()
    if len(msg) < 2: return
    if any(msg.startswith(p) for p in BLOCK_PREFIXES): return
    if any(k in msg for k in BLOCK_KEYWORDS): return

    group_id = str(event.group_id)
    last_time = AUTO_CHAT_COOLDOWN.get(group_id, 0)
    if time.time() < get_safe_until():
        return
    if now_ts - last_time < 10:
        return

    if random.random() > CHAT_PROBABILITY: return

    AUTO_CHAT_COOLDOWN[group_id] = now_ts

    reply_segment = MessageSegment.reply(event.message_id)
    group_context = get_group_context(str(event.group_id), limit=50)
    current_user_name = event.sender.card or event.sender.nickname

    # 本地关键词白名单扫描
    relation_info = ""
    if RELATIONSHIP_DATA:
        for entry in RELATIONSHIP_DATA:
            for kw in entry.get("keywords", []):
                if kw in msg:
                    relation_info = entry.get("content", "")
                    break
            if relation_info:
                break

    cool_guy_filter = PROMPTS_DB.get("cool_guy_filter", "")

    h24 = now.hour
    if h24 < 6: period = "凌晨"
    elif h24 < 12: period = "上午"
    elif h24 == 12: period = "中午"
    elif h24 < 18: period = "下午"
    else: period = "晚上"
    h12 = 12 if h24 % 12 == 0 else h24 % 12
    time_str = f"{period}{h12}点{now.minute:02d}分"

    persona = get_base_persona()
    is_wl2_active = False
    try:
        mem = get_user_memory(f"group_{group_id}")
        if any(item.get("id") == "WL2" for item in mem.get("temp_implants", [])):
            is_wl2_active = True
    except Exception as e:
        logger.error(f"WL2 状态获取失败: {e}")

    if is_wl2_active:
        logger.info("🔥 [AutoChat] 判定当前处于 WL2 模式，正在注入绝望剧本...")
        wl2_text = load_prompt_template("wl2_persona.txt")
        if wl2_text:
            persona += "\n" + wl2_text
        persona += "\n🎬【导演附加指导】：请基于上述 WL2 设定进行潜水判断。你不关心群友的状态，回复可以体现一些冷漠和距离感。"

    BOT_NAMES = ["小彰", "彰人", "东云彰人"]
    is_directed_at_bot = any(name in msg for name in BOT_NAMES)

    if is_directed_at_bot:
        scene_desc = f'群友【{current_user_name}】直接在跟你说话，消息内容是："{msg}"'
        task_logic = f'''
    作为东云彰人，对方在直接跟你说话，你必须回应。保持盐系男高中生人设，用符合角色的方式直接回复【{current_user_name}】。
    1. 不需要判断"这话是不是对我说的"，直接进入回应。
    2. 根据消息内容决定态度：调侃就无语反击，问问题就冷淡回答，废话就简短怼回去。
    3. **必须**输出非空的reply。'''
        inner_os_guide = f'分析过程：对方【{current_user_name}】在直接跟我说话，内容是"{msg}"。思考一下用什么态度回比较符合人设。'
        user_content = f'请以东云彰人的身份直接回应【{current_user_name}】，对方在跟你说话。严格按JSON格式输出，禁止复读原话。'
    else:
        scene_desc = f'你正在群里潜水（旁观），群友【{current_user_name}】刚刚发了一条消息："{msg}"'
        task_logic = f'''
    作为群里潜水的成员（东云彰人），看心情决定是否插一句嘴。遵循以下法则：
    1. **首选当前消息**：评估"{msg}"有没有槽点，有就直接点评。
    2. **追溯机制**：如果这句话毫无意义，可以在上下文里找【{current_user_name}】本人刚才说过的有意义的话来承接，禁止接其他人的话题。
    3. **静默判定**：没意思且没值得接的，必须输出空字符串继续潜水。'''
        inner_os_guide = f'分析过程：1.这句话是对我说的吗？2."{msg}"有槽点吗？3.没意思的话，【{current_user_name}】本人刚才说过什么值得在意的事吗？4.决定是否插嘴。'
        user_content = f'你在群里潜水，看到【{current_user_name}】说了："{msg}"。这句话不是对你说的。决定是否插嘴点评，严格按JSON格式输出。'

    system_prompt = f"""
    【系统级绝对指令：潜水思维链与格式强制】
    {persona}
    【系统物理时间】当前时间是：{time_str}。绝对不可弄错时间。

    【场景】{scene_desc}
    【群聊上下文】\n{group_context}
    【人际资料】{relation_info}

    {PJSK_KNOWLEDGE_BASE}
    {cool_guy_filter}

    【任务目标与回复逻辑 (极其重要)】
    {task_logic}

    【================ 强制输出格式 (JSON) ================】
    你必须且只能输出合法的 JSON 格式。不要用 ```json 包裹！
    {{
      "inner_os": "{inner_os_guide}",
      "reply": "你实际发在群里的话。要求：1. 纯文本，极少用(动作)。2. 善用逗号连接短句，语感流畅。3. 绝不乱接别人的话。4. 旁观模式下如果决定不理，必须输出空字符串。"
    }}
    """

    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content}
                ],
                temperature=1.1,
                presence_penalty=0.4,
                frequency_penalty=0.6,
                max_tokens=2048,
                stream=False,
                extra_body={"thinking": {"type": "disabled"}},
            ),
            timeout=10.0,
        )

        raw_result = response.choices[0].message.content
        reply = ""

        clean_json_str = raw_result
        json_match = re.search(r'\{[\s\S]*\}', raw_result)
        if json_match:
            clean_json_str = json_match.group(0)

        try:
            response_data = json.loads(clean_json_str)
            inner_os = response_data.get("inner_os", "")
            if inner_os:
                logger.info(f"💦【小彰潜水OS】: {inner_os}")
            reply = response_data.get("reply", "").strip()

            clean_msg = msg.strip("。，！？.!?~ \n\r")
            clean_reply = reply.strip("。，！？.!?~ \n\r")

            if len(clean_reply) >= 4 and (clean_reply in clean_msg or clean_msg in clean_reply):
                logger.warning(f"⚠️ [AutoChat] 触发片段/缝合复读拦截！静音丢弃: {reply}")
                return

        except json.JSONDecodeError:
            logger.warning(f"⚠️ 插嘴系统未输出标准JSON: {raw_result[:120]}")
            # 救援：提取 reply 字段（最常见原因：inner_os 内部引号未转义）
            rescue_os = re.search(r'"inner_os"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_result)
            if rescue_os:
                logger.info(f"💦【小彰潜水OS（救援）】: {rescue_os.group(1)[:80]}")
            rescue = re.search(r'"reply"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_result)
            if rescue:
                reply = rescue.group(1).strip()
                logger.info(f"🔧 插嘴救援成功，reply={repr(reply[:40])}")
                # reply 为空 = 模型决定静默，走正常静默流程
            else:
                return

        if "念叨" in reply or "自言自语" in reply: return
        if not reply or reply.strip() == "……": return
        if len(reply) < 2: return

        save_my_response(str(event.group_id), str(bot.self_id), reply)

        base_delay = random.uniform(1.5, 3.5)
        typing_delay = len(reply) * 0.15
        total_delay = base_delay + typing_delay
        if total_delay > 8: total_delay = 8

        await asyncio.sleep(total_delay)
        await random_chat.finish(reply_segment + reply)
    except FinishedException:
        raise
    except Exception:
        pass
