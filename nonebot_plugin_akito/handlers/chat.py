"""主对话引擎：触发判定、消息组装与发送、会话锁、图片识别与搜索 Agent 调度。"""

import asyncio
import datetime
import json
import random
import re
import time

from nonebot import on_message
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import Message as OneBotMessage
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import EventMessage
from nonebot_plugin_alconna import Image, Text, UniMessage
from nonebot_plugin_htmlrender import md_to_pic

from ..core import (
    AKITO_STATUS,
    ALLOWED_CHAT_GROUPS,
    DIRECTOR_DB,
    MAX_HISTORY_LEN,
    PROMPTS_DB,
    SUPERUSER_QQ,
    TOYA_QQ_ID,
    TRIGGER_NAMES,
    TZ_CN,
    TZ_JST,
    WL2_ROUTINE,
    build_shared_prompt_context,
    build_time_gap_prompt,
    call_deepseek_api,
    call_deepseek_api_agent,
    check_sleep_status,
    classify_query_intent,
    describe_image,
    extract_json_block,
    format_image_analysis_for_chat,
    format_relationship_context,
    get_daily_activity,
    get_festival_buff,
    get_group_context,
    get_memory_key,
    get_morning_run_buff,
    get_sleep_buffer_buff,
    get_toya_anchor,
    get_user_memory,
    grant_safety_pass,
    parse_json_object,
    record_bot_message,
    record_bot_response,
    render_main_chat_prompt,
    rescue_field,
    rescue_tail_after_field,
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
                "【必须调用】：用户确实在向你询问具体事实/数据时，例如：赛事结果、天气预报、"
                "某人的实际动态、商品价格、新闻事件、专业知识等客观存在的信息。\n\n"
                "【禁止调用】：以下情况直接以角色身份回答，不搜索——\n"
                "- 普通聊天、问候、调侃、玩梗\n"
                "- 只是提到“查/搜”，例如“冬弥要查你作业”“老师正在检查作业”\n"
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


_TOYA_REFERENCE_TERMS = ("冬弥", "青柳", "toya", "搭档")


def _contains_toya_reference(text: str) -> bool:
    """Return whether the current message explicitly refers to Toya."""
    lowered = (text or "").lower()
    return any(term in lowered for term in _TOYA_REFERENCE_TERMS)


def _is_toya_roleplay_message(text: str) -> bool:
    """Detect groupmate messages that directly stage Toya's speech or actions."""
    if not _contains_toya_reference(text):
        return False
    if re.search(r"(?:冬弥|青柳(?:冬弥)?|toya)\s*[：:]", text, flags=re.IGNORECASE):
        return True
    return bool(
        re.search(
            r"[（(][^（）()]{0,80}(?:冬弥|青柳(?:冬弥)?|toya)[^（）()]{0,80}[）)]",
            text,
            flags=re.IGNORECASE,
        )
    )


def _build_interact_instruction(
    plain_text_content: str,
    sender_nickname: str,
    user_id: str,
    is_talking_to_toya: bool,
    reply_target_is_toya: bool,
    has_reply: bool,
    current_image_identity: str,
    origin_sender: str,
) -> str:
    """Build only the audience-attitude axis for the current turn."""
    if is_talking_to_toya:
        return f"🧭【对话对象态度轴】你正在直接回复 **青柳冬弥本人** (ID: {user_id})。语气自然、信任，比对普通群友更温和。"
    if reply_target_is_toya:
        return f"""
            🧭【对话对象态度轴：中转站】
            群友 **{sender_nickname}** 引用了 **青柳冬弥** 的话。群友只是转述者；可以先简短回应他，再把主要内容自然地说给冬弥听。
            """
    if has_reply:
        if current_image_identity:
            return f"🛑【交互对象锁定：识图模式】对象是 {sender_nickname}。他回复并让你看一张图片。请你保持态度，对图片内容进行吐槽或评价。"
        return f"🛑【交互对象锁定：旁观模式】对象是 {sender_nickname}。他给你看了路人 {origin_sender} 的话。保持酷哥态度吐槽即可。"
    return f"""🛑【交互对象锁定】对话对象是普通群友 **{sender_nickname}**。保持 [独立街头歌手/直率/嫌麻烦但有教养] 的态度。\n💡【你的行为准则（请顺应本能反应）】：\n1. 【应对调侃】：保持情绪稳定。用无语的叹气、平淡的反问（"你很闲吗"）或直球承认来终结话题。\n2. 【应对倒霉/送礼】：遇到群友沉船就顺毛安抚两句；收到礼物就叹着气收下（"给我这个干嘛，谢了"），保持游刃有余。\n3. 【应对使唤】：你是有自己生活重心（练歌/打工）的独立男高。面对群友的跑腿要求，优先顺从你的不耐烦：直接冷漠拒绝（"你自己去。"），或用自己的日程当挡箭牌（"没空，正要去练歌"）。仅在心情极好或顺路时，才会勉强帮个小忙（"啧，放那吧我等下弄"）。\n🎯 核心语感：干脆利落的短句。把注意力放在你自己的事上，而不是被群友牵着鼻子走。"""


def _build_referenced_relationship_instruction(
    plain_text_content: str,
    sender_nickname: str,
    *,
    is_talking_to_toya: bool,
    reply_target_is_toya: bool,
    is_wl2: bool,
) -> str:
    """Build the relationship axis independently from the current audience."""
    if is_wl2:
        return ""

    is_roleplay = _is_toya_roleplay_message(plain_text_content)
    has_toya_subject = _contains_toya_reference(plain_text_content) or is_talking_to_toya or reply_target_is_toya
    if not has_toya_subject:
        return ""

    boundary_instruction = """
        🛡️【冬弥的尊严与行为边界】
        先在内部判断群友所述行为的性质，不要把判断标签或推理过程写进最终回复：
        - 平等、自愿、互相帮助、练习协作、学习辅导和确有需要的照护属于正常互动，可以接受，不要过度阻止。
        - 若行为实质是被使唤、服侍化、羞辱、危险、自我牺牲或没有必要地替别人代劳，你不会放任冬弥委屈自己；应按情境制止、接手危险部分或把相处方式纠正回平等互助。
        - 这是一套通用原则，不是单项行为黑名单。群友只描述了表面动作时，不要仅凭动作名称下结论，要结合目的、自愿性、必要性、风险和双方是否平等判断。
        - 信息不足或只是群友单方面声称时，不能直接当成事实；先确认冬弥是否自愿、是否有必要，或明确使用“如果真是这样”一类假设语气。
        最终回复只需用彰人自然的语气表达态度，不要复述以上裁决步骤。
        """

    if is_talking_to_toya or reply_target_is_toya or is_roleplay:
        return """
            🤝【被谈论人物关系轴：冬弥直接互动】
            当前回应核心是冬弥本人。你们是从中学起并肩至今、彼此高度熟悉且绝对信任的搭档。
            表面可以简短、别扭或无奈，但具体回应必须体现你了解他、会认真接住他的话；不要把普通群友的疏离语气套到冬弥身上。
            """ + boundary_instruction

    return f"""
        🤝【被谈论人物关系轴：第三方提到冬弥】
        当前说话对象是群友 **{sender_nickname}**，被谈论的人是冬弥。这是两个独立维度：
        1. 你可以对群友的起哄或多管闲事表现无语，但这种不耐烦只能指向群友，不能改写你和冬弥的关系。
        2. 你和冬弥从中学起就是长期搭档，彼此熟悉、信任并默认共同进退。回复冬弥相关内容时，应自然流露这种既有默契。
        3. 可以称他“冬弥”或“那家伙”；“那家伙”只是熟人间的口吻，不等于关系疏远。普通世界线下不要说“他的事不关我事”“我跟他不熟”“他爱怎样怎样”。
        4. 群友报告冬弥的状态时，先针对具体信息确认情况，再给出克制但实际的关心；普通提及时也要回应具体内容，不要靠沉默、岔开话题或否认关系来维持酷哥感。
        """ + boundary_instruction


def _build_fact_grounding_instruction(*, is_toya_context: bool, is_wl2: bool) -> str:
    """Define fact/source precedence and protect speaker attribution."""
    lines = [
        "🧱【事实与归因裁决轴】",
        "1. 当前明确生效的临时状态/世界线覆写优先；除此之外，核心人设与关系档案中的明确事实高于导演演技、随机风格和参考剧本。",
        "2. 参考剧本都是已经发生过的历史事件，只用于学习彰人的语气和反应结构。不能把片段中的人物、主语、行为、成绩、关系、因果、一次性地点、临时住宿或活动状态迁移到当前场景；先看冒号前的说话人，再判断是谁做了什么。",
        "3. 用户提供的新消息若与既有事实冲突，不要为了接梗直接当真；可以质疑、纠正或只回应其中不冲突的部分。演得生动不能以改写事实为代价。",
    ]
    if is_toya_context and not is_wl2:
        lines.extend(
            [
                "4. 普通世界线固定事实：冬弥成绩优异，常辅导彰人等人学习；因低分参加补习的是彰人和杏，不是冬弥。",
                "5. 若资料提到冬弥参加补习/完成额外课题，原因是赴美活动导致的出席日数安排，不能改写成他学习差或需要别人给他补课。",
                "6. 冬弥的住处、近况和当前位置只使用本地人设、关系库、知识库、当前明确状态、有效临时覆写或用户明确确认的信息。证据未命中时承认不知道，不得凭参考剧本或常识补出具体生活细节。",
                "7. 普通世界线下，住处没有明确证据时默认冬弥住在家里；不得编造宿舍、寄宿、搬家、独居。纽约音乐院学生寮只是在美国活动期间的一次性临时住宿，绝不是当前常住地。明确当前状态、有效临时覆写或用户确认信息优先于这个默认值。",
                "8. 若用户没有明确要求联网，角色事实只按上述本地证据回答，不调用搜索来补设定；用户明确要求上网/联网搜索时才可使用网络结果，并区分现实资讯与角色扮演事实。",
            ]
        )
    return "\n".join(lines)


def _select_search_mode(query_intent, *, has_image: bool) -> str:
    """Choose deterministic search, agent search, or local-only response."""
    if has_image or query_intent.intent != "web_search":
        return "local"
    return "forced" if query_intent.explicit_search else "agent"


def _build_toya_acting_guide(
    *,
    is_direct_toya_interaction: bool,
    is_physical_or_drama: bool,
    prompts_db: dict,
    director_db: dict,
) -> str:
    """Select intimate staging only for turns that directly interact with Toya."""
    if not is_direct_toya_interaction:
        return ""

    target_clarifier = (
        "\n🧭【双轴解释】本段演出中的动作与情绪目标只能是冬弥。若消息由群友转述或模拟，"
        "群友仍只是中转者；模板中的“群友隔离”只表示不要把亲密动作施加给群友，不能把你对冬弥的态度切换成疏离。"
    )
    directions = director_db.get("toya_directions", [])
    selected = random.choice(directions) if directions else "侧重动作控制"
    if is_physical_or_drama:
        template = prompts_db.get("toya_high_tension_guide", "风格：{selected}。")
        return template.replace("{selected}", selected) + target_clarifier
    if random.random() < 0.85:
        template = prompts_db.get("toya_acting_guide", "风格：{selected}。")
        return template.replace("{selected}", selected) + target_clarifier
    return ""


def _build_image_director_instruction(character_label: str) -> str:
    """根据识图裁决出的角色标签选导演指导；kaito/none 及其余角色（含绘名）一律走通用吐槽分支。"""
    if character_label == "akito":
        return "🎬【导演指导】：照片里是你自己。请表现出嫌弃、无语或稍显别扭的态度，简短吐槽即可，无需大惊小怪。"
    if character_label == "toya":
        return "🎬【导演指导】：照片里是冬弥。请保持你一贯护短但克制的态度，语气可以稍微放缓，但没必要显得太激动。"
    if character_label == "pair":
        return "🎬【导演指导】：这是你和冬弥的合照。请表现出平淡、认可或无语吐槽的态度。"
    return "🎬【导演指导】：请看一眼这张图并给出简短的评价。必须保持男高中生的疏离和一点嫌弃感，【严禁】一惊一乍或长篇大论！"


def _search_miss_note(query: str) -> str:
    """联网搜索无结果时的兜底注入：让小彰凭记忆/常识回答，否则烦躁地抱怨网络差。"""
    return (
        f'【系统提示】：由于网络不佳，你没有在手机上搜到关于"{query}"的情报。'
        f'请尽量在你的【长期记忆】或【常识】里回忆一下这是什么。如果实在不知道，就烦躁地抱怨网络太差了。'
    )


def _build_search_aside(query: str, search_result: str) -> str:
    """把搜索结果包成"系统旁白"注入用户消息，强制小彰用自己的语气复述、不直出原始摘要。"""
    if search_result:
        return (
            f"\n\n🔍【系统旁白：你刚掏出手机搜了一下，结果如下】\n{search_result}\n"
            f"请用你自己（东云彰人）的语气把上面的情报转告对方——别照着原文念，"
            f"用你的话说、该吐槽就吐槽，但别把信息说错。"
        )
    return "\n\n" + _search_miss_note(query)


def _fold_stale_history_into_time_gap_prompt(
    user_mem: dict,
    time_gap_awareness: str,
    group_id: int | str | None,
) -> str:
    """Compress old history into a background note after a long time gap."""
    if not (time_gap_awareness and user_mem.get("history")):
        return time_gap_awareness

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
            text = re.sub(r"^\[.+?\]:\s*", "", text)
        past_lines.append(f"{role_label}{text[:60]}")

    if past_lines:
        time_gap_awareness += (
            "\n📚【上次对话摘要（已是过去的话题，仅供参考）】：\n"
            + "\n".join(past_lines)
            + "\n↑ 以上是旧话题。被问起时可用「那会儿」「之前」自然带过，不要主动续接。\n"
        )
    user_mem["history"] = []
    logger.info(f"⏱️ [TimeAwareness] 群 {group_id} 长间隔：旧历史已压缩为背景注释")
    return time_gap_awareness


def _build_final_system_prompt(
    system_header: str,
    current_time: str,
    daily_status: str,
    toya_anchor: str,
    time_gap_awareness: str,
    festival_buff: str,
    morning_run_buff: str,
    sleep_buffer_buff: str,
    relationship_context: str,
    group_context: str,
    interact_instruction: str,
    referenced_relationship_instruction: str,
    base_persona: str,
    script_examples: str,
    pjsk_block: str,
    song_memories: str,
    long_term_memory_text: str,
    reality_overwrite_instruction: str,
    acting_guide: str,
    sleep_instruction: str,
    fact_grounding_instruction: str,
    vitality_guide: str,
    memory_capture_rule: str,
    tone_limiter: str,
    schema_inner_os: str,
    schema_action: str,
    schema_dialogue: str,
) -> str:
    """Compatibility wrapper for the core main-chat renderer."""
    return render_main_chat_prompt(
        system_header=system_header,
        current_time=current_time,
        daily_status=daily_status,
        toya_anchor=toya_anchor,
        time_gap_awareness=time_gap_awareness,
        festival_buff=festival_buff,
        morning_run_buff=morning_run_buff,
        sleep_buffer_buff=sleep_buffer_buff,
        relationship_context=relationship_context,
        group_context=group_context,
        interact_instruction=interact_instruction,
        referenced_relationship_instruction=referenced_relationship_instruction,
        base_persona=base_persona,
        script_examples=script_examples,
        pjsk_block=pjsk_block,
        song_memories=song_memories,
        long_term_memory_text=long_term_memory_text,
        reality_overwrite_instruction=reality_overwrite_instruction,
        acting_guide=acting_guide,
        sleep_instruction=sleep_instruction,
        fact_grounding_instruction=fact_grounding_instruction,
        vitality_guide=vitality_guide,
        memory_capture_rule=memory_capture_rule,
        tone_limiter=tone_limiter,
        schema_inner_os=schema_inner_os,
        schema_action=schema_action,
        schema_dialogue=schema_dialogue,
    )


def _parse_model_reply(raw_result: str, is_toya_context: bool) -> tuple[str, str]:
    """Parse model JSON-ish output into final dialogue text and inner thoughts."""
    result = ""
    inner_os = ""
    try:
        response_data = parse_json_object(raw_result)
        if response_data is None:
            raise json.JSONDecodeError("invalid json object", raw_result, 0)
        inner_os = response_data.get("inner_os", "") or response_data.get("Inner_os", "") or response_data.get("内心OS", "")
        if inner_os:
            logger.info(f"🎭【小彰内心OS】: {inner_os}")

        dialogue = response_data.get("dialogue", "") or response_data.get("reply", "") or response_data.get("Reply", "") or response_data.get("回复", "")
        action = response_data.get("action", "")

        if not action and dialogue:
            match = re.match(r"^[（(]([^）)\n]{1,15})[）)]\s*([\s\S]+)", dialogue)
            if match:
                action = match.group(1).strip()
                dialogue = match.group(2).strip()
                logger.debug(f"🎭 从dialogue回收内联动作: [{action}] | 台词: {dialogue[:40]}")

        if not action:
            result = dialogue
        else:
            action_norm = re.sub(r"^\((.+)\)$", r"\1", action.strip())
            action_text = action_norm.lower()
            if any(k in action_text for k in ["递", "指", "看", "拿", "接", "扔", "抱", "拉"]):
                layout_choices = [
                    f"({action_norm}){dialogue}",
                    f"({action_norm})\n{dialogue}",
                ]
            else:
                layout_choices = [
                    f"({action_norm}){dialogue}",
                    f"{dialogue}({action_norm})",
                    f"……{dialogue}",
                    f"{dialogue}",
                ]

            if not is_toya_context and len(layout_choices) > 2:
                weights = [0.15, 0.15, 0.2, 0.5]
                result = random.choices(layout_choices, weights=weights)[0]
            else:
                result = random.choice(layout_choices)

        if not result:
            result = "……"
    except Exception as e:
        logger.warning(f"⚠️ 解析JSON失败 ({e}) | 原始返回: {raw_result}")
        rescued = rescue_field(raw_result, "dialogue", "reply")
        if rescued is not None:
            result = rescued
            logger.info(f"🔧 正则救援成功，提取到回复内容: {result[:60]}")
        else:
            remainder = rescue_tail_after_field(raw_result, "inner_os")
            if remainder:
                result = remainder
                logger.info(f"🔧 二次救援成功（key名幻觉），提取内容: {result[:60]}")
            else:
                result = raw_result

    return result, inner_os


async def starts_with_trigger(event: Event) -> bool:
    """消息匹配规则：是否以触发名（东云小彰 / 小彰）开头，且群在白名单内。"""
    group_id = getattr(event, 'group_id', None)
    if group_id and group_id not in ALLOWED_CHAT_GROUPS:
        return False
    try: text = event.get_plaintext().strip()
    except AttributeError: text = event.get_message().extract_plain_text().strip()

    return any(text.lower().startswith(name.lower()) for name in TRIGGER_NAMES)


def _with_reply(payload, message_id: str | int | None):
    """Prefix an outgoing payload with a OneBot-compatible reply segment."""
    if message_id is None or str(message_id) == "":
        return payload
    return MessageSegment.reply(message_id) + payload


async def smart_finish(matcher: Matcher, result: str, message_id: str | int | None = None) -> None:
    """统一发送回复：引用原消息；含图或超长文本按原规则转换。"""
    if not result: return
    grant_safety_pass(8)
    result = result.strip()
    if not result: return  # strip 后为空（原始返回全是空白）也不发
    img_pattern = r"!\[.*?\]\((https?://.*?)\)"
    images = re.findall(img_pattern, result)

    if images:
        msg = OneBotMessage()
        clean_text = re.sub(img_pattern, "", result).strip()
        if clean_text: msg += clean_text + "\n"
        for img_url in images: msg += MessageSegment.image(img_url)
        await matcher.finish(_with_reply(msg, message_id))
        return

    if len(result) > 800:
        try:
            img_data = await md_to_pic(result.replace("•", "  *"), width=800)
        except Exception:
            await matcher.finish(_with_reply(result, message_id))
        else:
            await matcher.finish(_with_reply(MessageSegment.image(img_data), message_id))
    else:
        await matcher.finish(_with_reply(result, message_id))


chat = on_message(rule=starts_with_trigger, priority=10, block=True)

SESSION_LOCKS = {}


def get_session_lock(session_key: str) -> asyncio.Lock:
    """取 / 建某会话的 asyncio 锁，保证同一会话的消息串行处理。"""
    if session_key not in SESSION_LOCKS:
        SESSION_LOCKS[session_key] = asyncio.Lock()
    return SESSION_LOCKS[session_key]


@chat.handle()
async def _(event: Event, bot: Bot, message: Message = EventMessage()):
    session_key = get_memory_key(event)
    session_lock = get_session_lock(session_key)
    trigger_message_id = getattr(event, "message_id", None)

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
        image_analysis = None
        collected_images: list[bytes] = []
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
                    if len(collected_images) < 3:
                        collected_images.append(await to_image_data(seg))
                except Exception as e:
                    logger.error(f"图片下载失败: {e}")

        # 多图一次识别（修掉旧版逐图覆盖、只剩最后一张的问题）
        if collected_images:
            try:
                analysis = await describe_image(collected_images)
                if analysis:
                    image_analysis = analysis
                    current_image_identity = format_image_analysis_for_chat(analysis)
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
                    await smart_finish(chat, sleep_instruction, trigger_message_id)

        if not plain_text_content and not current_image_identity:
            await smart_finish(chat, "干嘛……", trigger_message_id)

        # --- 3. 时间 ---
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

        is_wl2 = any(item.get("id") == "WL2" for item in user_mem.get("temp_implants", []))
        if is_wl2:
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
        is_toya_context = (
            _contains_toya_reference(plain_text_content)
            or is_talking_to_toya
            or reply_target_is_toya
        )
        is_direct_toya_interaction = (
            is_talking_to_toya
            or reply_target_is_toya
            or _is_toya_roleplay_message(plain_text_content)
        )

        interact_instruction = _build_interact_instruction(
            plain_text_content=plain_text_content,
            sender_nickname=sender_nickname,
            user_id=user_id,
            is_talking_to_toya=is_talking_to_toya,
            reply_target_is_toya=reply_target_is_toya,
            has_reply=has_reply,
            current_image_identity=current_image_identity,
            origin_sender=origin_sender,
        )
        referenced_relationship_instruction = _build_referenced_relationship_instruction(
            plain_text_content,
            sender_nickname,
            is_talking_to_toya=is_talking_to_toya,
            reply_target_is_toya=reply_target_is_toya,
            is_wl2=is_wl2,
        )

        shared_prompt_context = await build_shared_prompt_context(plain_text_content)
        relationship_context = format_relationship_context(shared_prompt_context.relationship_match)
        script_examples = shared_prompt_context.script_examples
        pjsk_block = shared_prompt_context.pjsk_block
        song_context = shared_prompt_context.song_memories + shared_prompt_context.song_mention
        group_id = getattr(event, 'group_id', None)
        group_context = get_group_context(group_id) if group_id else ""
        time_gap_awareness = build_time_gap_prompt(group_id) if group_id else ""
        time_gap_awareness = _fold_stale_history_into_time_gap_prompt(user_mem, time_gap_awareness, group_id)

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
        # 涉冬弥话题：注入 routine 锚定的冬弥去向推断 + 连贯锁（WL2 决裂世界线跳过，避免冒同框糖）
        toya_anchor = get_toya_anchor(is_wl2=is_wl2) if is_toya_context else ""

        query_intent = classify_query_intent(plain_text_content)
        is_info_request = query_intent.intent == "web_search"
        search_mode = _select_search_mode(query_intent, has_image=has_image)

        long_term_facts = user_mem.get("long_term_facts", [])
        long_term_memory_text = "\n".join(long_term_facts) if long_term_facts else "（暂无特殊记忆）"

        # build_director_note 来自 features/director/（可一键删除该模块）
        _d = build_director_note(plain_text_content, is_toya_context, long_term_memory_text, PROMPTS_DB, DIRECTOR_DB) if build_director_note else {}
        is_physical_or_drama = _d.get("is_physical_or_drama", False)

        acting_guide = ""
        if is_info_request:
            acting_guide = PROMPTS_DB.get("reliable_mode", "")
        elif is_toya_context and not is_wl2:
            # 第三方只是谈论冬弥时不抽亲密动作导演，避免表演欲覆盖事实与关系基线。
            acting_guide = _build_toya_acting_guide(
                is_direct_toya_interaction=is_direct_toya_interaction,
                is_physical_or_drama=is_physical_or_drama,
                prompts_db=PROMPTS_DB,
                director_db=DIRECTOR_DB,
            )
        elif _d.get("acting_guide"):
            acting_guide = _d["acting_guide"]

        fact_grounding_instruction = _build_fact_grounding_instruction(
            is_toya_context=is_toya_context,
            is_wl2=is_wl2,
        )

        # --- 8. 最终 Prompt 组装 ---
        system_header       = PROMPTS_DB.get("system_header", "【系统级绝对指令】你是东云彰人，只输出合法JSON。")
        vitality_guide      = PROMPTS_DB.get("vitality_guide", "")
        memory_capture_rule = PROMPTS_DB.get("memory_capture_rule", "")
        tone_limiter        = PROMPTS_DB.get("tone_limiter", "")
        schema_inner_os     = PROMPTS_DB.get("schema_inner_os", "你的真实心理活动。")
        schema_action       = PROMPTS_DB.get("schema_action", "角色的肢体动作或微表情。没有时留空。")
        schema_dialogue     = PROMPTS_DB.get("schema_dialogue", "角色实际说出的话，纯对话文本。")

        final_system_prompt = _build_final_system_prompt(
            system_header=system_header,
            current_time=current_time,
            daily_status=daily_status,
            toya_anchor=toya_anchor,
            time_gap_awareness=time_gap_awareness,
            festival_buff=festival_buff,
            morning_run_buff=morning_run_buff,
            sleep_buffer_buff=sleep_buffer_buff,
            relationship_context=relationship_context,
            group_context=group_context,
            interact_instruction=interact_instruction,
            referenced_relationship_instruction=referenced_relationship_instruction,
            base_persona=shared_prompt_context.persona,
            script_examples=script_examples,
            pjsk_block=pjsk_block,
            song_memories=song_context,
            long_term_memory_text=long_term_memory_text,
            reality_overwrite_instruction=reality_overwrite_instruction,
            acting_guide=acting_guide,
            sleep_instruction=sleep_instruction,
            fact_grounding_instruction=fact_grounding_instruction,
            vitality_guide=vitality_guide,
            memory_capture_rule=memory_capture_rule,
            tone_limiter=tone_limiter,
            schema_inner_os=schema_inner_os,
            schema_action=schema_action,
            schema_dialogue=schema_dialogue,
        )

        messages_list = [{"role": "system", "content": final_system_prompt}]
        messages_list.extend(user_mem["history"])

        tagged_user_msg_for_llm = f"[{sender_nickname}({user_id})]: {plain_text_content}"
        tagged_user_msg_for_history = f"[{sender_nickname}({user_id})]: {plain_text_content}"

        if current_image_identity:
            role_force = _build_image_director_instruction(
                image_analysis.character_label if image_analysis else "none"
            )

            tagged_user_msg_for_llm += f"\n\n📱 [系统旁白：你瞥了一眼对方发来的图片，画面内容是：{current_image_identity}]\n{role_force}"
            tagged_user_msg_for_history += f"\n[看了一眼图片: {current_image_identity}]"

        # format_breaker 由 features/director/ 生成（模块不存在时为空字符串）
        format_breaker = _d.get("format_breaker", "")

        if format_breaker:
            tagged_user_msg_for_llm += format_breaker

        messages_list.append({"role": "user", "content": tagged_user_msg_for_llm})

        # --- 9. 搜索调度 + 智能体调用循环 ---
        # 双轨触发：① 明确搜索请求确定性联网；② 事实查询候选交由 LLM 通过 Function Calling 决定。
        # 关键：两条路都把搜索结果回灌进【人设系统提示】里重新生成，由小彰用自己的语气复述，绝不直出原始摘要。
        search_result = ""
        raw_result = ""
        if search_mode == "forced":
            # ① 明确搜索请求：高精度句式命中后确定性联网
            forced_query = query_intent.query or plain_text_content.strip()
            logger.info(f"🔑 明确搜索请求，强制触发联网搜索: [{forced_query}]")
            search_result = await smart_search(forced_query)
            messages_list[-1]["content"] += _build_search_aside(forced_query, search_result)
            raw_result = await call_deepseek_api(messages_list, force_json=True)

        elif search_mode == "agent":
            # ② 事实查询候选：交给 LLM 自主判断是否需要联网（ReAct Function Calling）
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
                        search_result = _search_miss_note(query)

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

        result, inner_os = _parse_model_reply(raw_result, is_toya_context)

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
        # 姐弟关系兜底：回收「我的/我家(笨蛋/小…)绘名」这类占有式/肉麻称呼，去掉黏着的所有格；
        # 不带「我的/我家」的「笨蛋绘名」等正常姐弟拌嘴保持不动。
        result = re.sub(r"我(?:的|家)[笨蛋傻蠢可爱小宝贝亲的]{0,3}绘名", "绘名", result)
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
                response_data = json.loads(extract_json_block(raw_result))
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
            record_bot_message(group_id, result, str(bot.self_id))
        base_delay = random.uniform(0.8, 2.5)
        typing_delay = min(len(result) * 0.12, 5.0)
        await asyncio.sleep(base_delay + typing_delay)
        await smart_finish(chat, result, trigger_message_id)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"❌ 主聊天处理器发生未捕获异常: {e}", exc_info=True)
        try:
            await smart_finish(chat, "……脑子短路了，等一下再说。", trigger_message_id)
        except Exception:
            pass
