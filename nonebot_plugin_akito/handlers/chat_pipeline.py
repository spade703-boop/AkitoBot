"""Application pipeline for one main-chat turn."""

from __future__ import annotations

from dataclasses import dataclass
import datetime
import json
import re
import time
from typing import Any

from nonebot.adapters import Bot, Event, Message
from nonebot.log import logger
from nonebot_plugin_alconna import Image, Text, UniMessage

from ..core import (
    AKITO_STATUS,
    DIRECTOR_DB,
    MAX_HISTORY_LEN,
    PROMPTS_DB,
    SUPERUSER_QQ,
    TOYA_QQ_ID,
    TZ_CN,
    TZ_JST,
    WL2_ROUTINE,
    ImageAnalysis,
    MemorySession,
    QueryIntent,
    build_shared_prompt_context,
    build_time_gap_prompt,
    call_deepseek_api,
    call_deepseek_api_agent,
    check_sleep_status,
    classify_query_intent,
    describe_image,
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
    record_bot_message,
    record_bot_response,
    save_memory,
    smart_search,
    to_image_data,
)


@dataclass(frozen=True)
class IncomingTurn:
    session_key: str
    message_id: str | int | None
    user_id: str
    group_id: int | str | None
    sender_nickname: str
    plain_text_content: str
    has_image: bool
    current_image_identity: str
    image_analysis: ImageAnalysis | None
    has_reply: bool
    reply_target_is_toya: bool
    origin_sender: str


@dataclass(frozen=True)
class GateDecision:
    text: str | None
    delay_seconds: float
    skip_send: bool
    sleep_instruction: str


@dataclass
class PreparedTurn:
    turn: IncomingTurn
    user_mem: MemorySession
    messages_list: list[dict[str, Any]]
    tagged_user_msg_for_llm: str
    tagged_user_msg_for_history: str
    is_toya_context: bool
    search_mode: str
    query_intent: QueryIntent


@dataclass(frozen=True)
class ChatReply:
    text: str
    inner_os: str


@dataclass(frozen=True)
class PipelineResult:
    text: str | None
    delay_seconds: float
    finish_silently: bool = False


def _chat_module():
    from . import chat

    return chat


async def collect_turn_input(event: Event, bot: Bot, message: Message) -> IncomingTurn:
    session_key = get_memory_key(event)
    uni_message = await UniMessage.generate(message=message, event=event, bot=bot)

    has_reply = False
    reply_target_is_toya = False
    origin_sender = ""

    if getattr(event, "reply", None):
        has_reply = True
        try:
            origin_msg_id = event.reply.message_id
            origin_msg = await bot.get_msg(message_id=origin_msg_id)
            origin_text = origin_msg.get("message", [])
            origin_sender = origin_msg.get("sender", {}).get("nickname", "未知用户")
            reply_target_is_toya = str(origin_msg.get("sender", {}).get("user_id")) == TOYA_QQ_ID

            img_url = ""
            if isinstance(origin_text, list):
                for segment in origin_text:
                    if isinstance(segment, dict) and segment.get("type") == "image":
                        img_url = segment.get("data", {}).get("url", "")
                        if img_url:
                            break
            elif isinstance(origin_text, str):
                match = re.search(r"\[CQ:image,.*?url=(https?[^,\]]+)", origin_text)
                if match:
                    img_url = match.group(1)

            if img_url:
                uni_message = UniMessage([Image(url=img_url)]) + uni_message
                logger.info(f"📸 成功在回复溯源中抓取到隐藏图片: {img_url[:30]}...")
        except Exception as exc:
            logger.error(f"提取回复原消息失败: {exc}")

    name_stripped = False
    plain_text_content = ""
    current_image_identity = ""
    image_analysis = None
    collected_images: list[bytes] = []
    has_image = False

    for segment in uni_message:
        if isinstance(segment, Text) and segment.text.strip() != "":
            text = segment.text
            if not name_stripped:
                clean_text = text.lstrip()
                for name in _chat_module().TRIGGER_NAMES:
                    if clean_text.lower().startswith(name.lower()):
                        text = clean_text[len(name):].lstrip()
                        name_stripped = True
                        break
            if text.strip() != "":
                plain_text_content += text
        elif isinstance(segment, Image):
            has_image = True
            try:
                if len(collected_images) < 3:
                    collected_images.append(await to_image_data(segment))
            except Exception as exc:
                logger.error(f"图片下载失败: {exc}")

    if collected_images:
        try:
            analysis = await describe_image(collected_images)
            if analysis:
                image_analysis = analysis
                current_image_identity = format_image_analysis_for_chat(analysis)
                logger.info(f"👁️ 视觉系统成功将画面传递给大脑: {current_image_identity}")
        except Exception as exc:
            logger.error(f"视觉解析后赋值失败: {exc}")

    user_id = str(event.get_user_id())
    sender = event.sender
    sender_nickname = sender.card or sender.nickname or f"用户{user_id}"
    return IncomingTurn(
        session_key=session_key,
        message_id=getattr(event, "message_id", None),
        user_id=user_id,
        group_id=getattr(event, "group_id", None),
        sender_nickname=sender_nickname,
        plain_text_content=plain_text_content,
        has_image=has_image,
        current_image_identity=current_image_identity,
        image_analysis=image_analysis,
        has_reply=has_reply,
        reply_target_is_toya=reply_target_is_toya,
        origin_sender=origin_sender,
    )


