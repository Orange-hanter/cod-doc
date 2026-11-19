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
    ProjectTab("activity", "Feed", "/activity"),
)


def project_tabs(slug: str) -> list[tuple[str, str, str]]:
    """``(key, label, href)`` вкладок проекта ``slug`` в порядке полосы."""
    return [(t.key, t.label, f"/p/{slug}{t.suffix}") for t in PROJECT_TABS]


class TabGroup(NamedTuple):
    key: str
    label: str
    #: Ключи вкладок группы из ``PROJECT_TABS``, в порядке показа.
    tabs: tuple[str, ...]


#: Разделы проекта (редизайн Ledger, WUI-008): 18 вкладок в одну полосу
#: переносились в два-три ряда, и активную приходилось искать глазами. Теперь
#: первая строка — шесть разделов, вторая — вкладки текущего раздела; вкладки
#: остальных разделов открываются по наведению или фокусу. Каждая вкладка
#: обязана войти ровно в одну группу — стережёт tests/api/test_web_tabs.py.
TAB_GROUPS: Final[tuple[TabGroup, ...]] = (
    TabGroup("overview", "Overview", ("overview", "run", "metrics", "costs")),
    TabGroup("docs", "Docs", ("docs", "scenarios", "code_refs", "search")),
    TabGroup("work", "Work", ("tasks", "stories")),
    TabGroup("plans", "Plans", ("plans", "roadmap")),
    TabGroup("decisions", "Decisions", ("adr", "questions", "proposals")),
    TabGroup("activity", "Activity", ("activity", "revisions", "commits", "routines")),
)


class TabGroupView(NamedTuple):
    key: str
    label: str
    #: Адрес первой вкладки группы — куда ведёт клик по разделу.
    href: str
    current: bool
    tabs: list[tuple[str, str, str]]


def project_tab_groups(slug: str, active: str) -> list[TabGroupView]:
    """Разделы проекта для полосы вкладок; ``current`` — у раздела вкладки ``active``."""
    by_key = {key: (key, label, href) for key, label, href in project_tabs(slug)}
    return [
        TabGroupView(
            key=group.key,
            label=group.label,
            href=by_key[group.tabs[0]][2],
            current=active in group.tabs,
            tabs=[by_key[key] for key in group.tabs],
        )
        for group in TAB_GROUPS
    ]


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
