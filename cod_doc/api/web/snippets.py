"""Сниппет полнотекстового поиска как безопасная разметка (WUI-006).

``search_service.search`` отдаёт кусок исходного текста, где совпадения
обёрнуты в ``<mark>``. Текст — описание задачи или тело документа, то есть
markdown с сырым HTML: вставленный как есть, он исполнил бы чужой
``<img onerror=…>`` у каждого, кто откроет поиск, а оборванный посреди тега
кусок ломал бы вёрстку. Экранируем всё, затем возвращаем только подсветку.
"""

from __future__ import annotations

import html

from markupsafe import Markup

_MARK_OPEN = "&lt;mark&gt;"
_MARK_CLOSE = "&lt;/mark&gt;"


def fts_snippet(raw: str | None) -> Markup:
    """Сниппет, где живы только ``<mark>``; всё остальное экранировано."""
    escaped = html.escape(raw or "", quote=False)
    # Безопасно: строка экранирована целиком строкой выше.
    return Markup(escaped.replace(_MARK_OPEN, "<mark>").replace(_MARK_CLOSE, "</mark>"))
