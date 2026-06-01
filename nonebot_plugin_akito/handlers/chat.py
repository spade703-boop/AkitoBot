"""主对话引擎：触发判定、消息组装与发送、会话锁、图片识别与搜索 Agent 调度。"""

import asyncio
import datetime
import json
import random
import re
import time

from nonebot import on_message
from nonebot.adapters import Bot, Event, Message
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import EventMessage
from nonebot_plugin_alconna import Image, Reply, Text, UniMessage
from nonebot_plugin_htmlrender import md_to_pic

from ..core import (
    AKITO_STATUS,
    ALLOWED_CHAT_GROUPS,
    DIRECTOR_DB,
    MAX_HISTORY_LEN,
    PJSK_KNOWLEDGE_BASE,
    PROMPTS_DB,
    SUPERUSER_QQ,
    TOYA_QQ_ID,
    TRIGGER_NAMES,
    TZ_CN,
    TZ_JST,
    WL2_ROUTINE,
    build_time_gap_prompt,
    call_deepseek_api,
    call_deepseek_api_agent,
    check_sleep_status,
    describe_image,
    get_base_persona,
    get_daily_activity,
    get_festival_buff,
    get_group_context,
    get_hybrid_relationship,
    get_memory_key,
    get_morning_run_buff,
    get_random_examples,
    get_sleep_buffer_buff,
    get_song_memories,
    get_user_memory,
    grant_safety_pass,
    record_bot_response,
    save_memory,
    smart_search,
    to_image_data,
)

try:
    from ..features.director import build_director_note
except ImportError:
    build_director_note = None


AGENT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_internet",
            "description": (
                "搜索互联网上的实时、客观信息。\n\n"
                "【必须调用】：用户询问具体事实/数据时，例如：赛事结果、天气预报、"
                "某人的实际动态、商品价格、新闻事件、专业知识等客观存在的信息。\n\n"
                "【禁止调用】：以下情况直接以角色身份回答，不搜索——\n"
                "- 普通聊天、问候、调侃、玩梗\n"
                "- 询问你对某事的看法、感受或个人经历\n"
                "- 关于东云彰人、冬弥、VBS、PJSK 世界观的角色设定问题\n"
                "- 用户在和你进行 RP 互动"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "精炼后的搜索关键词，3-10 字，去掉称呼和语气词"
                    }
                },
                "required": ["query"]
            }
        }
    }
]


