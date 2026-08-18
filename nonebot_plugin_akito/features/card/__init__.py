"""VBS 卡面知识库功能：目录、别称解析、专用检索与维护指令。"""

from __future__ import annotations

from nonebot.log import logger

from ...core.retrieval import register_corpus, reload_indices
from .catalog import (
    CARD_ALIAS_NOTES,
    CARD_ALIASES,
    CARD_DB,
    CARD_GROUP_ALIAS_NOTES,
    CARD_GROUP_ALIASES,
    CardResolution,
    bind_card_alias,
    bind_card_group_alias,
    get_relevant_cards,
    init_card_catalog,
    normalize_card_alias,
    render_card_fact,
    resolve_card_mentions,
    unbind_card_alias,
    unbind_card_group_alias,
)
from .retrieval import card_retrieval_text


def _register_card_retrieval() -> None:
    register_corpus(
        "cards",
        CARD_DB,
        "cards_embeddings.npz",
        card_retrieval_text,
        rerank_min_score=0.1,
    )


def reload_card_data() -> None:
    """Reload card metadata and keep the feature-owned retrieval registration live."""
    init_card_catalog()
    _register_card_retrieval()


_register_card_retrieval()
try:
    reload_indices()
except Exception as exc:
    logger.debug(f"🔧 卡面检索索引初始化跳过: {exc}")


# Import for side effect: these on_command handlers register on plugin startup.
from . import commands as commands  # noqa: E402,F401


__all__ = [
    "CARD_DB",
    "CARD_ALIASES",
    "CARD_ALIAS_NOTES",
    "CARD_GROUP_ALIASES",
    "CARD_GROUP_ALIAS_NOTES",
    "CardResolution",
    "bind_card_alias",
    "bind_card_group_alias",
    "get_relevant_cards",
    "normalize_card_alias",
    "render_card_fact",
    "resolve_card_mentions",
    "unbind_card_alias",
    "unbind_card_group_alias",
    "reload_card_data",
]
