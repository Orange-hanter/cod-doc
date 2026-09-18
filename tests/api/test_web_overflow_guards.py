"""ADO-121 / ADO-122 / ADO-153 / ADO-126: контейнер обязан ограничивать содержимое.

Четыре дефекта одного класса — вёрстка вылезает за свой контейнер:

* карточки kanban шире колонки (`min-width:auto` у grid/flex-элемента),
* колонка Diff в `/revisions` уезжает за экран (`max-width` на `<td>` в
  таблице с `border-collapse` браузером игнорируется),
* строка «Recent revisions» разъезжается при переносе (`flex-wrap` плюс
  `margin-left:auto` у метки времени),
* hover-превью на code-refs уходит за правый край экрана.

Проверить это отрендеренным HTML нельзя: браузерная раскладка в тестах не
считается. Поэтому здесь два уровня — разметка (обёртки и число ячеек,
на которые опирается сетка) и наличие самих ограничивающих CSS-правил.
Последнее — не «тест на реализацию», а единственный способ поймать регресс:
правило `min-width: 0` невидимо для любого ассерта по HTML, а без него
дефект возвращается целиком.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "cod_doc"
CSS = (ROOT / "static" / "css" / "_components.css").read_text(encoding="utf-8")
TEMPLATES = ROOT / "templates" / "web"


def _rule(selector: str) -> str:
    """Тело правила для точного селектора (первое вхождение)."""
    m = re.search(
        rf"(?:^|\n)\s*{re.escape(selector)}\s*\{{(.*?)\}}",
        CSS,
        re.DOTALL,
    )
    assert m is not None, f"правило {selector} исчезло из _components.css"
    return m.group(1)


# ── ADO-121: kanban ─────────────────────────────────────────────────────


def test_kanban_column_can_shrink() -> None:
    """Без `min-width: 0` grid-трек не сжимается ниже min-content содержимого."""
    assert "min-width: 0" in _rule(".kanban-col")


def test_kanban_card_can_shrink_and_clips() -> None:
    body = _rule(".kanban-card")
    assert "min-width: 0" in body
    assert "overflow: hidden" in body


def test_kanban_card_meta_does_not_dictate_width() -> None:
    body = _rule(".kanban-card-meta")
    assert "min-width: 0" in body
    assert "overflow: hidden" in body


def test_kanban_card_title_breaks_long_tokens() -> None:
    """`pilot-hardening-2026-09` иначе не переносится и распирает карточку."""
    assert "overflow-wrap: anywhere" in _rule(".kanban-card-title")


def test_kanban_hover_does_not_move_the_card() -> None:
    """`translateX` выталкивал карточку за бордер колонки."""
    assert "transform" not in _rule(".kanban-card:hover")


# ── ADO-122: широкие таблицы ────────────────────────────────────────────


def test_clamp_helper_is_block_level() -> None:
    """Обрезка работает только на блочном элементе внутри ячейки, не на `<td>`."""
    body = _rule(".cell-clamp")
    assert "display: block" in body
    assert "text-overflow: ellipsis" in body


def test_revisions_table_is_wrapped_in_a_scroll_container() -> None:
    html = (TEMPLATES / "project" / "revisions.html").read_text(encoding="utf-8")
    assert 'class="table-scroll"' in html
    assert "overflow-x: auto" in _rule(".table-scroll")


def test_wide_revision_cells_are_clamped() -> None:
    html = (TEMPLATES / "project" / "revisions.html").read_text(encoding="utf-8")
    assert 'class="cell-clamp-prose"' in html, "колонка Reason не обрезается"
    assert 'class="cell-clamp"' in html, "колонка Diff не обрезается"
    # Полное значение остаётся доступным по наведению.
    assert 'title="{{ r.reason }}"' in html
    assert 'title="{{ r.diff_preview }}"' in html


def test_task_history_diff_cell_is_clamped() -> None:
    html = (TEMPLATES / "project" / "task_show.html").read_text(encoding="utf-8")
    assert 'class="cell-clamp"' in html


# ── ADO-153: строка recent revisions ────────────────────────────────────


def test_rev_list_row_is_a_grid() -> None:
    body = _rule(".rev-list li")
    assert "display: grid" in body
    assert "flex-wrap" not in body, "flex-wrap и был причиной разъезжающейся строки"


def test_rev_list_row_always_has_three_cells() -> None:
    """Сетка из трёх колонок ломается, если ячеек то три, то четыре.

    До ADO-153 `reason` рендерился условным `{% if %}` вокруг всего span —
    строки без причины имели другой набор детей, и время уезжало в чужую
    колонку. Условным теперь может быть только атрибут `title`.
    """
    html = (TEMPLATES / "project" / "show.html").read_text(encoding="utf-8")
    start = html.index('<ul class="rev-list">')
    block = html[start : html.index("</ul>", start)]
    row_start = block.index("<li>")
    row = block[row_start : block.index("</li>", row_start)]

    for cell in ('class="rev-who"', "rev-reason", 'class="muted ts"'):
        assert cell in row, f"ячейка {cell} исчезла из строки"

    without_title_guard = row.replace('{% if r.reason %}title="{{ r.reason }}"{% endif %}', "")
    assert "{% if" not in without_title_guard, (
        "ячейка строки снова стала условной — сетка разъедется на строках без причины"
    )


# ── ADO-126: hover-превью code-refs ─────────────────────────────────────


def test_code_ref_preview_is_clamped_to_viewport() -> None:
    html = (TEMPLATES / "project" / "code_refs_list.html").read_text(encoding="utf-8")
    assert "window.innerWidth" in html, "позиция превью не проверяется против ширины экрана"
    assert "window.innerHeight" in html
    assert "max-width: min(600px, calc(100vw - 20px))" in html


def test_code_ref_preview_uses_real_tokens() -> None:
    """`--bg-card` и `--text-muted` в проекте не объявлены — превью было без фона."""
    html = (TEMPLATES / "project" / "code_refs_list.html").read_text(encoding="utf-8")
    assert "--bg-card" not in html
    assert "--text-muted" not in html


def test_no_template_uses_undeclared_css_variables() -> None:
    """Общий гейт: переменная из inline-стиля обязана существовать в токенах.

    `--bg-card` молча давал прозрачный фон — такую опечатку не видно ни в
    одном тесте по HTML и не ловит линтер.
    """
    base = (ROOT / "static" / "css" / "_base.css").read_text(encoding="utf-8")
    declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", base + CSS))
    used: set[str] = set()
    for tpl in TEMPLATES.rglob("*.html"):
        used |= set(re.findall(r"var\((--[a-z0-9-]+)", tpl.read_text(encoding="utf-8")))

    unknown = sorted(used - declared)
    assert not unknown, f"шаблоны ссылаются на необъявленные токены: {unknown}"


def test_story_line_text_can_shrink_and_break() -> None:
    """ADO-144: колонка разобранного нарратива обязана ужиматься И переносить.

    `.story-line` — сетка `60px 1fr`, а трек `1fr` по умолчанию
    `min-width: auto`: он не сужается ниже самого длинного неразрывного
    слова. Такие слова в нарративах штатные — например
    `(backlog/todo/in_progress/in_review/blocked/done|cancelled)` из
    US-012: ни пробела, ни дефиса, ни одной точки переноса.

    Дефект появился ровно в тот момент, когда русские нарративы начали
    разбираться: до этого текст лежал широким абзацем
    `.story-narrative-raw` и переносился сам, а в узкой колонке вылез на
    73px за карточку и дал горизонтальную полосу всей странице.

    Нужны обе декларации. `overflow-wrap` без `min-width: 0` не помогает
    (трек уже растянут под слово, переносить нечего), `min-width: 0` без
    `overflow-wrap` — тоже (трек ужался, слово торчит наружу).
    """
    body = _rule(".story-line-text")
    assert "min-width: 0" in body, "трек 1fr не сможет ужаться ниже длинного слова"
    assert "overflow-wrap: anywhere" in body, "длинное слово не получит точку переноса"
