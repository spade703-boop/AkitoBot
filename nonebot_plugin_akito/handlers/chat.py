"""主对话引擎：触发判定、消息组装与发送、会话锁、图片识别与搜索 Agent 调度。"""

import asyncio
import json
import random
import re

from nonebot import on_message
from nonebot.adapters import Bot, Event, Message
from nonebot.adapters.onebot.v11 import Message as OneBotMessage
from nonebot.adapters.onebot.v11 import MessageSegment
from nonebot.exception import FinishedException
from nonebot.log import logger
from nonebot.matcher import Matcher
from nonebot.params import EventMessage
from nonebot_plugin_htmlrender import md_to_pic

from ..core import (
    ALLOWED_CHAT_GROUPS,
    TRIGGER_NAMES,
    current_request_id,
    get_memory_key,
    grant_safety_pass,
    parse_json_object,
    record_parse_result,
    render_main_chat_prompt,
    rescue_field,
    rescue_tail_after_field,
)
from .chat_pipeline import run_chat_turn

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
    event_memory: str = "",
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
        event_memory=event_memory,
    )


def _parse_model_reply(raw_result: str, is_toya_context: bool) -> tuple[str, str]:
    """Parse model JSON-ish output into final dialogue text and inner thoughts."""
    result = ""
    inner_os = ""
    try:
        response_data = parse_json_object(raw_result)
        if response_data is None:
            record_parse_result(current_request_id(), success=False)
            raise json.JSONDecodeError("invalid json object", raw_result, 0)
        record_parse_result(current_request_id(), success=True)
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
            pipeline_result = await run_chat_turn(event, bot, message)
            if pipeline_result.finish_silently:
                await chat.finish()
            if pipeline_result.delay_seconds:
                await asyncio.sleep(pipeline_result.delay_seconds)
            if pipeline_result.text is not None:
                await smart_finish(chat, pipeline_result.text, trigger_message_id)
    except FinishedException:
        raise
    except Exception as e:
        logger.error(f"❌ 主聊天处理器发生未捕获异常: {e}", exc_info=True)
        try:
            await smart_finish(chat, "……脑子短路了，等一下再说。", trigger_message_id)
        except Exception:
            pass
