"""Точка сборки подписчиков событий services (SVC-007).

`doc_service` публикует события (`doc_events`), `link_service` на них
реагирует; связывает их этот модуль, и только он знает обе стороны. Его
зовут точки входа до первой записи документа: корневая группа CLI, модуль
MCP-сервера, модуль web-приложения; в тестах — autouse-фикстура
`tests/conftest.py`. Пропущенная точка входа не молчит: публикация без
подписчика падает `doc_events.MissingSubscriberError`.

Импорт тянет `link_service` (а с ним SQLAlchemy и почти весь services), поэтому
CLI зовёт `install()` не на импорте, а в колбэке корневой группы и только для
команд, которые работают с БД.
"""

from __future__ import annotations

from cod_doc.services.link_service import doc_event_handlers


def install() -> None:
    """Подписать обработчики на события документов. Идемпотентна."""
    doc_event_handlers.register()