def decide_gate(turn: IncomingTurn) -> GateDecision:
    if turn.user_id == SUPERUSER_QQ:
        sleep_instruction = ""
    else:
        should_block, sleep_instruction = check_sleep_status(turn.plain_text_content)
        if should_block:
            if sleep_instruction == "ignore":
                return GateDecision(text=None, delay_seconds=0, skip_send=True, sleep_instruction="")
            return GateDecision(
                text=sleep_instruction,
                delay_seconds=2,
                skip_send=False,
                sleep_instruction=sleep_instruction,
            )

    if not turn.plain_text_content and not turn.current_image_identity:
        return GateDecision(text="干嘛……", delay_seconds=0, skip_send=False, sleep_instruction=sleep_instruction)
    return GateDecision(text=None, delay_seconds=0, skip_send=False, sleep_instruction=sleep_instruction)


async def prepare_turn(turn: IncomingTurn, sleep_instruction: str) -> PreparedTurn:
    chat = _chat_module()
    now_time = datetime.datetime.now(TZ_CN)
    now_jst = datetime.datetime.now(TZ_JST)
    hour_24 = now_time.hour
    jst_h = now_jst.hour
    if jst_h < 6:
        period = "凌晨"
    elif jst_h < 12:
        period = "上午"
    elif jst_h == 12:
        period = "中午"
    elif jst_h < 18:
        period = "下午"
    else:
        period = "晚上"
    hour_12 = jst_h % 12 or 12
    current_time = (
        f"{now_jst.year}年{now_jst.month}月{now_jst.day}日 {period}{hour_12}点{now_jst.minute:02d}分 "
        f"(24小时制: {now_jst.strftime('%H:%M')} JST)"
    )

    daily_status = get_daily_activity(now_time.hour, now_time.weekday(), now_time.minute)
    festival_buff = get_festival_buff(now_jst)
    morning_run_buff = get_morning_run_buff(hour_24)
    sleep_buffer_buff = get_sleep_buffer_buff(hour_24, now_time.minute)
    user_mem = get_user_memory(turn.session_key)

    is_wl2 = any(item.get("id") == "WL2" for item in user_mem.get("temp_implants", []))
    if is_wl2:
        if 0 <= hour_24 < 6:
            time_key = "late_night"
        elif 6 <= hour_24 < 12:
            time_key = "morning"
        elif 12 <= hour_24 < 14:
            time_key = "noon"
        elif 14 <= hour_24 < 18:
            time_key = "afternoon"
        else:
            time_key = "night"
        rnd = chat.random.Random(now_time.day * 24 + hour_24)
        routine_pool = WL2_ROUTINE.get(time_key, ["独自一人，在沉默中发呆。"])
        daily_status = f"【当前状态】{rnd.choice(routine_pool)}"

    user_id = turn.user_id
    is_talking_to_toya = user_id == TOYA_QQ_ID
    is_toya_context = (
        chat._contains_toya_reference(turn.plain_text_content)
        or is_talking_to_toya
        or turn.reply_target_is_toya
    )
    is_direct_toya_interaction = (
        is_talking_to_toya
        or turn.reply_target_is_toya
        or chat._is_toya_roleplay_message(turn.plain_text_content)
    )
    interact_instruction = chat._build_interact_instruction(
        plain_text_content=turn.plain_text_content,
        sender_nickname=turn.sender_nickname,
        user_id=user_id,
        is_talking_to_toya=is_talking_to_toya,
        reply_target_is_toya=turn.reply_target_is_toya,
        has_reply=turn.has_reply,
        current_image_identity=turn.current_image_identity,
        origin_sender=turn.origin_sender,
    )
    referenced_relationship_instruction = chat._build_referenced_relationship_instruction(
        turn.plain_text_content,
        turn.sender_nickname,
        is_talking_to_toya=is_talking_to_toya,
        reply_target_is_toya=turn.reply_target_is_toya,
        is_wl2=is_wl2,
    )

    shared_prompt_context = await build_shared_prompt_context(turn.plain_text_content)
    relationship_context = format_relationship_context(shared_prompt_context.relationship_match)
    group_context = await get_group_context(turn.group_id) if turn.group_id else ""
    time_gap_awareness = build_time_gap_prompt(turn.group_id) if turn.group_id else ""
    time_gap_awareness = chat._fold_stale_history_into_time_gap_prompt(
        user_mem,
        time_gap_awareness,
        turn.group_id,
    )

    implants = user_mem.get("temp_implants", [])
    valid_implants = [item for item in implants if time.time() < item.get("expire_at", item.get("expire_time", 0))]
    user_mem["temp_implants"] = valid_implants
    implant_context = ""
    if valid_implants:
        details = [
            f"- {item['content']} (剩余 {int((item.get('expire_at', item.get('expire_time', 0)) - time.time()) / 60)} 分钟)"
            for item in valid_implants
        ]
        combined = "\n".join(details)
        implant_context = f"⭐⭐⭐【强制临时状态 (最高优先级)】⭐⭐⭐\n当前事件：\n{combined}\n"

    reality_overwrite_instruction = ""
    if implant_context:
        if relationship_context:
            template = PROMPTS_DB.get("memory_fusion_template") or "【警告】特殊状态：{implant}。关系：{relationship}。"
            reality_overwrite_instruction = template.replace("{implant}", implant_context).replace(
                "{relationship}", relationship_context
            )
            relationship_context = ""
        else:
            template = PROMPTS_DB.get("memory_force_template") or "【警告】唯一真理：{implant}。"
            reality_overwrite_instruction = template.replace("{implant}", implant_context)

    toya_anchor = get_toya_anchor(is_wl2=is_wl2) if is_toya_context else ""
    query_intent = classify_query_intent(turn.plain_text_content)
    is_info_request = query_intent.intent == "web_search"
    search_mode = chat._select_search_mode(query_intent, has_image=turn.has_image)
    long_term_facts = user_mem.get("long_term_facts", [])
    long_term_memory_text = "\n".join(long_term_facts) if long_term_facts else "（暂无特殊记忆）"
    director_note = chat.build_director_note(
        turn.plain_text_content,
        is_toya_context,
        long_term_memory_text,
        PROMPTS_DB,
        DIRECTOR_DB,
    ) if chat.build_director_note else {}
    is_physical_or_drama = director_note.get("is_physical_or_drama", False)

    if is_info_request:
        acting_guide = PROMPTS_DB.get("reliable_mode", "")
    elif is_toya_context and not is_wl2:
        acting_guide = chat._build_toya_acting_guide(
            is_direct_toya_interaction=is_direct_toya_interaction,
            is_physical_or_drama=is_physical_or_drama,
            prompts_db=PROMPTS_DB,
            director_db=DIRECTOR_DB,
        )
    else:
        acting_guide = director_note.get("acting_guide", "")

    final_system_prompt = chat._build_final_system_prompt(
        system_header=PROMPTS_DB.get("system_header", "【系统级绝对指令】你是东云彰人，只输出合法JSON。"),
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
        script_examples=shared_prompt_context.script_examples,
        pjsk_block=shared_prompt_context.pjsk_block,
        song_memories=shared_prompt_context.song_memories + shared_prompt_context.song_mention,
        long_term_memory_text=long_term_memory_text,
        reality_overwrite_instruction=reality_overwrite_instruction,
        acting_guide=acting_guide,
        sleep_instruction=sleep_instruction,
        fact_grounding_instruction=chat._build_fact_grounding_instruction(
            is_toya_context=is_toya_context,
            is_wl2=is_wl2,
        ),
        vitality_guide=PROMPTS_DB.get("vitality_guide", ""),
        memory_capture_rule=PROMPTS_DB.get("memory_capture_rule", ""),
        tone_limiter=PROMPTS_DB.get("tone_limiter", ""),
        schema_inner_os=PROMPTS_DB.get("schema_inner_os", "你的真实心理活动。"),
        schema_action=PROMPTS_DB.get("schema_action", "角色的肢体动作或微表情。没有时留空。"),
        schema_dialogue=PROMPTS_DB.get("schema_dialogue", "角色实际说出的话，纯对话文本。"),
    )

    tagged_user_msg_for_llm = f"[{turn.sender_nickname}({user_id})]: {turn.plain_text_content}"
    tagged_user_msg_for_history = tagged_user_msg_for_llm
    messages_list = [{"role": "system", "content": final_system_prompt}]
    messages_list.extend(user_mem["history"])
    if turn.current_image_identity:
        role_force = chat._build_image_director_instruction(
            turn.image_analysis.character_label if turn.image_analysis else "none"
        )
        tagged_user_msg_for_llm += (
            f"\n\n📱 [系统旁白：你瞥了一眼对方发来的图片，画面内容是：{turn.current_image_identity}]\n{role_force}"
        )
        tagged_user_msg_for_history += f"\n[看了一眼图片: {turn.current_image_identity}]"
    format_breaker = director_note.get("format_breaker", "")
    if format_breaker:
        tagged_user_msg_for_llm += format_breaker
    messages_list.append({"role": "user", "content": tagged_user_msg_for_llm})
    return PreparedTurn(
        turn=turn,
        user_mem=user_mem,
        messages_list=messages_list,
        tagged_user_msg_for_llm=tagged_user_msg_for_llm,
        tagged_user_msg_for_history=tagged_user_msg_for_history,
        is_toya_context=is_toya_context,
        search_mode=search_mode,
        query_intent=query_intent,
    )


