"""ADO-180: реестр в MASTER.md описан дважды и обязан сходиться.

У документа два представления: блок со ссылкой
``📁 /path | 🗃️ doc:key | 🔑 sha:…`` и строка сводной таблицы с тем же
doc-key. `update_hashes` обновлял только первое, таблица велась руками — и
молча отставала: на момент правки 7 записей из 16 расходились, причём во всех
семи правдой была ссылка.

Инвариант тут не «числа равны». Две строки таблицы (RFC 23, RFC 24) блока со
ссылкой не имеют вовсе, и это нормально. Проверяемое утверждение: **там, где
оба представления есть, они согласны**, и счётчик «Всего» равен числу строк.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MASTER = REPO_ROOT / "MASTER.md"

#: Блок со ссылкой: путь, doc-key и хэш в одной строке.
_LINK = re.compile(r"📁 (?P<path>\S+) \| 🗃️ (?P<key>doc:[\w-]+) \| 🔑 sha:(?P<hash>[0-9a-f]{12})")

#: Строка сводной таблицы: номер, название, doc-key, хэш, дата, статус.
_ROW = re.compile(
    r"^\|\s*(?P<num>\d+)\s*\|[^|]*\|\s*`(?P<key>doc:[\w-]+)`\s*\|\s*`(?P<hash>[0-9a-f]{12})`", re.M
)

#: Любая строка таблицы, включая запись №1 без хэша.
_ANY_ROW = re.compile(r"^\|\s*(\d+)\s*\|", re.M)

_TOTAL = re.compile(r"\*\*Всего:\*\* (?P<n>\d+) документов")


def _text() -> str:
    return MASTER.read_text(encoding="utf-8")


def test_hashes_agree_between_link_and_table() -> None:
    """Ядро задачи: два представления одного документа не расходятся."""
    text = _text()
    links = {m.group("key"): m.group("hash") for m in _LINK.finditer(text)}
    rows = {m.group("key"): m.group("hash") for m in _ROW.finditer(text)}

    mismatched = {
        key: (links[key], rows[key])
        for key in links.keys() & rows.keys()
        if links[key] != rows[key]
    }
    assert not mismatched, (
        "хэш в блоке со ссылкой не совпадает с хэшем в таблице.\n"
        "Пересчитать: cod-doc hash update\n"
        + "\n".join(f"  {k}: ссылка {a} != таблица {b}" for k, (a, b) in sorted(mismatched.items()))
    )


def test_table_numbering_is_contiguous() -> None:
    """Нумерация строк — сплошная: пропуск означает потерянную запись.

    Считаем ВСЕ строки таблицы, а не только с хэшем: у записи №1 (сам
    MASTER.md) в колонке хэша стоит `regen-on-write` — файл ссылается на себя,
    и его хэш менялся бы от собственной записи.
    """
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

    Согласованность теперь утверждают тесты выше, а не строка в файле,
    которую надо помнить обновить (ADO-180).
    """
    assert "VALID**" not in _text(), (
        "ручной счётчик «N/N VALID» вернулся в MASTER.md; "
        "согласованность проверяется тестами, а не текстом"
    )


def test_every_link_target_is_declared_with_a_path() -> None:
    """У каждого блока есть путь — без него `update_hashes` нечего считать."""
    for match in _LINK.finditer(_text()):
        assert match.group("path").startswith("/"), (
            f"{match.group('key')}: путь '{match.group('path')}' не абсолютный от корня репо"
        )