async def starts_with_trigger(event: Event) -> bool:
    """消息匹配规则：是否以触发名（东云小彰 / 小彰）开头，且群在白名单内。"""
    group_id = getattr(event, 'group_id', None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS:
        return False
    try: text = event.get_plaintext().strip()
    except AttributeError: text = event.get_message().extract_plain_text().strip()

    return any(text.lower().startswith(name.lower()) for name in TRIGGER_NAMES)


async def smart_finish(matcher: Matcher, result: str) -> None:
    """统一发送回复：含图则转 UniMessage，超长(>800)转图片，否则纯文本。"""
    if not result: return
    grant_safety_pass(8)
    result = result.strip()
    if not result: return  # strip 后为空（原始返回全是空白）也不发
    img_pattern = r"!\[.*?\]\((https?://.*?)\)"
    images = re.findall(img_pattern, result)

    if images:
        msg = UniMessage()
        clean_text = re.sub(img_pattern, "", result).strip()
        if clean_text: msg += Text(clean_text + "\n")
        for img_url in images: msg += Image(url=img_url)
        await matcher.finish(msg)
        return

    if len(result) > 800:
        try:
            img_data = await md_to_pic(result.replace("•", "  *"), width=800)
            await matcher.finish(UniMessage(Image(raw=img_data)))
        except Exception: await matcher.finish(result)
    else:
        await matcher.finish(result)


async def get_uni_reply(reply: Reply, event: Event, bot: Bot) -> UniMessage:
    """将被回复消息(Reply)转成 UniMessage，供后续解析引用内容。"""
    if reply.msg is None: raise ValueError("回复为空")
    if isinstance(reply.msg, str): return UniMessage([Text(reply.msg)])
    elif isinstance(reply.msg, Message): return await UniMessage.generate(message=reply.msg, event=event, bot=bot)


chat = on_message(rule=starts_with_trigger, priority=10, block=True)

SESSION_LOCKS = {}


def get_session_lock(session_key: str) -> asyncio.Lock:
    """取 / 建某会话的 asyncio 锁，保证同一会话的消息串行处理。"""
    if session_key not in SESSION_LOCKS:
        SESSION_LOCKS[session_key] = asyncio.Lock()
    return SESSION_LOCKS[session_key]


@chat.handle()
async def _(event: Event, bot: Bot, message: Message = EventMessage(), raw_message: Message = EventMessage()):
    session_key = get_memory_key(event)
    session_lock = get_session_lock(session_key)

    try:
      async with session_lock:
        uni_message = await UniMessage.generate(message=message, event=event, bot=bot)

        # --- 0. 溯源回复消息 (解决识图盲区) ---
        has_reply = False
        reply_target_is_toya = False
        origin_text, origin_sender = "", ""

        if getattr(event, "reply", None):
            has_reply = True
            try:
                origin_msg_id = event.reply.message_id
                origin_msg = await bot.get_msg(message_id=origin_msg_id)
                origin_text = origin_msg.get('message', [])
                origin_sender = origin_msg.get('sender', {}).get('nickname', '未知用户')

                if str(origin_msg.get('sender', {}).get('user_id')) == TOYA_QQ_ID:
                    reply_target_is_toya = True

                img_url = ""
                if isinstance(origin_text, list):
                    for seg in origin_text:
                        if isinstance(seg, dict) and seg.get('type') == 'image':
                            img_url = seg.get('data', {}).get('url', '')
                            if img_url: break
                elif isinstance(origin_text, str):
                    match = re.search(r'\[CQ:image,.*?url=(https?[^,\]]+)', origin_text)
                    if match: img_url = match.group(1)

                if img_url:
                    uni_message = UniMessage([Image(url=img_url)]) + uni_message
                    logger.info(f"📸 成功在回复溯源中抓取到隐藏图片: {img_url[:30]}...")

            except Exception as e:
                logger.error(f"提取回复原消息失败: {e}")

        name_stripped = False
        plain_text_content = ""
        current_image_identity = ""
        has_image = False

        # --- 1. 解析文本与视觉 ---
        for seg in uni_message:
            if isinstance(seg, Text) and seg.text.strip() != "":
                text = seg.text
                if not name_stripped:
                    clean_text = text.lstrip()
                    for name in TRIGGER_NAMES:
                        if clean_text.lower().startswith(name.lower()):
                            text = clean_text[len(name):].lstrip()
                            name_stripped = True
                            break
                if text.strip() != "": plain_text_content += text

            elif isinstance(seg, Image):
                has_image = True
                try:
                    img_data = await to_image_data(seg)
                    raw_code = await describe_image(img_data)
                    if raw_code:
                        current_image_identity = raw_code.strip()
                        logger.info(f"👁️ 视觉系统成功将画面传递给大脑: {current_image_identity}")
                except Exception as e:
                    logger.error(f"视觉解析后赋值失败: {e}")

        # --- 2. 睡眠拦截 ---
        _is_superuser = str(event.get_user_id()) == SUPERUSER_QQ
        if _is_superuser:
            sleep_instruction = ""      # 管理员：无睡眠提示注入
        else:
            should_block, sleep_instruction = check_sleep_status(plain_text_content)
            if should_block:
                if sleep_instruction == "ignore": await chat.finish()
                else:
                    await asyncio.sleep(2)
                    grant_safety_pass(5)
                    await chat.finish(sleep_instruction)

        if not plain_text_content and not current_image_identity:
            await chat.finish("干嘛……")

        # --- 3. 时间 ---（搜索意图由 LLM 通过 Function Calling 自主决定，见第 9 步）
        search_result = ""

        now_time = datetime.datetime.now(TZ_CN)
        now_jst  = datetime.datetime.now(TZ_JST)
        hour_24 = now_time.hour
        jst_h = now_jst.hour
        if jst_h < 6: period = "凌晨"
        elif jst_h < 12: period = "上午"
        elif jst_h == 12: period = "中午"
        elif jst_h < 18: period = "下午"
        else: period = "晚上"
        hour_12 = jst_h % 12 or 12
        current_time = f"{now_jst.year}年{now_jst.month}月{now_jst.day}日 {period}{hour_12}点{now_jst.minute:02d}分 (24小时制: {now_jst.strftime('%H:%M')} JST)"

        daily_status = get_daily_activity(now_time.hour, now_time.weekday(), now_time.minute)
        festival_buff = get_festival_buff(now_jst)
        morning_run_buff = get_morning_run_buff(hour_24)
        sleep_buffer_buff = get_sleep_buffer_buff(hour_24, now_time.minute)
        unique_key = get_memory_key(event)
        user_mem = get_user_memory(unique_key)

        if any(item.get("id") == "WL2" for item in user_mem.get("temp_implants", [])):
            if 0 <= hour_24 < 6: time_key = "late_night"
            elif 6 <= hour_24 < 12: time_key = "morning"
            elif 12 <= hour_24 < 14: time_key = "noon"
            elif 14 <= hour_24 < 18: time_key = "afternoon"
            else: time_key = "night"

            rnd = random.Random(now_time.day * 24 + hour_24)
            routine_pool = WL2_ROUTINE.get(time_key, ["独自一人，在沉默中发呆。"])
            chosen_wl2_routine = rnd.choice(routine_pool)
            daily_status = f"【当前状态】{chosen_wl2_routine}"

        # --- 4. 交互对象与中转站逻辑 ---
        user_id = str(event.get_user_id())
        sender_nickname = event.sender.card or event.sender.nickname or f"用户{user_id}"
        is_talking_to_toya = (user_id == TOYA_QQ_ID)

        interact_instruction = ""
        if is_talking_to_toya:
            interact_instruction = f"🛑【交互对象锁定】对话对象是 **青柳冬弥本人** (ID: {user_id})。切换至 [搭档/CP模式]，语气信任、护短。"
        elif reply_target_is_toya:
            interact_instruction = f"""
            🛑【交互对象锁定：中转站模式】
            用户 **{sender_nickname}** 引用了 **青柳冬弥** 的话。
            必须执行"两段式"回复：1. 先对 {sender_nickname} 嫌弃吐槽，让他少管闲事。 2. 无视群友，直接针对引用的冬弥的话做出包容反应。
            """
        elif "冬弥" in plain_text_content:
            interact_instruction = f"""
            🛑【交互对象锁定：涉冬弥话题模式】
            群友 **{sender_nickname}** 提到了"冬弥"。⚠️ 开启【极度双标/护短】判定：
            1. 如果群友在【报告冬弥的状况】（如：他在楼下摔倒了/他不舒服）：对报告状态的群友表现出被打扰的急躁，但难掩关心（"啧，他在哪？"）。⚠️你必须结合你当前的【生物钟状态】做出【合理且克制的急切反应】。
             - 严禁写成夸张的偶像剧（绝对不要写"冲出教室"、"发疯般地跑"等降智描写）！
             - 正确的动作参考：上课时（眉头紧锁，举手跟老师借口去洗手间然后快步离开）；睡觉时（烦躁地抓着头发爬起来，套上外套就走）；街头练习时（停下动作，直接拎起包）。
             - 在动作中找到冬弥后，态度极其自然地切入【照顾模式】。不准大惊小怪，而是语气放缓、压低声音询问，绝对不能对虚弱的冬弥说重话（例如："……撞到哪了？我看看。"）。
            2. 如果群友在括号里【模拟冬弥的动作/台词】：直接把这当成冬弥本人的互动！无视群友，对"冬弥"展现你的占有欲和特有的包容，严禁对"冬弥"粗暴！
            3. 如果只是普通提及：用护短的酷哥态度回应群友。
               🚫【防复读强制令】：绝对禁止使用"哈？你脑子没问题吧"、"少拿我和那家伙瞎起哄"这类套话！你必须根据群友说的【具体内容】给出针对性的不同反应！
               可用的反应方向：沉默不接话、反问对方为什么问、用当前状态岔开话题、简短确认然后立刻转移、表现出被打扰的轻微烦躁但不重复固定句式。
            """
        elif has_reply:
            if current_image_identity:
                interact_instruction = f"🛑【交互对象锁定：识图模式】对象是 {sender_nickname}。他回复并让你看一张图片。请你保持态度，对图片内容进行吐槽或评价。"
            else:
                interact_instruction = f"🛑【交互对象锁定：旁观模式】对象是 {sender_nickname}。他给你看了路人 {origin_sender} 的话。保持酷哥态度吐槽即可。"
        else:
            interact_instruction = f"""🛑【交互对象锁定】对话对象是普通群友 **{sender_nickname}**。保持 [独立街头歌手/直率/嫌麻烦但有教养] 的态度。\n💡【你的行为准则（请顺应本能反应）】：\n1. 【应对调侃】：保持情绪稳定。用无语的叹气、平淡的反问（"你很闲吗"）或直球承认来终结话题。\n2. 【应对倒霉/送礼】：遇到群友沉船就顺毛安抚两句；收到礼物就叹着气收下（"给我这个干嘛，谢了"），保持游刃有余。\n3. 【应对使唤】：你是有自己生活重心（练歌/打工）的独立男高。面对群友的跑腿要求，优先顺从你的不耐烦：直接冷漠拒绝（"你自己去。"），或用自己的日程当挡箭牌（"没空，正要去练歌"）。仅在心情极好或顺路时，才会勉强帮个小忙（"啧，放那吧我等下弄"）。\n🎯 核心语感：干脆利落的短句。把注意力放在你自己的事上，而不是被群友牵着鼻子走。"""

        relationship_context = await get_hybrid_relationship(plain_text_content)
        script_examples = get_random_examples(num=5)
        group_id = getattr(event, 'group_id', None)
        group_context = get_group_context(group_id) if group_id else ""
        time_gap_awareness = build_time_gap_prompt(group_id) if group_id else ""
        if time_gap_awareness and user_mem.get("history"):
            # 将旧对话历史「降级」为背景摘要，注入 time_gap_awareness 末尾
            # 而非直接清空——这样 AI 被问起「之前的事」时仍能正常引用，
            # 但不会把旧历史当作活跃对话继续接着聊
            past_lines = []
            for m in user_mem["history"][-8:]:
                role_label = "（小彰）" if m["role"] == "assistant" else "（对方）"
                content = m["content"]
                try:
                    parsed = json.loads(content)
                    text = str(parsed.get("reply") or parsed.get("dialogue") or content)
                except Exception:
                    text = str(content)
                if m["role"] == "user":
                    text = re.sub(r'^\[.+?\]:\s*', '', text)
                past_lines.append(f"{role_label}{text[:60]}")
            if past_lines:
                time_gap_awareness += (
                    "\n📚【上次对话摘要（已是过去的话题，仅供参考）】：\n"
                    + "\n".join(past_lines)
                    + "\n↑ 以上是旧话题。被问起时可用「那会儿」「之前」自然带过，不要主动续接。\n"
                )
            user_mem["history"] = []
            logger.info(f"⏱️ [TimeAwareness] 群 {group_id} 长间隔：旧历史已压缩为背景注释")

        # --- 6. 记忆融合引擎 ---
        implant_context = ""
        implants = user_mem.get("temp_implants", [])
        valid_implants = [m for m in implants if time.time() < m.get("expire_at", m.get("expire_time", 0))]
        user_mem["temp_implants"] = valid_implants

        if valid_implants:
            details = [f"- {m['content']} (剩余 {int((m.get('expire_at', m.get('expire_time', 0)) - time.time()) / 60)} 分钟)" for m in valid_implants]
            combined = "\n".join(details)
            implant_context = f"⭐⭐⭐【强制临时状态 (最高优先级)】⭐⭐⭐\n当前事件：\n{combined}\n"

        reality_overwrite_instruction = ""
        if implant_context:
            if relationship_context:
                template = PROMPTS_DB.get("memory_fusion_template") or "【警告】特殊状态：{implant}。关系：{relationship}。"
                reality_overwrite_instruction = template.replace("{implant}", implant_context).replace("{relationship}", relationship_context)
                relationship_context = ""
            else:
                template = PROMPTS_DB.get("memory_force_template") or "【警告】唯一真理：{implant}。"
                reality_overwrite_instruction = template.replace("{implant}", implant_context)

        # --- 7. 导演骰子 ---
        toya_keywords = ["冬弥", "toya", "Toya", "搭档", "青柳"]
        is_toya_context = any(k in plain_text_content for k in toya_keywords) or is_talking_to_toya

        # 仅用于 acting_guide 语气调节，不触发实际搜索
        info_keywords = ["搜", "查", "是什么", "谁是", "天气", "新闻", "多少钱", "查询", "我想知道"]
        is_info_request = any(k in plain_text_content for k in info_keywords)

        long_term_facts = user_mem.get("long_term_facts", [])
        long_term_memory_text = "\n".join(long_term_facts) if long_term_facts else "（暂无特殊记忆）"

        # build_director_note 来自 features/director.py（可一键删除该模块）
        _d = build_director_note(plain_text_content, is_toya_context, long_term_memory_text, PROMPTS_DB, DIRECTOR_DB) if build_director_note else {}
        is_physical_or_drama = _d.get("is_physical_or_drama", False)

        acting_guide = ""
        if is_info_request:
            acting_guide = PROMPTS_DB.get("reliable_mode", "")
        elif is_toya_context:
            # 位置推断模式：用 routine 推断冬弥在哪（短语 + 长度限制，避免普通"冬弥"提及误触发）
            location_keywords = ["冬弥呢", "搭档呢", "冬弥在哪", "冬弥在干嘛", "冬弥去哪了"]
            is_asking_toya_location = any(k in plain_text_content for k in location_keywords) and len(plain_text_content) < 20

            if is_asking_toya_location:
                default_loc_guide = (
                    "🎯【冬弥位置推断模式】：用户在问冬弥现在在哪里/在干什么。"
                    "**你必须完全基于你自己当前的【生物钟状态】来推断冬弥最可能的位置和状态。**"
                    "你的答案必须在逻辑上与你当前正在做的事保持一致，不能自相矛盾。"
                    "（例如：你刚洗完澡在家，就不能说冬弥在外面走了；你在练歌房，冬弥应该也在附近或者路上。）"
                )
                acting_guide = PROMPTS_DB.get("toya_location_guide", default_loc_guide)
            else:
                directions = DIRECTOR_DB.get("toya_directions", [])
                selected = random.choice(directions) if directions else "侧重动作控制"

                if is_physical_or_drama:
                    template = PROMPTS_DB.get("toya_high_tension_guide", "风格：{selected}。")
                    acting_guide = template.replace("{selected}", selected)
                else:
                    if random.random() < 0.85:
                        template = PROMPTS_DB.get("toya_acting_guide", "风格：{selected}。")
                        acting_guide = template.replace("{selected}", selected)
        elif _d.get("acting_guide"):
            acting_guide = _d["acting_guide"]

        # --- 8. 最终 Prompt 组装 ---
        system_header       = PROMPTS_DB.get("system_header", "【系统级绝对指令】你是东云彰人，只输出合法JSON。")
        vitality_guide      = PROMPTS_DB.get("vitality_guide", "")
        memory_capture_rule = PROMPTS_DB.get("memory_capture_rule", "")
        tone_limiter        = PROMPTS_DB.get("tone_limiter", "")
        schema_inner_os     = PROMPTS_DB.get("schema_inner_os", "你的真实心理活动。")
        schema_action       = PROMPTS_DB.get("schema_action", "角色的肢体动作或微表情。没有时留空。")
        schema_dialogue     = PROMPTS_DB.get("schema_dialogue", "角色实际说出的话，纯对话文本。")

        final_system_prompt = f"""
        {system_header}

        # 1. 物理现实与环境
        - 当前系统时间：{current_time}
        - 你的生物钟状态：{daily_status}
        {time_gap_awareness}
        - 今日特殊日历：{festival_buff}
        {morning_run_buff}
        {sleep_buffer_buff}

        # 2. 动态情报栈
        {relationship_context}
        {search_result}

        # 3. 社交上下文
        📜【群聊背景流】
        {group_context}
        🎯【当前交互对象】：
        {interact_instruction}

        # 4. 核心人设与记忆
        {get_base_persona()}
        {script_examples}
        🎮【PJSK 世界观/黑话库】：
        {PJSK_KNOWLEDGE_BASE}
        {get_song_memories()}
        🧠【你的长期记忆】：
        {long_term_memory_text}
        ⚡【强制临时状态/指令】：
        {reality_overwrite_instruction}
        {acting_guide}
        {sleep_instruction}

        {vitality_guide}

        {memory_capture_rule}

        {tone_limiter}

        # ================= 强制输出格式 (JSON) =================
        {{
          "inner_os": "{schema_inner_os}",
          "action": "{schema_action}",
          "dialogue": "{schema_dialogue}"
        }}
        """

        messages_list = [{"role": "system", "content": final_system_prompt}]
        messages_list.extend(user_mem["history"])

        tagged_user_msg_for_llm = f"[{sender_nickname}({user_id})]: {plain_text_content}"
        tagged_user_msg_for_history = f"[{sender_nickname}({user_id})]: {plain_text_content}"

        if current_image_identity:
            if "[彰人]" in current_image_identity:
                role_force = "🎬【导演指导】：照片里是你自己。请表现出嫌弃、无语或稍显别扭的态度，简短吐槽即可，无需大惊小怪。"
            elif "[冬弥]" in current_image_identity:
                role_force = "🎬【导演指导】：照片里是冬弥。请保持你一贯护短但克制的态度，语气可以稍微放缓，但没必要显得太激动。"
            elif "[合照]" in current_image_identity:
                role_force = "🎬【导演指导】：这是你和冬弥的合照。请表现出平淡、认可或无语吐槽的态度。"
            else:
                role_force = "🎬【导演指导】：请看一眼这张图并给出简短的评价。必须保持男高中生的疏离和一点嫌弃感，【严禁】一惊一乍或长篇大论！"

            tagged_user_msg_for_llm += f"\n\n📱 [系统旁白：你瞥了一眼对方发来的图片，画面内容是：{current_image_identity}]\n{role_force}"
            tagged_user_msg_for_history += f"\n[看了一眼图片: {current_image_identity}]"

        # format_breaker 由 features/director.py 生成（模块不存在时为空字符串）
        format_breaker = _d.get("format_breaker", "")

        if format_breaker:
            tagged_user_msg_for_llm += format_breaker

        messages_list.append({"role": "user", "content": tagged_user_msg_for_llm})

        # --- 9. ReAct 智能体调用循环 ---
        raw_result = ""
        if not has_image:
            agent_message = await call_deepseek_api_agent(messages_list, tools=AGENT_TOOLS)

            if agent_message is not None and agent_message.tool_calls:
                tool_call = agent_message.tool_calls[0]
                try:
                    query = json.loads(tool_call.function.arguments).get("query", "")
                except Exception:
                    query = ""
                logger.info(f"🤖 Agent 主动触发搜索: [{query}]")

                if query:
                    search_result = await smart_search(query)
                    if not search_result:
                        search_result = (
                            f'【系统提示】：由于网络不佳，你没有在手机上搜到关于"{query}"的情报。'
                            f'请尽量在你的【长期记忆】或【常识】里回忆一下这是什么。如果实在不知道，就烦躁地抱怨网络太差了。'
                        )

                messages_list.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        }
                    }]
                })
                messages_list.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": search_result or "搜索无结果。",
                })
                raw_result = await call_deepseek_api(messages_list, force_json=True)

            elif agent_message is not None:
                raw_result = agent_message.content or ""
            else:
                raw_result = await call_deepseek_api(messages_list, force_json=True)
        else:
            raw_result = await call_deepseek_api(messages_list, force_json=True)

        result = ""
        clean_json_str = raw_result
        json_match = re.search(r'\{[\s\S]*\}', raw_result)
        if json_match:
            clean_json_str = json_match.group(0)
        inner_os = ""
        try:
            response_data = json.loads(clean_json_str)
            inner_os = response_data.get("inner_os", "") or response_data.get("Inner_os", "") or response_data.get("内心OS", "")
            if inner_os:
                logger.info(f"🎭【小彰内心OS】: {inner_os}")

            # 分别提取动作和台词
            dialogue = response_data.get("dialogue", "") or response_data.get("reply", "") or response_data.get("Reply", "") or response_data.get("回复", "")
            action = response_data.get("action", "")

            # action 字段为空时，尝试从 dialogue 开头回收内联动作交给排版层
            # 只处理"开头单个短动作"的情形（≤15字），多动作混排不干预
            if not action and dialogue:
                m = re.match(r'^[（(]([^）)\n]{1,15})[）)]\s*([\s\S]+)', dialogue)
                if m:
                    action = m.group(1).strip()
                    dialogue = m.group(2).strip()
                    logger.debug(f"🎭 从dialogue回收内联动作: [{action}] | 台词: {dialogue[:40]}")

            # Python端智能接管排版
            if not action:
                result = dialogue
            else:
                # 归一化：剥掉 action 自带的外层括号，统一由下方排版层补回
                # 例：(叹气) → 叹气，叹气 → 叹气，(叹气)(皱眉) → 不变（不是单层包裹）
                action_norm = re.sub(r'^\((.+)\)$', r'\1', action.strip())
                # 嗅探动作类型
                action_text = action_norm.lower()
                # 交互类、指向类动作，强制前置，保证时序连贯
                if any(k in action_text for k in ["递", "指", "看", "拿", "接", "扔", "抱", "拉"]):
                    layout_choices = [
                        f"({action_norm}){dialogue}",
                        f"({action_norm})\n{dialogue}"
                    ]
                else:
                    # 情绪类、状态类动作，完全随机（前置、后置、舍弃），打碎复读感
                    layout_choices = [
                        f"({action_norm}){dialogue}",
                        f"{dialogue}({action_norm})",
                        f"……{dialogue}",
                        f"{dialogue}"
                    ]

                # 日常状态下大幅提高纯文本概率
                if not is_toya_context and len(layout_choices) > 2:
                    weights = [0.15, 0.15, 0.2, 0.5]
                    result = random.choices(layout_choices, weights=weights)[0]
                else:
                    result = random.choice(layout_choices)

            if not result:
                result = "……"
        except Exception as e:
            logger.warning(f"⚠️ 解析JSON失败 ({e}) | 原始返回: {raw_result}")
            # 正则救援：从截断/残缺 JSON 里直接抠出 dialogue/reply 的值
            rescue = re.search(r'"(?:dialogue|reply)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw_result)
            if rescue:
                result = rescue.group(1)
                logger.info(f"🔧 正则救援成功，提取到回复内容: {result[:60]}")
            else:
                # 二次救援：key 名幻觉（模型把动作描写写成了 key 名）
                # 策略：定位 inner_os 值结束位置，把后面剩余内容当作回复
                inner_os_end = re.search(r'"inner_os"\s*:\s*"(?:[^"\\]|\\.)*"', raw_result)
                if inner_os_end:
                    remainder = raw_result[inner_os_end.end():].strip()
                    # 剔除：逗号 + 非标准 key 名（带引号）+ 分隔符（> 或 :）
                    remainder = re.sub(r'^,\s*"[^"]*"\s*[>:]\s*', '', remainder)
                    # 剔除：首尾多余引号和 JSON 闭合符号
                    remainder = remainder.strip('"} \n')
                    if remainder:
                        result = remainder
                        logger.info(f"🔧 二次救援成功（key名幻觉），提取内容: {result[:60]}")
                    else:
                        result = raw_result
                else:
                    result = raw_result

        # --- 10. 长期记忆提取与保存 ---
        memory_pattern = r"\[\[记下[:：]\s*(.*?)\]\]"
        matches = re.findall(memory_pattern, result)
        if matches:
            if "long_term_facts" not in user_mem: user_mem["long_term_facts"] = []
            new_facts = 0
            for fact in matches:
                fact = fact.strip()
                if not any(fact in old for old in user_mem["long_term_facts"]):
                    timestamp = datetime.datetime.now(TZ_CN).strftime('%m-%d')
                    user_mem["long_term_facts"].append(f"[{timestamp}] {fact}")
                    new_facts += 1
            if new_facts > 0:
                save_memory()
                logger.info(f"🧠 小彰记住了新设定: {matches}")
            result = re.sub(memory_pattern, "", result).strip()

        # --- 11. OOC 暴力拦截 ---
        result = result.replace("绘名姐", "绘名").replace("老姐", "绘名").replace("杏姐", "杏").replace("心羽酱", "心羽")
        result = result.replace("啊喂", "啊").replace("吗喂", "吗")
        result = re.sub(r'[\(（](战术掩饰|语感参考|动作参考)[^)）]*?[:：]?\s*', '(', result)

        def _extract_reply(j_str: str) -> str:
            try: return json.loads(j_str).get("reply", j_str)
            except Exception: return j_str

        recent_bot_replies = [
            _extract_reply(m["content"])
            for m in user_mem["history"][-8:]
            if m["role"] == "assistant"
        ]
        if result.strip() in [r.strip() for r in recent_bot_replies]:
            logger.warning("⚠️ 检测到复读！强制注入去重指令重新生成...")
            messages_list[-1]["content"] += (
                "\n🚫【紧急系统警告】：你刚才说过完全一样的话！"
                "这次必须从完全不同的角度切入，换一种表达方式，绝对不能重复！"
            )
            raw_result = await call_deepseek_api(messages_list, force_json=True)
            try:
                json_match = re.search(r'\{[\s\S]*\}', raw_result)
                if json_match:
                    response_data = json.loads(json_match.group(0))
                    result = response_data.get("dialogue", "") or response_data.get("reply", "") or raw_result
            except Exception as e:
                logger.warning(f"⚠️ JSON解析失败，使用原始回复: {e}")
                result = raw_result

        # --- 12. 更新上下文流 ---
        user_mem["history"].append({"role": "user", "content": tagged_user_msg_for_history})
        actual_os = inner_os if inner_os else "（情绪波动）"
        fake_json_history = json.dumps({"inner_os": actual_os, "reply": result}, ensure_ascii=False)
        user_mem["history"].append({"role": "assistant", "content": fake_json_history})

        if len(user_mem["history"]) > MAX_HISTORY_LEN:
            user_mem["history"] = user_mem["history"][-MAX_HISTORY_LEN:]

        save_memory()

        # --- 13. 模拟真人打字延迟 ---
        AKITO_STATUS["last_trigger_user"] = user_id
        if user_id == SUPERUSER_QQ:
            AKITO_STATUS.setdefault("last_superuser_trigger_time", {})[str(group_id)] = time.time()
        if group_id:
            record_bot_response(group_id)
        base_delay = random.uniform(0.8, 2.5)
        typing_delay = min(len(result) * 0.12, 5.0)
        await asyncio.sleep(base_delay + typing_delay)
        await smart_finish(chat, result)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"❌ 主聊天处理器发生未捕获异常: {e}", exc_info=True)
        try:
            await chat.finish("……脑子短路了，等一下再说。")
        except Exception:
            pass
