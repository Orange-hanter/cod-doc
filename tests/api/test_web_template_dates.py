"""ADO-154: anti-drift — дата в шаблоне печатается только через фильтр.

История: единственный date-фильтр (``relative_time``) принимал строку и при
`datetime` молча возвращал вход, поэтому 19 мест печатали время кто как —
сырым значением, ``strftime``, ``replace("T", " ")``, срезами ``[:10]``,
``[:16]``, ``[:19]`` и вторым серверным хелпером ``_fmt_age``. На странице это
выглядело как ``2026-09-08 03:21:28.897000``.

HTML-снапшоты в этом репозитории отвергнуты осознанно
(``docs/system/capabilities/web-frontend.md`` §8: «шумит на каждой
косметической правке»), поэтому регресс ловится линтом по исходникам — как
``tests/test_tool_naming_style.py`` ловит dotted-имена тулов.

Рантайм-двойник — ``test_no_page_renders_a_raw_datetime_repr`` в
tests/api/test_web_polish.py: он ловит ту же ошибку на странице, которой тут
ещё нет.
"""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "cod_doc" / "templates" / "web"

#: Фильтры из cod_doc/api/web/dates.py. Расширять только вместе с модулем.
APPROVED_FILTERS = ("relative_time", "short_datetime", "short_date", "ts_tooltip")

#: Поля, которые несут время. Совпадение по имени атрибута, а не по типу:
#: шаблон не знает типов, и в этом вся проблема.
_DATE_ATTR_RE = re.compile(
    r"\.(?:at|ts|created|updated|last_updated|last_run|last_run_at|updated_at|"
    r"decided_at|generated_at|started_at|analyzed_at|next_fire_at|completed_at|"
    r"checked_out_at)\b"
)

_INTERPOLATION_RE = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)

#: Ручные обрезки даты — отдельный запрет: они проходят мимо фильтра и молча
#: портят значение при смене формата.
_BANNED_SUBSTRINGS = (
    "strftime",
    'replace("T"',
    "replace('T'",
    "[:10]",
    "[:16]",
    "[:19]",
    "truncate(16",
)

#: Ратчет, как ``SURFACE_DEBT`` в test_task_mutation_surface_parity.
#: Ключ — "<относительный путь>::<фрагмент выражения>", значение — причина.
#: Список может только уменьшаться.
ALLOWLIST: dict[str, str] = {
    "project/adr_show.html::adr.decided_at or ''": (
        '<input type="date"> требует сырой ISO YYYY-MM-DD, а не отформатированную '
        "для человека строку — это значение формы, не текст страницы"
    ),
}


def _templates() -> list[Path]:
    return sorted(TEMPLATES_DIR.rglob("*.html"))


def _rel(path: Path) -> str:
    return path.relative_to(TEMPLATES_DIR).as_posix()


def _allow_key(path: Path, expr: str) -> str:
    return f"{_rel(path)}::{expr.strip()}"


def test_every_date_interpolation_goes_through_a_filter() -> None:
    offenders: list[str] = []
    for path in _templates():
        for raw in _INTERPOLATION_RE.findall(path.read_text(encoding="utf-8")):
            expr = raw.strip()
            if not _DATE_ATTR_RE.search(expr):
                continue
            if any(f in expr for f in APPROVED_FILTERS):
                continue
            if _allow_key(path, expr) in ALLOWLIST:
                continue
            offenders.append(f"{_rel(path)}: {{{{ {expr} }}}}")

    assert not offenders, (
        "Дата печатается без фильтра из cod_doc/api/web/dates.py — в HTML уедет "
        "str(datetime) с микросекундами. Нарушители:\n  " + "\n  ".join(offenders)
    )


def test_no_hand_rolled_date_slicing_in_templates() -> None:
    offenders: list[str] = []
    for path in _templates():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for banned in _BANNED_SUBSTRINGS:
                if banned in line:
                    offenders.append(f"{_rel(path)}:{lineno}: {banned!r} — {line.strip()}")

    assert not offenders, "Ручное форматирование даты в шаблоне вместо фильтра:\n  " + "\n  ".join(
        offenders
    )


def test_allowlist_entries_are_still_real() -> None:
    """Ратчет не должен протухать: снятое исключение обязано исчезнуть из списка."""
    stale: list[str] = []
    for key in ALLOWLIST:
        rel, _, expr = key.partition("::")
        path = TEMPLATES_DIR / rel
        if not path.exists() or expr not in path.read_text(encoding="utf-8"):
            stale.append(key)

    assert not stale, (
        "ALLOWLIST ссылается на выражения, которых больше нет — удали записи:\n  "
        + "\n  ".join(stale)
    )


def test_the_lint_actually_matches_something() -> None:
    """Страховка от «зелёного» линта, который ничего не проверяет.

    Если регекс полей или разбор ``{{ … }}`` сломаются, оба теста выше станут
    вечнозелёными. Здесь проверяется, что в шаблонах вообще находятся
    интерполяции с датой и что они прикрыты фильтром.
    """
    matched = 0
    for path in _templates():
        for raw in _INTERPOLATION_RE.findall(path.read_text(encoding="utf-8")):
            if _DATE_ATTR_RE.search(raw):
                matched += 1

    assert matched >= 20, f"линт видит всего {matched} дат-интерполяций — регекс сломан?"
