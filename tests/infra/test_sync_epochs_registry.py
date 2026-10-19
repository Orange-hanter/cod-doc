"""CLO-032: гейт реестра эпох ``cod_doc/infra/sync_epochs.py`` (RFC 29 §3.6, Q-012).

Эпоху открывает миграция, меняющая уезжающие колонки (``SHIPPED_COLUMNS``):
операции старой схемы на новой не применяются. Решение «открывает / не
открывает» записано в ``MIGRATIONS`` руками; гейт сверяет его с фактом.

Факт — по настоящей схеме, а не по тексту миграции: ``0050`` добавляет
``rank`` сырым ``ALTER TABLE`` через ``op.execute``, и разбор вызовов
``op.add_column`` его бы не увидел. Цепочка прогоняется по шагу от
``SYNC_BASELINE`` на временной SQLite, после каждого шага снимаются колонки и
их типы у таблиц, которые что-то отправляют. Изменение считается уезжающим,
если добавлена или сменила тип колонка из ``SHIPPED_COLUMNS``, появилась или
исчезла такая таблица, или колонка исчезла вовсе (fail closed: исчезнувшей
колонки в реестре уже нет, и local она была или authored — не узнать).

Красное: ревизия после базы без записи в реестре; запись не в порядке
цепочки; «не открывает» при уезжающем изменении; «открывает» без него.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import create_engine, inspect

from cod_doc.infra import sync_epochs
from cod_doc.infra import sync_registry as reg
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

Shape = dict[str, dict[str, str]]


def _chain_after_baseline() -> list[str]:
    order = sync_epochs.revision_order()
    base = order[sync_epochs.SYNC_BASELINE]
    return sorted((rev for rev, i in order.items() if i > base), key=lambda rev: order[rev])


def _shape(url: str) -> Shape:
    engine = create_engine(url)
    try:
        insp = inspect(engine)
        present = set(insp.get_table_names())
        return {
            table: {col["name"]: str(col["type"]) for col in insp.get_columns(table)}
            for table in reg.SHIPPED_COLUMNS
            if table in present
        }
    finally:
        engine.dispose()


def shipped_changes(before: Shape, after: Shape) -> list[str]:
    """Уезжающие изменения схемы между двумя снимками (см. докстринг модуля)."""
    changes: list[str] = []
    for table in sorted(before.keys() | after.keys()):
        shipped = reg.SHIPPED_COLUMNS.get(table, frozenset())
        old, new = before.get(table), after.get(table)
        if old is None or new is None:
            changes.append(f"{table}: таблица {'появилась' if old is None else 'исчезла'}")
            continue
        changes += [f"{table}.{c}: колонка исчезла" for c in sorted(old.keys() - new.keys())]
        changes += [
            f"{table}.{c}: колонка добавлена"
            for c in sorted(new.keys() - old.keys())
            if c in shipped
        ]
        changes += [
            f"{table}.{c}: тип {old[c]} → {new[c]}"
            for c in sorted(old.keys() & new.keys())
            if c in shipped and old[c] != new[c]
        ]
    return changes


def mismatches(classification: Mapping[str, bool], facts: Mapping[str, list[str]]) -> list[str]:
    out: list[str] = []
    for rev, changes in facts.items():
        if rev not in classification:
            out.append(f"{rev}: нет в sync_epochs.MIGRATIONS — реши, открывает ли она эпоху")
        elif classification[rev] and not changes:
            out.append(f"{rev}: записана открывающей эпоху, но уезжающие колонки не меняет")
        elif not classification[rev] and changes:
            out.append(
                f"{rev}: меняет уезжающие колонки ({'; '.join(changes)}), а эпоху не открывает"
            )
    return out


@pytest.fixture(scope="module")
def facts(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    path: Path = tmp_path_factory.mktemp("epochs") / "chain.db"
    url = f"sqlite:///{path}"
    run_alembic("upgrade", sync_epochs.SYNC_BASELINE, db_url=url)
    before = _shape(url)
    out: dict[str, list[str]] = {}
    for rev in _chain_after_baseline():
        run_alembic("upgrade", rev, db_url=url)
        after = _shape(url)
        out[rev] = shipped_changes(before, after)
        before = after
    return out


def test_registry_lists_every_revision_after_the_baseline_in_chain_order() -> None:
    assert list(sync_epochs.MIGRATIONS) == _chain_after_baseline()


def test_classification_matches_the_schema(facts: dict[str, list[str]]) -> None:
    problems = mismatches(sync_epochs.MIGRATIONS, facts)
    assert not problems, "реестр эпох расходится со схемой:\n" + "\n".join(problems)


def test_gate_sees_the_rank_migration(facts: dict[str, list[str]]) -> None:
    """Гейт не слеп: 0050 добавляет rank сырым SQL — и это уезжающее изменение."""
    assert any(".rank: колонка добавлена" in c for c in facts["0050_sync_rank"])
    assert any("document.node_rank" in c for c in facts["0050_sync_rank"])


def test_gate_catches_a_wrong_or_missing_decision(facts: dict[str, list[str]]) -> None:
    wrong = {**sync_epochs.MIGRATIONS, "0050_sync_rank": False, "0051_sync_conflict": True}
    missing = {
        k: v for k, v in sync_epochs.MIGRATIONS.items() if k != "0052_sync_conflict_opened_hlc"
    }
    assert len(mismatches(wrong, facts)) == 2
    assert mismatches(missing, facts) == [
        "0052_sync_conflict_opened_hlc: нет в sync_epochs.MIGRATIONS — реши, открывает ли она эпоху"
    ]


def test_dropped_column_counts_as_shipped_change() -> None:
    before: Shape = {"task": {"row_id": "INTEGER", "legacy": "TEXT"}}
    assert shipped_changes(before, {"task": {"row_id": "INTEGER"}}) == [
        "task.legacy: колонка исчезла"
    ]


# --- вывод эпохи --------------------------------------------------------------------


def test_epoch_of_a_schema() -> None:
    head = sync_epochs.code_head()
    assert sync_epochs.epoch_of(head) == "0050_sync_rank"
    assert sync_epochs.epoch_of("0050_sync_rank") == "0050_sync_rank"
    assert sync_epochs.epoch_of(sync_epochs.SYNC_BASELINE) is None
    with pytest.raises(sync_epochs.UnknownRevisionError):
        sync_epochs.epoch_of("9999_unknown")


def test_unknown_epoch_is_newer_and_own_unknown_epoch_is_refused() -> None:
    assert sync_epochs.epoch_is_newer("0099_future", "0050_sync_rank")
    assert not sync_epochs.epoch_is_newer("0050_sync_rank", "0050_sync_rank")
    with pytest.raises(sync_epochs.UnknownRevisionError):
        sync_epochs.epoch_is_newer("0050_sync_rank", "0099_future")


def test_epochs_opened_between() -> None:
    head = sync_epochs.code_head()
    assert sync_epochs.epochs_opened_between(sync_epochs.SYNC_BASELINE, head) == ["0050_sync_rank"]
    assert sync_epochs.epochs_opened_between("0050_sync_rank", head) == []
    assert sync_epochs.epochs_opened_between(None, head) == []
    assert sync_epochs.epochs_opened_between("9999_newer_than_code", head) == []