async def _dispatch_model(prepared: PreparedTurn) -> str:
    chat = _chat_module()
    messages_list = prepared.messages_list
    search_result = ""
    if prepared.search_mode == "forced":
        forced_query = prepared.query_intent.query or prepared.turn.plain_text_content.strip()
        chat.logger.info(f"🔑 明确搜索请求，强制触发联网搜索: [{forced_query}]")
        search_result = await smart_search(forced_query)
        messages_list[-1]["content"] += chat._build_search_aside(forced_query, search_result)
        return await call_deepseek_api(messages_list, force_json=True)

    if prepared.search_mode == "agent":
        agent_message = await call_deepseek_api_agent(messages_list, tools=chat.AGENT_TOOLS)
        if agent_message is not None and agent_message.tool_calls:
            tool_call = agent_message.tool_calls[0]
            try:
                query = json.loads(tool_call.function.arguments).get("query", "")
            except Exception:
                query = ""
            chat.logger.info(f"🤖 Agent 主动触发搜索: [{query}]")
            if query:
                search_result = await smart_search(query)
                if not search_result:
                    search_result = chat._search_miss_note(query)
            messages_list.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "type": tool_call.type,
                            "function": {
                                "name": tool_call.function.name,
                                "arguments": tool_call.function.arguments,
                            },
                        }
                    ],
                }
            )
            messages_list.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": search_result or "搜索无结果。",
                }
            )
            return await call_deepseek_api(messages_list, force_json=True)
        if agent_message is not None:
            return agent_message.content or ""
    return await call_deepseek_api(messages_list, force_json=True)


