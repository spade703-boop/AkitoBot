"""Context block normalization and shadow selection for M1.

The first M1 slice only describes and measures context. Callers continue to
send their existing prompts; the selection result is recorded for comparison.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .types import ContextBlock

DEFAULT_CONTEXT_BUDGET_TOKENS = 12000


def estimate_token_count(content: str) -> int:
    """Return a deterministic, privacy-safe token estimate for shadow metrics."""
    text = str(content or "")
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


@dataclass(frozen=True)
class ContextShadowReport:
    """Metadata about a hypothetical selection, without prompt content."""

    stage: str
    budget_tokens: int
    total_blocks: int
    estimated_tokens: int
    selected_sources: tuple[str, ...]
    omitted_sources: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "stage": self.stage,
            "budget_tokens": self.budget_tokens,
            "total_blocks": self.total_blocks,
            "estimated_tokens": self.estimated_tokens,
            "selected_sources": list(self.selected_sources),
            "omitted_sources": list(self.omitted_sources),
        }


class ContextOrchestrator:
    """Normalize context blocks and calculate a non-invasive selection shadow."""

    def __init__(self, *, budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS):
        self.budget_tokens = max(1, int(budget_tokens))

    def normalize(self, blocks: Iterable[Mapping[str, object] | ContextBlock]) -> tuple[ContextBlock, ...]:
        """Normalize metadata while preserving caller order and content."""
        normalized: list[ContextBlock] = []
        for index, block in enumerate(blocks):
            content = str(block.get("content", "") or "")
            source = str(block.get("source", "") or f"block_{index}")
            kind = str(block.get("kind", "") or source)
            priority_value = block.get("priority", 100)
            ttl_value = block.get("ttl_seconds", 0)
            token_value = block.get("token_estimate", 0)
            try:
                priority = int(priority_value or 0)
            except (TypeError, ValueError):
                priority = 100
            try:
                ttl_seconds = max(0, int(ttl_value or 0))
            except (TypeError, ValueError):
                ttl_seconds = 0
            try:
                token_estimate = int(token_value or 0)
            except (TypeError, ValueError):
                token_estimate = 0
            normalized.append(
                {
                    "kind": kind,
                    "content": content,
                    "source": source,
                    "priority": priority,
                    "ttl_seconds": ttl_seconds,
                    "token_estimate": token_estimate or estimate_token_count(content),
                }
            )
        return tuple(normalized)

    def select(self, blocks: Iterable[Mapping[str, object] | ContextBlock]) -> tuple[ContextBlock, ...]:
        """Select highest-priority blocks within the hypothetical budget."""
        normalized = self.normalize(blocks)
        return self._select_normalized(normalized)

    def _select_normalized(self, normalized: tuple[ContextBlock, ...]) -> tuple[ContextBlock, ...]:
        """Select from an already normalized tuple without copying its blocks."""
        ranked = sorted(enumerate(normalized), key=lambda item: (-int(item[1]["priority"]), item[0]))
        selected: list[tuple[int, ContextBlock]] = []
        used_tokens = 0
        for index, block in ranked:
            token_estimate = int(block["token_estimate"] or 0)
            if selected and used_tokens + token_estimate > self.budget_tokens:
                continue
            selected.append((index, block))
            used_tokens += token_estimate
        return tuple(block for _, block in sorted(selected, key=lambda item: item[0]))

    def shadow(
        self,
        blocks: Iterable[Mapping[str, object] | ContextBlock],
        *,
        stage: str,
    ) -> ContextShadowReport:
        """Describe what selection would do without changing the caller's prompt."""
        normalized = self.normalize(blocks)
        selected = self._select_normalized(normalized)
        selected_ids = {id(block) for block in selected}
        selected_sources = tuple(str(block["source"]) for block in selected)
        omitted_sources = tuple(
            str(block["source"])
            for block in normalized
            if id(block) not in selected_ids
        )
        return ContextShadowReport(
            stage=stage,
            budget_tokens=self.budget_tokens,
            total_blocks=len(normalized),
            estimated_tokens=sum(int(block["token_estimate"] or 0) for block in normalized),
            selected_sources=selected_sources,
            omitted_sources=omitted_sources,
        )

    def select_with_report(
        self,
        blocks: Iterable[Mapping[str, object] | ContextBlock],
        *,
        stage: str,
    ) -> tuple[tuple[ContextBlock, ...], ContextShadowReport]:
        """Select blocks and return the same privacy-safe report used by shadow mode."""
        normalized = self.normalize(blocks)
        selected = self._select_normalized(normalized)
        selected_ids = {id(block) for block in selected}
        report = ContextShadowReport(
            stage=stage,
            budget_tokens=self.budget_tokens,
            total_blocks=len(normalized),
            estimated_tokens=sum(int(block["token_estimate"] or 0) for block in normalized),
            selected_sources=tuple(str(block["source"]) for block in selected),
            omitted_sources=tuple(
                str(block["source"]) for block in normalized if id(block) not in selected_ids
            ),
        )
        return selected, report


def build_context_blocks(
    blocks: Iterable[Mapping[str, object] | ContextBlock],
    *,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> tuple[ContextBlock, ...]:
    """Convenience wrapper for callers that only need normalized blocks."""
    return ContextOrchestrator(budget_tokens=budget_tokens).normalize(blocks)


def shadow_context(
    blocks: Iterable[Mapping[str, object] | ContextBlock],
    *,
    stage: str,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> ContextShadowReport:
    """Convenience wrapper for a non-invasive shadow selection report."""
    return ContextOrchestrator(budget_tokens=budget_tokens).shadow(blocks, stage=stage)


def select_context_for_mode(
    blocks: Iterable[Mapping[str, object] | ContextBlock],
    *,
    stage: str,
    active: bool,
    budget_tokens: int = DEFAULT_CONTEXT_BUDGET_TOKENS,
) -> tuple[tuple[ContextBlock, ...], ContextShadowReport]:
    """Return selected blocks when active, while always producing a shadow report."""
    orchestrator = ContextOrchestrator(budget_tokens=budget_tokens)
    if active:
        return orchestrator.select_with_report(blocks, stage=stage)
    normalized = orchestrator.normalize(blocks)
    return normalized, orchestrator.shadow(normalized, stage=stage)
