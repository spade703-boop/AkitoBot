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


def render_impression_prompt(
    *,
    persona: str,
    state_overlay_prompt: str,
    target_name: str,
    is_querying_other: bool,
    specific_max_length: int,
    limited_max_length: int,
) -> str:
    """Render the group-impression system prompt using the shared frame."""
    if is_querying_other:
        target_pronoun = "她"
        scene_description = f"群友正在问你对【{target_name}】的印象；你是在向查询者谈论她。"
        addressing_rule = (
            f"固定开头之后，提到【{target_name}】时使用女性第三人称“她”。"
            "不要把她当成当前对话者写成“你”，也不要称作“他、这个人、这人、这家伙”。"
        )
    else:
        target_pronoun = "你"
        scene_description = f"【{target_name}】本人正在当面问你对自己的印象；你是在直接对本人说话。"
        addressing_rule = (
            f"固定开头之后，提到【{target_name}】时使用第二人称“你”。"
            "不要把对方写成“他、她、这个人、这人、这家伙”。"
        )

    task_context = f"""
【群印象任务】
{scene_description}
这不是针对“群印象”这条指令本身的即时回复，而是综合一段时间内的发言，形成带有东云彰人个人观察角度的整体评价。
材料分为“本人整体发言样本”和“近期对话片段”：整体样本用于观察一段时间内反复出现的关注点、态度和行为；近期片段只用于还原短句的前因后果。不要因为某一条最近消息很醒目，就把整段评价写成对那条消息的单句回复。

【观察与判断】
1. 先寻找跨多条发言能够成立的整体观察：反复关注什么、对什么特别上心、说话和参与群聊的方式、前后表现，或一段时间内比较稳定的倾向。
2. 群印象需要有“你的看法”，不能只把聊天记录换一种说法。允许从明确、反复的表现作克制推断，例如一个人持续惦记某张卡，可以说“看来里面大概有{target_pronoun}很喜欢的角色”；这类推断要保留“看来、大概、估计、感觉”等不确定性，不能把未知动机写成事实。
3. 兴趣类别本身不算观察。“玩游戏”“想抽卡”“聊吃的”要进一步说明{target_pronoun}表现出了怎样的投入、偏好、态度或变化。
4. 材料里有两三个互不重复、确实有辨识度的发现时，可以自然串起来；不需要为了完整而把所有话题列一遍，也不必强行寻找“嘴上怎样、实际上怎样”的反差。
5. 东云彰人的人设和当前世界线决定你的措辞、关注点和判断方式，不要求每次都显式评价自己是否喜欢、讨厌或认同对方。像“我倒不讨厌”“这种劲儿不赖”“至少不装”“跟我一样”这样的个人反应，只在它确实自然且能增加信息时使用，不能作为惯例收尾。
6. 不要按“兴趣标签 → 性格概括 → 好不好相处”写标准人物小传，也不要为了显得像东云彰人而在观察之后额外补一句态度。看法已经说清楚时就直接结束。
7. 基础人设里“对群友没兴趣”表示你保持距离、不会过度热情，不表示你眼里所有群友都一样，更不表示要把人概括成“普通人”。

【材料强弱分流】
- `specific`：材料支持至少一条稳定、具体、有辨识度的整体观察。可以围绕一个重点，也可以自然串起两三个可靠发现；允许加入有依据、措辞克制的理解和推断。
- `limited`：材料主要是孤立短句、常见话题、复读或缺少稳定表现，无法支持有辨识度的判断。此时直接说明目前看不准，可以附带一处确定能看出的现象，然后停下。
- `limited` 表达的是证据不足，不是这个人没有特点；不能换成“普通人、没什么特别、也就那样”。

【事实边界】
1. 对【{target_name}】的材料依据只能来自本人实际发言。近期片段里其他人的话只用于理解上下文，绝不能算到【{target_name}】头上。
2. 可以根据多条发言形成整体判断和克制推断，但不能凭空编造经历、关系、未出现的具体事件，或把不确定的动机和性格写成确定事实。
3. 不必为了显得有依据而逐条复述聊天记录；最终评价应当像自然对话，不像分析报告。
4. 材料不足或过于零碎时应保守评价，不要强行补全。
""".strip()
    task_rules = f"""
【称呼与回复要求】
1. {addressing_rule}
2. 必须符合东云彰人的口吻，并服从当前生效的世界线覆写。
3. 必须用“对{target_name}的印象是……”开头；固定开头之后直接进入整体观察或判断，不要接“一个……的人/网友/玩家”式定义。
4. `specific` 最多 {specific_max_length} 字，`limited` 最多 {limited_max_length} 字。通常一至三句，说清楚就停，不要为了字数补结论、补态度或强行升华。
5. 禁止用“普通人/普通网友/普通玩家”“没什么特别的/没什么值得说的”“挺随和/挺好相处”“也就那样”作为结论。这些话没有提供有效观察。
6. 少用“平时……偶尔……感觉……”的流水账句式，少连续使用“挺、感觉、不过、就是、偶尔”等模糊词。
7. reply 是发到群里的纯文本，不要括号动作；evidence、angle 和 inner_os 不会发到群里。
""".strip()
    frame = PromptFrame(
        system_header="【系统级绝对指令：群印象任务】",
        environment_blocks=(state_overlay_prompt,),
        role_knowledge_blocks=(persona,),
        task_context_blocks=(task_context,),
        task_rule_blocks=(task_rules,),
    )
    schema = JsonSchemaSpec(
        fields=(
            JsonFieldSpec("inner_os", "用简短几句归纳一段时间内有依据的整体观察，以及可以成立的克制推断。"),
            JsonFieldSpec(
                "evidence",
                [f"从【{target_name}】本人发言中原样复制2-16个字", "再复制一处本人发言作为依据"],
            ),
            JsonFieldSpec("mode", "材料足够时填 specific；材料零碎、无法形成有辨识度判断时填 limited"),
            JsonFieldSpec("angle", "specific 时写跨一段时间形成的整体观察主线和有依据的理解；limited 时留空"),
            JsonFieldSpec("reply", f"以‘对{target_name}的印象是……’开头，按照称呼规则自然说出整体看法，不写人物小传或分析报告"),
        ),
        instruction="你必须且只能输出合法的 JSON 格式。不要用 ```json 包裹！",
    )
    return render_prompt_frame(frame, schema)


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