async def generate_reply(prepared: PreparedTurn) -> ChatReply:
    chat = _chat_module()
    raw_result = await _dispatch_model(prepared)
    result, inner_os = chat._parse_model_reply(raw_result, prepared.is_toya_context)
    return ChatReply(text=result, inner_os=inner_os)


async def post_process_reply(prepared: PreparedTurn, reply: ChatReply) -> ChatReply:
    result = reply.text
    inner_os = reply.inner_os

    memory_pattern = r"\[\[记下[:：]\s*(.*?)\]\]"
    matches = re.findall(memory_pattern, result)
    if matches:
        if "long_term_facts" not in prepared.user_mem:
            prepared.user_mem["long_term_facts"] = []
        new_facts = 0
        for fact in matches:
            fact = fact.strip()
            if not any(fact in old for old in prepared.user_mem["long_term_facts"]):
                timestamp = datetime.datetime.now(TZ_CN).strftime("%m-%d")
                prepared.user_mem["long_term_facts"].append(f"[{timestamp}] {fact}")
                new_facts += 1
        if new_facts > 0:
            save_memory()
            logger.info(f"🧠 小彰记住了新设定: {matches}")
        result = re.sub(memory_pattern, "", result).strip()

    result = result.replace("绘名姐", "绘名").replace("老姐", "绘名").replace("杏姐", "杏").replace("心羽酱", "心羽")
    result = re.sub(r"我(?:的|家)[笨蛋傻蠢可爱小宝贝亲的]{0,3}绘名", "绘名", result)
    result = result.replace("啊喂", "啊").replace("吗喂", "吗")
    result = re.sub(r"[\(（](战术掩饰|语感参考|动作参考)[^)）]*?[:：]?\s*", "(", result)

    def extract_reply(history_content: str) -> str:
        try:
            return json.loads(history_content).get("reply", history_content)
        except Exception:
            return history_content

    recent_bot_replies = [
        extract_reply(item["content"])
        for item in prepared.user_mem["history"][-8:]
        if item["role"] == "assistant"
    ]
    if result.strip() in [reply.strip() for reply in recent_bot_replies]:
        logger.warning("⚠️ 检测到复读！强制注入去重指令重新生成...")
        prepared.messages_list[-1]["content"] += (
            "\n🚫【紧急系统警告】：你刚才说过完全一样的话！"
            "这次必须从完全不同的角度切入，换一种表达方式，绝对不能重复！"
        )
        raw_result = await call_deepseek_api(prepared.messages_list, force_json=True)
        result, inner_os = _chat_module()._parse_model_reply(raw_result, prepared.is_toya_context)
    return ChatReply(text=result, inner_os=inner_os)


