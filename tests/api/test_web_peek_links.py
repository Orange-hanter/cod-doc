"""WUI-001: ссылки на карточку задачи открываются в шторке.

Шторку включает атрибут `data-peek` на ссылке, а не шаблон URL: под
`/tasks/` живут и другие страницы (`/tasks/legacy`), и угадывание по пути
открыло бы их сломанной шторкой. Цена опт-ина — новая ссылка без атрибута
молча ведёт на полную страницу. Этот тест её ловит: сканирует шаблоны и
требует `data-peek` на каждом `<a>`, чей href — карточка задачи.
"""

from __future__ import annotations

import re
from pathlib import Path

import cod_doc
from cod_doc.api.web.templates_env import PEEKABLE_KINDS, peekable

TEMPLATES = Path(cod_doc.__file__).parent / "templates" / "web"

# `<a …>` целиком: атрибуты могут переноситься на следующие строки.
_ANCHOR = re.compile(r"<a\b[^>]*>", re.S)
# href карточки задачи: `/p/{{ … }}/tasks/{{ … }}"` — ровно id, без хвоста
# `/status`, `/fields/…` и без литерала вроде `/tasks/legacy`.
_TASK_DETAIL_HREF = re.compile(r'href="/p/\{\{[^}]+\}\}/tasks/\{\{[^}]+\}\}"')

# Шаблоны, где href пришёл из Python (`_link_href`, `_DEEPLINK_BY_KIND`)
# и вид цели известен только строкой.
_PYTHON_BUILT_LINKS = (
    "project/search.html",
    "project/question_show.html",
    "project/scenario_show.html",
)


def _task_detail_anchors() -> list[tuple[str, str]]:
    found = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for tag in _ANCHOR.findall(path.read_text()):
            if _TASK_DETAIL_HREF.search(tag):
                found.append((str(path.relative_to(TEMPLATES)), tag))
    return found


def test_scan_finds_task_links() -> None:
    """Сканер не ослеп: ссылок на карточку в шаблонах десятки, не ноль."""
    assert len(_task_detail_anchors()) >= 10


def test_every_task_detail_link_opens_in_sheet() -> None:
    missing = [
        f"{name}: {' '.join(tag.split())}"
        for name, tag in _task_detail_anchors()
        if "data-peek" not in tag
    ]
    assert not missing, "ссылка на карточку задачи без data-peek:\n" + "\n".join(missing)


def test_non_detail_task_pages_stay_plain_links() -> None:
    """`/tasks/legacy` и прочие страницы под `/tasks/` — не карточки."""
    wrong = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        for tag in _ANCHOR.findall(path.read_text()):
            if re.search(r'href="/p/\{\{[^}]+\}\}/tasks/[a-z]', tag) and "data-peek" in tag:
                wrong.append(f"{path.relative_to(TEMPLATES)}: {' '.join(tag.split())}")
    assert not wrong, "data-peek на ссылке не на карточку:\n" + "\n".join(wrong)


def test_python_built_links_ask_peekable() -> None:
    for name in _PYTHON_BUILT_LINKS:
        assert "peekable(" in (TEMPLATES / name).read_text(), name


def test_peekable_kinds() -> None:
    assert peekable("task")
    assert not peekable("story")
    assert not peekable("url")
    assert not peekable(None)
    assert "task" in PEEKABLE_KINDS
