"""Shared helpers used by multiple page handlers."""

from __future__ import annotations

from fastapi import HTTPException, Request

# SYM-003: POST /settings пишет LLM-ключ в config.yaml — принимаем его только
# с loopback. request.client приходит из сокета (не из заголовков), поэтому
# подделать его удалённо нельзя; X-Forwarded-For сознательно не доверяем —
# прокси-развёртывания должны терминировать на loopback.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
# Сентинел Starlette TestClient: реальный TCP-пир никогда не представит эту
# строку, поэтому допускать её безопасно (нужно для in-process web-тестов).
_TESTCLIENT_HOST = "testclient"


def ensure_loopback_client(request: Request) -> None:
    """403, если запрос пришёл не с loopback-интерфейса."""
    host = request.client.host if request.client is not None else None
    if host in _LOOPBACK_HOSTS or host == _TESTCLIENT_HOST:
        return
    raise HTTPException(status_code=403, detail="This endpoint is loopback-only")


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