async def commit_turn(prepared: PreparedTurn, reply: ChatReply, bot_self_id: str) -> None:
    turn = prepared.turn
    user_mem = prepared.user_mem
    user_mem["history"].append({"role": "user", "content": prepared.tagged_user_msg_for_history})
    actual_os = reply.inner_os if reply.inner_os else "（情绪波动）"
    fake_json_history = json.dumps({"inner_os": actual_os, "reply": reply.text}, ensure_ascii=False)
    user_mem["history"].append({"role": "assistant", "content": fake_json_history})
    if len(user_mem["history"]) > MAX_HISTORY_LEN:
        user_mem["history"] = user_mem["history"][-MAX_HISTORY_LEN:]
    save_memory()

    AKITO_STATUS["last_trigger_user"] = turn.user_id
    if turn.user_id == SUPERUSER_QQ:
        AKITO_STATUS.setdefault("last_superuser_trigger_time", {})[str(turn.group_id)] = time.time()
    if turn.group_id:
        record_bot_response(turn.group_id)
        await record_bot_message(turn.group_id, reply.text, bot_self_id)


async def run_chat_turn(event: Event, bot: Bot, message: Message) -> PipelineResult:
    turn = await collect_turn_input(event, bot, message)
    gate = decide_gate(turn)
    if gate.skip_send:
        return PipelineResult(text=None, delay_seconds=0, finish_silently=True)
    if gate.text is not None:
        return PipelineResult(text=gate.text, delay_seconds=gate.delay_seconds)

    prepared = await prepare_turn(turn, gate.sleep_instruction)
    reply = await generate_reply(prepared)
    reply = await post_process_reply(prepared, reply)
    await commit_turn(prepared, reply, str(bot.self_id))
    base_delay = _chat_module().random.uniform(0.8, 2.5)
    typing_delay = min(len(reply.text) * 0.12, 5.0)
    return PipelineResult(text=reply.text, delay_seconds=base_delay + typing_delay)
