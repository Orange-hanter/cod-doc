"""WUI-001/002: ссылки на карточку задачи и документа открываются в шторке.

Шторку включает атрибут `data-peek` на ссылке, а не шаблон URL: под
`/tasks/` и `/docs/` живут и другие страницы (`/tasks/legacy`, `/docs/new`),
и угадывание по пути открыло бы их сломанной шторкой. Цена опт-ина — новая
ссылка без атрибута молча ведёт на полную страницу. Этот тест её ловит:
сканирует шаблоны и требует решения на каждом `<a>`, чей href — карточка.
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
# href карточки документа: ключ — выражение, дальше разве что `#якорь`
# (в том числе условный `{% if %}#…{% endif %}`), но не `/expand` и т.п.
_DOC_DETAIL_HREF = re.compile(r'href="/p/\{\{[^}]+\}\}/docs/\{\{[^}]+\}\}(?:"|#|\?|\{%)')
# Страница под `/tasks/` или `/docs/`, которая не карточка: литеральный
# хвост (что угодно, кроме `{` — начала `{{ id }}` — и конца атрибута) либо
# выражение и дальше `/…` — подстраница (`{{ kind }}/new`, `{{ key }}/expand`).
_NON_DETAIL_HREF = re.compile(r'href="/p/\{\{[^}]+\}\}/(?:tasks|docs)/(?:[^{"]|\{\{[^}]+\}\}/)')
_HREF_VALUE = re.compile(r'href="([^"]*)"')
# Сырой вид документа (`?raw=1`) — режим полной страницы, шторка всегда
# отрисованная: такой ссылке data-peek не нужен.
_RAW_VIEW = "?raw=1"


def _has_attr(tag: str, name: str) -> bool:
    """Атрибут `name` именно этим именем: `data-peek-off` — не `data-peek`.

    `\b` здесь не годится: дефис для него граница слова, и `\bdata-peek\b`
    совпал бы с `data-peek-off`. Слева — не буква и не дефис, справа —
    пробел, `=`, `/`, конец тега или `{` (в исходнике шаблона за атрибутом
    сразу идёт `{% endif %}`).
    """
    return re.search(rf"(?<![\w-]){re.escape(name)}(?=[\s=/>{{])", tag) is not None


def _href(tag: str) -> str:
    m = _HREF_VALUE.search(tag)
    return m.group(1) if m else ""


# Шаблоны, где href пришёл из Python (`_link_href`, `_DEEPLINK_BY_KIND`)
# и вид цели известен только строкой: шаблон → (выражение href, вызов
# peekable с видом именно этой ссылки). В поиске вид — ключ группы `kind`
# из `result.by_kind`; у связей вопроса и сценария — `l.to_kind`.
_PYTHON_BUILT_LINKS = {
    "project/search.html": ("{{ h.url }}", "peekable(kind)"),
    "project/question_show.html": ("{{ l.href }}", "peekable(l.to_kind)"),
    "project/scenario_show.html": ("{{ l.href }}", "peekable(l.to_kind)"),
}


def _anchors() -> list[tuple[str, str]]:
    return [
        (str(path.relative_to(TEMPLATES)), tag)
        for path in sorted(TEMPLATES.rglob("*.html"))
        for tag in _ANCHOR.findall(path.read_text(encoding="utf-8"))
    ]


def _show(name: str, tag: str) -> str:
    return f"{name}: {' '.join(tag.split())}"


def _detail_anchors(pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    return [(name, tag) for name, tag in _anchors() if pattern.search(tag)]


def test_scan_finds_card_links() -> None:
    """Сканер не ослеп: ссылок на карточки в шаблонах десятки, не ноль."""
    assert len(_detail_anchors(_TASK_DETAIL_HREF)) >= 10
    assert len(_detail_anchors(_DOC_DETAIL_HREF)) >= 10


def test_every_task_detail_link_opens_in_sheet() -> None:
    missing = [
        _show(name, tag)
        for name, tag in _detail_anchors(_TASK_DETAIL_HREF)
        if not _has_attr(tag, "data-peek")
    ]
    assert not missing, "ссылка на карточку задачи без data-peek:\n" + "\n".join(missing)


def test_every_doc_detail_link_decides_on_sheet() -> None:
    """Ссылка на документ — шторка (`data-peek`) или осознанный отказ (`data-no-peek`)."""
    undecided = [
        _show(name, tag)
        for name, tag in _detail_anchors(_DOC_DETAIL_HREF)
        # Исключение — по значению href, а не по подстроке тега: `?raw=1` в
        # `hx-get` или `data-*` не освобождает ссылку на карточку от решения.
        if _RAW_VIEW not in _href(tag)
        and not _has_attr(tag, "data-peek")
        and not _has_attr(tag, "data-no-peek")
    ]
    assert not undecided, "ссылка на документ без data-peek/data-no-peek:\n" + "\n".join(undecided)


def test_non_detail_href_pattern() -> None:
    """Шаблон ловит любой литеральный хвост, а не только строчную латиницу."""
    for tail in (
        "tasks/legacy",
        "tasks/Foo",
        "tasks/_x",
        "tasks/9",
        "docs/new",
        "docs/navigator",
        "docs/{{ kind }}/new",
        "docs/{{ d.doc_key }}/expand",
        "tasks/{{ t.task_id }}/status",
    ):
        assert _NON_DETAIL_HREF.search(f'href="/p/{{{{ project.name }}}}/{tail}"'), tail
    for card in (
        "tasks/{{ t.task_id }}",
        "docs/{{ d.doc_key }}",
        "docs/{{ d.doc_key }}#{{ s.anchor }}",
        "docs/{{ d.doc_key }}?raw=1",
    ):
        assert not _NON_DETAIL_HREF.search(f'href="/p/{{{{ project.name }}}}/{card}"'), card


def test_has_attr_matches_exact_name() -> None:
    assert _has_attr('<a href="/x" data-peek>', "data-peek")
    assert _has_attr('<a data-peek href="/x">', "data-peek")
    assert _has_attr('<a href="/x"\n   data-peek\n   title="t">', "data-peek")
    assert _has_attr('<a href="/x"{% if k %} data-peek{% endif %}>', "data-peek")
    assert not _has_attr('<a href="/x" data-peek-off>', "data-peek")
    assert not _has_attr('<a href="/x" data-no-peek>', "data-peek")
    assert not _has_attr('<a href="/x" xdata-peek>', "data-peek")
    assert _has_attr('<a href="/x" data-no-peek>', "data-no-peek")


def test_raw_exemption_reads_href_only() -> None:
    assert (
        _href('<a hx-get="/x?raw=1" href="/p/{{ p }}/docs/{{ k }}">') == "/p/{{ p }}/docs/{{ k }}"
    )
    assert _RAW_VIEW in _href('<a href="/p/{{ p }}/docs/{{ k }}?raw=1">')


def test_doc_detail_href_pattern() -> None:
    for href in (
        'href="/p/{{ p }}/docs/{{ d.doc_key }}"',
        'href="/p/{{ p }}/docs/{{ d.doc_key }}#{{ s.anchor }}"',
        'href="/p/{{ p }}/docs/{{ d.doc_key }}{% if a %}#{{ a }}{% endif %}"',
        'href="/p/{{ p }}/docs/{{ d.doc_key }}?raw=1"',
    ):
        assert _DOC_DETAIL_HREF.search(href), href
    assert not _DOC_DETAIL_HREF.search('href="/p/{{ p }}/docs/{{ d.doc_key }}/expand"')


def test_non_detail_pages_stay_plain_links() -> None:
    """`/tasks/legacy`, `/docs/new` и прочие страницы под ними — не карточки."""
    wrong = [
        _show(name, tag)
        for name, tag in _anchors()
        if _NON_DETAIL_HREF.search(tag) and _has_attr(tag, "data-peek")
    ]
    assert not wrong, "data-peek на ссылке не на карточку:\n" + "\n".join(wrong)


def test_python_built_links_ask_peekable() -> None:
    """Каждый `<a>` с href из Python сам решает `peekable(вид)` и ставит `data-peek`."""
    for name, (href, call) in _PYTHON_BUILT_LINKS.items():
        source = (TEMPLATES / name).read_text(encoding="utf-8")
        tags = [t for t in _ANCHOR.findall(source) if f'href="{href}"' in t]
        assert tags, f"{name}: нет ссылки {href}"
        for tag in tags:
            # Порознь, а не одной подстрокой: порядок атрибутов в теге свободный.
            assert call in tag, _show(name, tag)
            assert _has_attr(tag, "data-peek"), _show(name, tag)


def test_peekable_kinds() -> None:
    for kind in ("task", "doc", "document", "section"):
        assert peekable(kind), kind
    for kind in ("story", "adr", "url", None):
        assert not peekable(kind), kind
    assert "task" in PEEKABLE_KINDS
