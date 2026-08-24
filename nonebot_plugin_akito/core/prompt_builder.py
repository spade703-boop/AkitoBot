"""Shared prompt context assembly for chat-oriented model tasks."""

import asyncio
from dataclasses import dataclass
import json

from .context import (
    RelationshipMatch,
    find_relationship_match,
    get_base_persona,
    get_relevant_examples,
    get_relevant_pjsk,
    get_song_memories,
    get_song_mention,
)
from .retrieval import RetrievalContext, build_retrieval_context


@dataclass(frozen=True)
class SharedPromptContext:
    """Task-neutral prompt fragments gathered from the same user query."""

    persona: str
    relationship_match: RelationshipMatch | None
    script_examples: str
    pjsk_block: str
    song_memories: str
    song_mention: str


@dataclass(frozen=True)
class PromptFrame:
    """The five common sections shared by task-specific system prompts."""

    system_header: str
    environment_blocks: tuple[str, ...]
    role_knowledge_blocks: tuple[str, ...]
    task_context_blocks: tuple[str, ...]
    task_rule_blocks: tuple[str, ...]


@dataclass(frozen=True)
class JsonFieldSpec:
    """One field in a model-output JSON example."""

    name: str
    example: str | list[str]


@dataclass(frozen=True)
class JsonSchemaSpec:
    """Structured description of a task's JSON output contract."""

    fields: tuple[JsonFieldSpec, ...]
    instruction: str = ""


def _render_blocks(blocks: tuple[str, ...]) -> str:
    return "\n".join(str(block) for block in blocks)


def render_json_schema(schema: JsonSchemaSpec) -> str:
    """Render a stable JSON-shaped output example from ordered field specs."""
    lines = [schema.instruction] if schema.instruction else []
    lines.append("{")
    for index, field in enumerate(schema.fields):
        value = json.dumps(field.example, ensure_ascii=False)
        suffix = "," if index < len(schema.fields) - 1 else ""
        lines.append(f'  {json.dumps(field.name, ensure_ascii=False)}: {value}{suffix}')
    lines.append("}")
    return "\n".join(lines)


def render_prompt_frame(frame: PromptFrame, schema: JsonSchemaSpec) -> str:
    """Render the shared five-section system prompt skeleton."""
    return f"""
{frame.system_header}

# 1. 环境与状态
{_render_blocks(frame.environment_blocks)}

# 2. 角色与知识
{_render_blocks(frame.role_knowledge_blocks)}

# 3. 当前任务上下文
{_render_blocks(frame.task_context_blocks)}

# 4. 任务规则
{_render_blocks(frame.task_rule_blocks)}

# 5. 强制输出格式 (JSON)
{render_json_schema(schema)}
""".strip()


async def build_shared_prompt_context(
    query: str,
    *,
    retrieval_ctx: RetrievalContext | None = None,
    script_limit: int = 5,
    pjsk_limit: int = 6,
) -> SharedPromptContext:
    """Build reusable persona, relationship and retrieval fragments once per query."""
    query_text = query or ""
    active_retrieval_ctx = retrieval_ctx
    if active_retrieval_ctx is None:
        active_retrieval_ctx = await build_retrieval_context(
            query_text,
            enable_expansion=bool(query_text and len(query_text.strip()) >= 3),
        )

    script_examples, pjsk_block = await asyncio.gather(
        get_relevant_examples(query_text, script_limit, retrieval_ctx=active_retrieval_ctx),
        get_relevant_pjsk(query_text, pjsk_limit, retrieval_ctx=active_retrieval_ctx),
    )
    return SharedPromptContext(
        persona=get_base_persona(),
        relationship_match=find_relationship_match(query_text),
        script_examples=script_examples,
        pjsk_block=pjsk_block,
        song_memories=get_song_memories(),
        song_mention=get_song_mention(query_text),
    )


