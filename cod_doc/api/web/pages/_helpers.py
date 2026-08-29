"""Shared helpers used by multiple page handlers."""

from __future__ import annotations

# Re-export: гварда переехала в общий API-слой (ADO-035), чтобы её мог
# использовать и REST-роутер (PATCH /api/config), не только web-страницы.
from cod_doc.api.deps import ensure_loopback_client

__all__ = ["ensure_loopback_client"]


def _masked_api_key(key: str) -> str:
    """Show only the last 4 chars; «sk-test1234» → «…1234»."""
    if not key:
        return ""
    if len(key) <= 4:
        return "…" * len(key)
    return "…" + key[-4:]


def _preview(text: str | None, max_lines: int) -> tuple[str | None, bool]:
    if text is None:
        return None, False
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text, False
    return "\n".join(lines[:max_lines]), True


MASTER_PREVIEW_LINES = 80
