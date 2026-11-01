"""Адреса веб-UI, которые нужны больше чем одной странице (WUI-006).

Вкладки проекта рисует ``_layout/project_tabs.html``, а палитра поиска
(``pages/palette.py``) предлагает те же вкладки как переходы. Один список на
обоих — иначе новая вкладка появилась бы в полосе и потерялась в палитре.
То же с адресами хитов поиска: их строят и страница ``/search``, и палитра.
"""

from __future__ import annotations

from typing import Final, NamedTuple


class ProjectTab(NamedTuple):
    key: str
    label: str
    #: Хвост адреса после ``/p/<slug>``; пустой — обзор проекта.
    suffix: str


PROJECT_TABS: Final[tuple[ProjectTab, ...]] = (
    ProjectTab("overview", "Overview", ""),
    ProjectTab("run", "Agent", "/run"),
    ProjectTab("docs", "Docs", "/docs"),
    ProjectTab("tasks", "Tasks", "/tasks"),
    ProjectTab("stories", "Stories", "/stories"),
    ProjectTab("plans", "Plans", "/plans"),
    ProjectTab("roadmap", "Roadmap", "/roadmap"),
    ProjectTab("adr", "ADRs", "/adr"),
    ProjectTab("scenarios", "Scenarios", "/scenarios"),
    ProjectTab("questions", "Questions", "/questions"),
    ProjectTab("proposals", "Proposals", "/proposals"),
    ProjectTab("metrics", "Metrics", "/metrics"),
    ProjectTab("commits", "Commits", "/commits"),
    ProjectTab("code_refs", "Code refs", "/code-refs"),
    ProjectTab("search", "Search", "/search"),
    ProjectTab("routines", "Routines", "/routines"),
    ProjectTab("costs", "Costs", "/costs"),
    ProjectTab("revisions", "Revisions", "/revisions"),
)


def project_tabs(slug: str) -> list[tuple[str, str, str]]:
    """``(key, label, href)`` вкладок проекта ``slug`` в порядке полосы."""
    return [(t.key, t.label, f"/p/{slug}{t.suffix}") for t in PROJECT_TABS]


#: Страница сущности по виду хита FTS. У находки (`finding`) своей страницы
#: нет — хит без ссылки.
_ENTITY_PATH: Final[dict[str, str]] = {
    "task": "tasks",
    "doc": "docs",
    "story": "stories",
    "adr": "adr",
    "question": "questions",
}


def hit_url(slug: str, kind: str, ref: str) -> str | None:
    """Адрес хита поиска или ``None``, если у вида нет страницы."""
    section = _ENTITY_PATH.get(kind)
    return f"/p/{slug}/{section}/{ref}" if section else None