def render_main_chat_prompt(
    *,
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
    """Render the main chat system prompt using the shared five-section frame."""
    frame = PromptFrame(
        system_header=system_header,
        environment_blocks=(
            f"- 当前系统时间：{current_time}",
            f"- 你的生物钟状态：{daily_status}",
            toya_anchor,
            time_gap_awareness,
            f"- 今日特殊日历：{festival_buff}",
            morning_run_buff,
            sleep_buffer_buff,
        ),
        role_knowledge_blocks=(
            base_persona,
            relationship_context,
            script_examples,
            f"🎮【PJSK 世界观/黑话库】：\n{pjsk_block}",
            song_memories,
            f"🧠【你的长期记忆】：\n{long_term_memory_text}",
        ),
        task_context_blocks=(
            "🎯【当前交互对象】：",
            interact_instruction,
            referenced_relationship_instruction,
            "📜【群聊背景流】",
            group_context,
        ),
        task_rule_blocks=(
            f"⚡【强制临时状态/指令】：\n{reality_overwrite_instruction}",
            acting_guide,
            sleep_instruction,
            fact_grounding_instruction,
            vitality_guide,
            memory_capture_rule,
            tone_limiter,
        ),
    )
    schema = JsonSchemaSpec(
        fields=(
            JsonFieldSpec("inner_os", schema_inner_os),
            JsonFieldSpec("action", schema_action),
            JsonFieldSpec("dialogue", schema_dialogue),
        )
    )
    return render_prompt_frame(frame, schema)


def render_impression_analysis_prompt(
    *,
    target_name: str,
    recent_reply_limit: int,
    relationship_context: str = "",
) -> str:
    """Render the persona-free analysis stage for group impressions."""
    frame = PromptFrame(
        system_header="【系统级绝对指令：群印象材料分析】",
        environment_blocks=("本阶段不注入角色人设、世界线或表达口吻。",),
        role_knowledge_blocks=(
            "你是中立的材料分析器。只整理材料中能够成立的事实、长期观察和不确定边界，不写最终回复。",
        ),
        task_context_blocks=(
            f"分析对象是【{target_name}】。用户消息会分别提供目标本人的历史材料、近期上下文，以及最近最多 {recent_reply_limit} 条已发送群印象。",
            "历史材料用于形成观察；近期已发送群印象只用于识别重复表达，绝不能作为目标事实。",
            relationship_context or "本次没有可用的人物关系资料；不要凭空补充角色关系。",
        ),
        task_rule_blocks=(
            "【分析规则】\n"
            "1. evidence 必须是包含2-4个字符串的 JSON 数组；每条都必须逐字来自目标本人发言，并保留足以辨认含义的2-16个字短片段，不能输出单个字符串。\n"
            "2. observations 描述一段时间内能够成立的具体发现，不使用角色口吻，不写评价台词，也不把兴趣名称直接当作性格。\n"
            "3. 可以记录有依据但尚不能确定的理解，将其放入 uncertainties，不得把推测写成事实。\n"
            "4. avoid_patterns 只抽象描述近期评价反复使用的组织方式、转折方式或收尾方式，不复制旧评价原句，也不引入旧评价中的人物事实。\n"
            "5. 材料中出现东云彰人、彰人、Akito、akito、小彰或akt时，按人物资料理解为你自己；出现冬弥或其他已识别角色时，按对应关系资料理解，不能写成陌生第三方。\n"
            "6. specific 需要 1-4 条 observations；limited 最多保留 1 条确定现象，并明确材料为何不足。",
        ),
    )
    schema = JsonSchemaSpec(
        fields=(
            JsonFieldSpec("mode", "材料充分时填 specific；材料零碎时填 limited"),
            JsonFieldSpec(
                "evidence",
                [f"从【{target_name}】本人发言中原样复制2-16个字", "再复制一处本人发言作为依据"],
            ),
            JsonFieldSpec("observations", ["跨多条材料能够成立的具体发现"]),
            JsonFieldSpec("uncertainties", ["有依据但仍需保留不确定性的理解；没有则为空数组"]),
            JsonFieldSpec("avoid_patterns", ["近期评价中已经重复出现的抽象表达结构；没有则为空数组"]),
        ),
        instruction="你必须且只能输出合法的 JSON 格式。不要用 ```json 包裹！",
    )
    return render_prompt_frame(frame, schema)


def render_impression_reply_prompt(
    *,
    persona: str,
    state_overlay_prompt: str,
    target_name: str,
    is_querying_other: bool,
    specific_max_length: int,
    limited_max_length: int,
    candidate_count: int = 3,
    relationship_context: str = "",
) -> str:
    """Render the persona-aware expression stage for group impressions."""
    if is_querying_other:
        scene_description = f"群友正在问你对【{target_name}】的印象；你是在向查询者谈论她。"
        addressing_rule = (
            f"固定开头之后，提到【{target_name}】时使用女性第三人称“她”。"
            "不要把她当成当前对话者写成“你”，也不要称作“他、这个人、这人、这家伙”。"
        )
    else:
        scene_description = f"【{target_name}】本人正在当面问你对自己的印象；你是在直接对本人说话。"
        addressing_rule = (
            f"固定开头之后，提到【{target_name}】时使用第二人称“你”。"
            "不要把对方写成“他、她、这个人、这人、这家伙”。"
        )

    task_context = f"""
【群印象表达任务】
{scene_description}
用户消息会提供已经通过事实校验的材料分析结果。你只根据该结果形成自己的整体看法，不重新分析原始聊天，也不补写分析中没有的经历、关系或确定动机。
完整人设和当前世界线负责决定你关注什么、怎么措辞、在哪里停下；不要为了体现角色性格，把对方硬套进你自己的核心特质或固定价值判断。
""".strip()
    task_rules = f"""
【称呼与回复要求】
1. {addressing_rule}
2. 必须符合东云彰人的口吻，并服从当前生效的世界线覆写。
3. 每条候选都必须用“对{target_name}的印象是……”开头，随后直接进入自然看法；不得写人物小传、分析报告或标签清单。
4. 分析结果中的 avoid_patterns 是本次必须避开的近期表达习惯。规避的是句式组织和收尾方式，不得因此歪曲材料或强行制造不同结论。
5. 生成 {candidate_count} 条事实结论一致、但侧重点、句法组织、停顿节奏和结束位置有实质差异的候选；不能只替换近义词。
6. 不要求补充态度、转折、总结或收尾。一个观察已经说清楚时可以直接结束；确有自然看法时也可以表达。
7. `specific` 每条最多 {specific_max_length} 字，`limited` 每条最多 {limited_max_length} 字；不设最低字数和固定句数。
8. 纯文本，不要括号动作；inner_os 不会发到群里。
9. 材料提到彰人相关别名时，把他当作你自己；提到冬弥或其他已识别角色时，按关系资料理解，不要在候选中把他们写成毫无关联的陌生第三方。
""".strip()
    frame = PromptFrame(
        system_header="【系统级绝对指令：群印象任务】",
        environment_blocks=(state_overlay_prompt,),
        role_knowledge_blocks=(persona, relationship_context or "本次没有可用的人物关系资料；不要凭空补充角色关系。"),
        task_context_blocks=(task_context,),
        task_rule_blocks=(task_rules,),
    )
    schema = JsonSchemaSpec(
        fields=(
            JsonFieldSpec("inner_os", "说明本次选择了哪些观察，以及候选之间如何避免重复结构。"),
            JsonFieldSpec(
                "replies",
                [f"第 {index + 1} 条以‘对{target_name}的印象是……’开头的完整候选" for index in range(candidate_count)],
            ),
        ),
        instruction="你必须且只能输出合法的 JSON 格式。不要用 ```json 包裹！",
    )
    return render_prompt_frame(frame, schema)


def render_impression_prompt(
    *,
    persona: str,
    state_overlay_prompt: str,
    target_name: str,
    is_querying_other: bool,
    specific_max_length: int,
    limited_max_length: int,
    relationship_context: str = "",
) -> str:
    """Compatibility wrapper for the persona-aware impression renderer."""
    return render_impression_reply_prompt(
        persona=persona,
        state_overlay_prompt=state_overlay_prompt,
        target_name=target_name,
        is_querying_other=is_querying_other,
        specific_max_length=specific_max_length,
        limited_max_length=limited_max_length,
        relationship_context=relationship_context,
    )


def render_auto_chat_prompt(
    *,
    persona: str,
    time_str: str,
    toya_anchor: str,
    scene_desc: str,
    group_context: str,
    relation_info: str,
    song_info: str,
    script_examples: str,
    pjsk_block: str,
    cool_guy_filter: str,
    task_logic: str,
    inner_os_guide: str,
) -> str:
    """Render the random interjection system prompt using the shared frame."""
    frame = PromptFrame(
        system_header="【系统级绝对指令：潜水思维链与格式强制】",
        environment_blocks=(f"【系统物理时间】当前时间是：{time_str}。绝对不可弄错时间。", toya_anchor),
        role_knowledge_blocks=(
            persona,
            f"【人际资料】{relation_info}",
            script_examples,
            f"🎮【PJSK 世界观/黑话库】：\n{pjsk_block}",
            song_info,
            cool_guy_filter,
        ),
        task_context_blocks=(
            f"【当前回复目标（唯一）】{scene_desc}",
            f"【近期群聊背景（仅用于理解当前目标，禁止回复其中旧消息）】\n{group_context}",
        ),
        task_rule_blocks=(f"【任务目标与回复逻辑 (极其重要)】\n{task_logic}",),
    )
    schema = JsonSchemaSpec(
        fields=(
            JsonFieldSpec("inner_os", inner_os_guide),
            JsonFieldSpec("anchor", "若要回复，从当前消息中原样复制至少2个字符作为依据；决定静默时留空。禁止复制历史消息。"),
            JsonFieldSpec("reply", "你实际发在群里的话。要求：1. 纯文本，极少用(动作)。2. 善用逗号连接短句，语感流畅。3. 绝不乱接别人的话。4. 旁观模式下如果决定不理，必须输出空字符串。"),
        ),
        instruction="你必须且只能输出合法的 JSON 格式。不要用 ```json 包裹！",
    )
    return render_prompt_frame(frame, schema)
