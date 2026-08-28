"""Pydantic-схемы для REST API v1 (SYM-006C)."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ── Findings ingest ───────────────────────────────────────────────────────────

ContextTargetKind = Literal["document", "task", "plan", "module"]
ContextDepth = Literal["L0", "L1", "L2", "L3"]
SearchScope = Literal["task", "doc", "story", "adr", "finding"]


class FindingIngestRequest(BaseModel):
    """Ingest внешнего export'а findings через именованный адаптер.

    ``adapter`` — ключ из ``INGEST_ADAPTERS`` (``ai_review``,
    ``zairgrush_findings``, ``zairgrush_tasks``). ``export`` — сырой JSON
    export'а, который парсит адаптер (для ``ai_review`` обязателен
    ``export.version == 1``).
    """

    adapter: str
    export: dict[str, Any]
    dry_run: bool = False


class FindingIngestResponse(BaseModel):
    adapter: str
    source_run_id: str
    total: int
    created: int
    updated: int
    dry_run: bool


# ── Context ───────────────────────────────────────────────────────────────────


class ContextMeta(BaseModel):
    depth: str
    effective_depth: str
    tokens_used: int
    truncated: bool
    generated_at: str


class ContextResponse(BaseModel):
    target_summary: dict[str, Any]
    core: dict[str, Any]
    related: dict[str, Any]
    hints: dict[str, Any]
    meta: ContextMeta


# ── Search ────────────────────────────────────────────────────────────────────


class SearchHit(BaseModel):
    ref: str
    title: str
    snippet: str
    score: float


class SearchResponse(BaseModel):
    query: str
    total: int
    by_kind: dict[str, list[SearchHit]]
