"""Публичный REST API v1 (RFC 22 §3.3, SYM-006C).

Router + pydantic-схемы поверх тех же сервисов, что обслуживают CLI и MCP:

- ``POST /api/v1/projects/{slug}/findings`` — ingest внешних findings через
  реестр адаптеров ``services/ingest_service`` (тот же пайплайн, что
  ``cod-doc ingest``): parse → fingerprint → atomic upsert → activity event.
- ``GET /api/v1/projects/{slug}/context`` — ``context_service.context_get``.
- ``GET /api/v1/projects/{slug}/search`` — ``search_service.search`` (FTS5).

**Bearer-гейт (RFC 29 §3.8, CLO-036).** ``/api/v1`` закрыт ASGI-гейтом
``api/auth.py`` — токены и роли из хранилища доступа (``access_service``), а не
единый ``COD_DOC_API_TOKEN`` из RFC 22 §3.3. Пока хранилища нет, гейт пропускает
всё; ``/api/v1/projects/{slug}`` открыт только участнику проекта. Legacy
``/api/*`` заморожен и гейта не получит — наружу его не выставляют.
"""
