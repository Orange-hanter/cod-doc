"""Публичный REST API v1 (RFC 22 §3.3, SYM-006C).

Router + pydantic-схемы поверх тех же сервисов, что обслуживают CLI и MCP:

- ``POST /api/v1/projects/{slug}/findings`` — ingest внешних findings через
  реестр адаптеров ``services/ingest_service`` (тот же пайплайн, что
  ``cod-doc ingest``): parse → fingerprint → atomic upsert → activity event.
- ``GET /api/v1/projects/{slug}/context`` — ``context_service.context_get``.
- ``GET /api/v1/projects/{slug}/search`` — ``search_service.search`` (FTS5).

**Bearer-гейт (контракт RFC 22 §3.3).** ``/api/v1`` — единственная
поверхность будущего Bearer-гейта: ``COD_DOC_API_TOKEN``, ASGI-middleware
только на ``/api/v1``, constant-time compare, 401 JSON. Гейт включается при
появлении первого удалённого вызывающего; legacy ``/api/*`` заморожен и
никогда его не получит.
"""
