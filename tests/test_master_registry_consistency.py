"""ADO-180: реестр в MASTER.md описан дважды и обязан сходиться.

У документа два представления: блок со ссылкой ``📁 /path | 🗃️ doc:key``
и строка сводной таблицы с тем же doc-key.

DEBT-001: хэшей в реестре cod-doc больше нет. Хранимый ``🔑 sha:`` дублировал
дрейф БД для документов, что в ней живут, и его приходилось переписывать при
каждой их правке — 40 из 78 коммитов в MASTER.md с июня меняли только хэши.
Устаревание ловит ``cod-doc doc drift``; здесь проверяется то, что дрейф не
видит: ссылка ведёт на существующий файл и описана в таблице.

Инвариант не «числа равны». Две строки таблицы (RFC 23, RFC 24) блока со
ссылкой не имеют вовсе, и это нормально.
"""

from __future__ import annotations

import re
from pathlib import Path

from cod_doc.core.hash_calc import LINK_PATTERN, is_ignored_by_git

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER = REPO_ROOT / "MASTER.md"

#: Строка сводной таблицы: номер, название, doc-key, дата, статус.
_ROW = re.compile(r"^\|\s*(?P<num>\d+)\s*\|[^|]*\|\s*`(?P<key>doc:[\w-]+)`\s*\|", re.M)

#: Любая строка таблицы.
_ANY_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.M)

_TOTAL = re.compile(r"\*\*Всего:\*\* (?P<n>\d+) документов")


def _text() -> str:
    return MASTER.read_text(encoding="utf-8")


def test_every_link_is_listed_in_the_table() -> None:
    """Ядро ADO-180: у блока со ссылкой есть строка в таблице."""
    text = _text()
    links = {m.group("vec_id") for m in LINK_PATTERN.finditer(text)}
    rows = {m.group("key") for m in _ROW.finditer(text)}

    assert links, "в MASTER.md не нашлось ни одной гибридной ссылки"
    missing = sorted(links - rows)
    assert not missing, f"ссылки без строки в таблице §5.1: {missing}"


def test_registry_stores_no_hashes() -> None:
    """DEBT-001: хэш в ссылке вернул бы ручной пересчёт при каждой правке."""
    hashed = [m.group(0) for m in LINK_PATTERN.finditer(_text()) if m.group("hash")]
    assert not hashed, (
        "в реестре cod-doc снова хранится 🔑 sha: — устаревание ловит "
        f"`cod-doc doc drift`, хэш не нужен: {hashed}"
    )


def test_every_link_target_exists() -> None:
    """Ссылка ведёт на файл. Отсутствие под `.gitignore` — штатно (ADO-174)."""
    broken = [
        rel
        for m in LINK_PATTERN.finditer(_text())
        if not (REPO_ROOT / (rel := m.group("path").lstrip("/"))).exists()
        and not is_ignored_by_git(rel, REPO_ROOT)
    ]
    assert not broken, f"битые ссылки в MASTER.md: {broken}"


def test_table_numbering_is_contiguous() -> None:
    """Нумерация строк — сплошная: пропуск означает потерянную запись."""
    numbers = [int(m.group(1)) for m in _ANY_ROW.finditer(_text())]
    assert numbers == list(range(1, len(numbers) + 1)), f"нумерация разъехалась: {numbers}"


def test_total_counter_matches_the_table() -> None:
    """Счётчик «Всего» считает строки таблицы, а не что-то своё."""
    text = _text()
    total = _TOTAL.search(text)
    assert total is not None, "строка «**Всего:** N документов» пропала"

    all_rows = _ANY_ROW.findall(text)
    assert int(total.group("n")) == len(all_rows), (
        f"счётчик говорит {total.group('n')}, строк в таблице {len(all_rows)}"
    )


def test_no_handwritten_valid_counter_returns() -> None:
    """«N/N VALID» вёлся руками и не сходился ни с чем — не возвращаем.

    Согласованность утверждают тесты выше, а не строка в файле, которую надо
    помнить обновить (ADO-180).
    """
    assert "VALID**" not in _text(), (
        "ручной счётчик «N/N VALID» вернулся в MASTER.md; "
        "согласованность проверяется тестами, а не текстом"
    )


def test_every_link_target_is_declared_with_a_path() -> None:
    """У каждого блока путь абсолютный от корня репо."""
    for match in LINK_PATTERN.finditer(_text()):
        assert match.group("path").startswith("/"), (
            f"{match.group('vec_id')}: путь '{match.group('path')}' не абсолютный от корня репо"
        )
