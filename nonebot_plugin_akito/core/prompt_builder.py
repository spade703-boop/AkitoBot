"""Shared prompt context assembly for chat-oriented model tasks."""

import asyncio
from dataclasses import dataclass

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
