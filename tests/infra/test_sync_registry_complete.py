"""CLO-001: гейт полноты реестра классов синхронизации (RFC 29 §3.2, §10.2).

Новая таблица не может молча выпасть из синхронизации: модель без записи в
``REGISTRY`` роняет гейт. Целочисленная ``*_id``-колонка authored-таблицы без
настоящего FK должна быть объявлена в ``logical_fks`` (или в ``POLYMORPHIC_FKS``),
иначе захват сериализовал бы локальный ``row_id``, бессмысленный на другой
реплике.

Проверки написаны функциями от ``(tables, registry)``, чтобы их зубы были видны
на синтетической схеме, а не только на зелёной реальной.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import Column, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint

import cod_doc.infra.models as models_pkg
from cod_doc.domain.entities import EntityKind, Sensitivity
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models.base import Base
from cod_doc.infra.sync_registry import (
    DOC_REFS,
    ENTITY_KIND_TABLES,
    POLYMORPHIC_FKS,
    REGISTRY,
    KeyKind,
    SyncClass,
    TableSpec,
    authored_tables,
    human_ref_target,
    parse_sensitivity,
    row_class,
    sensitivity_key,
    spec_for,
    withheld_columns,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

# UNIQUE на authored-таблице, который не натуральный ключ, не ребро и не
# позиция: значения глобальны по построению либо не сливаются (RFC 29 §3.3).
_UNIQUE_EXEMPT: dict[tuple[str, tuple[str, ...]], str] = {
    ("project", ("slug",)): "проект адресуется project.uid; слаг не сливается (§3.3)",
    ("approval", ("approval_id",)): "uuid4 — глобален по построению",
    ("finding", ("finding_uid",)): "uuid — глобален по построению",
}

_ORDER_PLACES = 11  # RFC 29 §3.2, Q-009: число из RFC — контракт CLO-003, не из схемы
_POSITION_COLUMNS = frozenset({"position", "node_position"})


def _all_model_tables() -> Mapping[str, Table]:
    """Каждый модуль пакета моделей, а не только реэкспорт ``__init__``.

    ``link_suggestion`` в ``__init__`` не импортирована и в ``Base.metadata``
    попадает лениво; без обхода пакета гейт её бы не увидел.
    """
    for mod in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"{models_pkg.__name__}.{mod.name}")
    return Base.metadata.tables


def _is_row_ref(column: Column[object]) -> bool:
    return (
        isinstance(column.type, Integer)
        and column.name.endswith("_id")
        and not column.primary_key
        and not column.foreign_keys
    )


def missing_entries(tables: Mapping[str, Table], registry: Mapping[str, TableSpec]) -> list[str]:
    problems = [
        f"{name}: нет записи в sync_registry" for name in sorted(set(tables) - set(registry))
    ]
    problems += [
        f"{name}: запись есть, таблицы нет" for name in sorted(set(registry) - set(tables))
    ]
    return problems


def _shipped(spec: TableSpec) -> bool:
    """Строки таблицы уезжают с uid-ссылками: та же развилка, что у ``column_class``.

    Набор классов берётся из реестра (``_SHIPPED_CLASSES``), а не дублируется.
    По ``SHIPPED_COLUMNS`` не выводится намеренно: забытая там запись иначе
    выключила бы для таблицы и этот гейт — две ошибки погасили бы друг друга.
    """
    return spec.cls in reg._SHIPPED_CLASSES or bool(spec.authored_columns)


def uncovered_fks(
    tables: Mapping[str, Table],
    registry: Mapping[str, TableSpec],
    polymorphic: Mapping[tuple[str, str], str] = POLYMORPHIC_FKS,
) -> list[str]:
    problems: list[str] = []
    for name, table in sorted(tables.items()):
        spec = registry.get(name)
        if spec is None or not _shipped(spec):
            continue
        for column in table.columns:
            if not _is_row_ref(column):
                continue
            if column.name in spec.logical_fks or (name, column.name) in polymorphic:
                continue
            problems.append(f"{name}.{column.name}: FK без ограничения не покрыт logical_fks")
        problems += [
            f"{name}.{col}: logical_fks → неизвестная таблица {target!r}"
            for col, target in spec.logical_fks.items()
            if target not in tables
        ]
    return problems


def _declared_columns(spec: TableSpec) -> set[str]:
    cols = set(spec.local_columns) | set(spec.authored_columns) | set(spec.text_columns)
    cols |= set(spec.logical_fks) | set(spec.human_id_refs)
    for key in spec.natural_keys:
        cols |= set(key.columns)
    cols |= set(spec.deterministic_uid or ())
    return cols


# --- гейт на реальной схеме --------------------------------------------------


def test_every_model_table_has_entry() -> None:
    assert missing_entries(_all_model_tables(), REGISTRY) == []


def test_every_unconstrained_fk_is_covered() -> None:
    assert uncovered_fks(_all_model_tables(), REGISTRY) == []


def test_declared_columns_exist() -> None:
    """Опечатка или переименование колонки не проходит молча.

    ``rank_column`` не сверяется: колонки ``rank`` появятся в CLO-003.
    """
    tables = _all_model_tables()
    problems = [
        f"{spec.table}.{col}"
        for spec in REGISTRY.values()
        for col in sorted(_declared_columns(spec))
        if col not in tables[spec.table].columns
    ]
    for (table, col), disc in POLYMORPHIC_FKS.items():
        problems += [f"{table}.{c}" for c in (col, disc) if c not in tables[table].columns]
    assert problems == []


def test_shipped_references_point_to_authored_tables() -> None:
    """Ссылка из authored-строки сериализуется через uid, а uid есть только у authored."""
    tables = _all_model_tables()
    problems = []
    for spec in authored_tables():
        targets = {fk.column.table.name for fk in tables[spec.table].foreign_keys}
        targets |= set(spec.logical_fks.values())
        problems += [
            f"{spec.table} → {t} ({spec_for(t).cls})"
            for t in sorted(targets)
            if spec_for(t).cls is not SyncClass.AUTHORED
        ]
    assert problems == []


def test_authored_uniques_are_declared() -> None:
    """Каждый UNIQUE authored-таблицы — натуральный ключ, ребро, позиция или исключение.

    Иначе одно значение, заведённое на двух репликах, упало бы на вставке при
    pull, а склейка (Q-007) о нём бы не знала.
    """
    tables = _all_model_tables()
    problems = []
    for spec in authored_tables():
        keys = {frozenset(k.columns) for k in spec.natural_keys}
        if spec.deterministic_uid:
            keys.add(frozenset(spec.deterministic_uid))
        for constraint in tables[spec.table].constraints:
            if not isinstance(constraint, UniqueConstraint):
                continue
            cols = tuple(c.name for c in constraint.columns)
            if (
                frozenset(cols) in keys
                or set(cols) & spec.local_columns
                or (spec.table, cols) in _UNIQUE_EXEMPT
            ):
                continue
            problems.append(f"{spec.table}{cols}")
    assert problems == []


def test_unique_exemptions_still_exist() -> None:
    """Исключение из сверки UNIQUE без самого ограничения — мёртвая запись."""
    tables = _all_model_tables()
    stale = [
        (table, cols)
        for table, cols in _UNIQUE_EXEMPT
        if not any(
            isinstance(c, UniqueConstraint) and tuple(col.name for col in c.columns) == cols
            for c in tables[table].constraints
        )
    ]
    assert stale == []


def test_registry_classifies_tables_per_rfc() -> None:
    by_cls: dict[SyncClass, set[str]] = {c: set() for c in SyncClass}
    for spec in REGISTRY.values():
        by_cls[spec.cls].add(spec.table)
    assert by_cls[SyncClass.JOURNAL] == {"revision", "activity_event"}
    assert by_cls[SyncClass.LOCAL] == {
        "routine_run",
        "agent_run",
        "trace_call",
        "task_metrics",
        "finding_source_run",
        # служебные таблицы синхронизации (0049, CLO-002): состояние реплики
        "replica",
        "sync_op",
        "changeset",
        "sync_ref",
        "sync_lease",
        "entity_alias",
    }
    assert {"link", "link_suggestion", "doc_node_suggestion", "doc_code_claim"} <= by_cls[
        SyncClass.DERIVED
    ]
    assert all(
        t in by_cls[SyncClass.DERIVED]
        for t in REGISTRY
        if t.startswith(("code_", "repo_"))
        or (t.startswith("structure_") and t != "structure_waiver")
    )
    assert "structure_waiver" in by_cls[SyncClass.AUTHORED]


def test_order_places_have_rank_and_local_position() -> None:
    """У каждого места порядка ровно одна local-колонка позиции, и она есть в схеме."""
    tables = _all_model_tables()
    ordered = [spec for spec in REGISTRY.values() if spec.rank_column]
    assert len(ordered) == _ORDER_PLACES, sorted(s.table for s in ordered)
    for spec in ordered:
        positions = spec.local_columns & _POSITION_COLUMNS
        assert len(positions) == 1, (spec.table, positions)
        (position,) = positions
        assert position in tables[spec.table].columns, (spec.table, position)


def test_document_working_tree_hashes_are_local() -> None:
    assert {"projection_hash", "content_sha256_head"} <= spec_for("document").local_columns
    assert {"resolved", "broken_reason", "last_checked"} <= spec_for(
        "open_question_link"
    ).local_columns


def test_natural_key_kinds() -> None:
    assert spec_for("plan_section").natural_keys[0].kind is KeyKind.LABEL
    for table in ("task", "adr", "user_story", "scenario", "open_question"):
        assert spec_for(table).natural_keys[0].kind is KeyKind.LEASED, table
    for table in ("document", "section", "doc_node", "routine", "plan", "tag", "adr_topic"):
        assert spec_for(table).natural_keys[0].kind is KeyKind.NAME, table
    assert spec_for("project").natural_keys == ()


# --- построчные исключения ---------------------------------------------------


_DOC_OWNED = ("section", "doc_comment", "document_tag")


def _doc_row(table: str, label: object, *, key: bool = True) -> dict[str, object]:
    """Строка таблицы, принадлежащей документу с меткой ``label``."""
    if table == "document":
        row: dict[str, object] = {"path": "docs/a.md"}
        if key:
            row["sensitivity"] = label
        return row
    row = {"document_id": 1, "section_id": None} if table == "doc_comment" else {"document_id": 1}
    if key:
        row[sensitivity_key("document_id")] = label
    return row


@pytest.mark.parametrize("table", ["document", *_DOC_OWNED])
def test_restricted_rows_are_local(table: str) -> None:
    assert row_class(table, _doc_row(table, "restricted")) is SyncClass.LOCAL
    for label in ("confidential", "internal", "public", Sensitivity.INTERNAL):
        assert row_class(table, _doc_row(table, label)) is SyncClass.AUTHORED, label


# --- fail-closed: неизвестное не уезжает (security review CLO-001) -----------


@pytest.mark.parametrize("table", ["document", *_DOC_OWNED])
@pytest.mark.parametrize(
    "label",
    [
        None,
        "secret",
        "RESTRICTED",
        "Restricted",
        "Internal",
        "INTERNAL",
        " internal",
        "internal ",
        "internal\n",
        "",
        b"internal",
        1,
    ],
)
def test_noncanonical_sensitivity_is_local(table: str, label: object) -> None:
    """Метка, которую ``Sensitivity(value)`` не разобрал бы, — local (parser differential)."""
    assert parse_sensitivity(label) is None
    assert row_class(table, _doc_row(table, label)) is SyncClass.LOCAL


@pytest.mark.parametrize("table", ["document", *_DOC_OWNED])
def test_missing_sensitivity_is_local(table: str) -> None:
    """Секция без разрешённого владельца — local."""
    assert row_class(table, _doc_row(table, None, key=False)) is SyncClass.LOCAL


def test_parse_sensitivity_matches_repository_read_path() -> None:
    """Гейт и ``document_repo`` разбирают метку одним конструктором."""
    for member in Sensitivity:
        assert parse_sensitivity(member.value) is member
        assert parse_sensitivity(member) is member


def _doc_ref_row(column: str, label: object, **extra: object) -> dict[str, object]:
    return {column: 1, sensitivity_key(column): label, **extra}


@pytest.mark.parametrize(
    ("table", "row"),
    [
        ("revision", _doc_ref_row("entity_id", "restricted", entity_kind="document")),
        ("revision", _doc_ref_row("entity_id", "Internal", entity_kind="section")),
        ("revision", {"entity_kind": "section", "entity_id": 1}),
        ("revision", {"entity_kind": "Document", "entity_id": 1}),
        ("revision", {"entity_kind": " task", "entity_id": 1}),
        ("revision", {}),
        ("activity_event", {"kind": "doc.updated", "scope_kind": "document", "scope_id": "d"}),
        ("activity_event", {"kind": "section.patched", "scope_kind": "section", "scope_id": "s"}),
        ("activity_event", {"kind": "comment.added", "scope_kind": "comment", "scope_id": "1"}),
        (
            "activity_event",
            _doc_ref_row("scope_id", "restricted", kind="doc.x", scope_kind="doc"),
        ),
        ("activity_event", {"kind": "task.created", "scope_kind": "Task", "scope_id": "T"}),
        ("activity_event", {"kind": "task.created", "scope_kind": None}),
        ("activity_event", {"kind": "run.started", "scope_kind": "run", "scope_id": "r"}),
        ("activity_event", {}),
        ("finding", {}),
        ("finding", {"source": None}),
        ("finding", {"source": " routine"}),
        ("finding", {"source": "Routine"}),
        ("finding", {"source": ""}),
        ("link_suggestion", {}),
        ("link_suggestion", {"state": "weird"}),
        ("link_suggestion", {"state": "Accepted"}),
        ("link_suggestion", {"state": " accepted"}),
    ],
)
def test_unknown_or_restricted_journal_and_row_values_do_not_ship(
    table: str, row: dict[str, object]
) -> None:
    """Ревизия restricted-документа несёт его diff — не уезжает; пустая строка тоже."""
    expected = SyncClass.DERIVED if table == "finding" else SyncClass.LOCAL
    assert row_class(table, row) is expected


def test_cleared_journal_rows_ship() -> None:
    assert (
        row_class("revision", _doc_ref_row("entity_id", "internal", entity_kind="document"))
        is SyncClass.JOURNAL
    )
    assert row_class("revision", {"entity_kind": "task", "entity_id": 1}) is SyncClass.JOURNAL
    assert (
        row_class("activity_event", {"kind": "task.created", "scope_kind": "task", "scope_id": "T"})
        is SyncClass.JOURNAL
    )
    assert (
        row_class(
            "activity_event", _doc_ref_row("scope_id", "public", kind="doc.x", scope_kind="doc")
        )
        is SyncClass.JOURNAL
    )


# --- все пути к restricted-документу (sibling-path parity) -------------------

_DOC_TABLES = frozenset({"document", "section"})


def _doc_reference_columns(tables: Mapping[str, Table]) -> set[tuple[str, str]]:
    """Колонки уезжающих таблиц, которые ссылаются на документ или секцию.

    FK и ``logical_fks`` на ``document``/``section``, колонки ``*doc_key``,
    полиморфные ссылки, которые могут указывать на документ. Сам документ не
    считается: его метка — его собственная колонка.
    """
    found: set[tuple[str, str]] = set()
    for name, table in tables.items():
        spec = REGISTRY.get(name)
        if spec is None:
            # без записи класс неизвестен: в выдачу как непокрытая ссылка, с
            # тем же диагнозом, что у missing_entries, а не голым KeyError
            found.add((name, "<нет записи в sync_registry>"))
            continue
        if not _shipped(spec) or name == "document":
            continue
        for column in table.columns:
            targets = {fk.column.table.name for fk in column.foreign_keys}
            if column.name in spec.logical_fks:
                targets.add(spec.logical_fks[column.name])
            if targets & _DOC_TABLES or column.name.endswith("doc_key"):
                found.add((name, column.name))
        if "to_ref" in table.columns and "to_kind" in table.columns:
            found.add((name, "to_ref"))
    found |= {key for key in POLYMORPHIC_FKS if key[0] in tables}
    found.add(("activity_event", "scope_id"))
    found.add(("approval_doc_revision_link", "revision_id"))
    return found


def test_every_document_reference_is_gated() -> None:
    """Каждая ссылка на документ/секцию из уезжающей таблицы объявлена в DOC_REFS."""
    declared = {(t, ref.column) for t, refs in DOC_REFS.items() for ref in refs}
    missing = sorted(_doc_reference_columns(_all_model_tables()) - declared)
    assert missing == []


def test_doc_refs_cover_real_columns() -> None:
    tables = _all_model_tables()
    for table, refs in DOC_REFS.items():
        cols = tables[table].columns
        for ref in refs:
            assert ref.column in cols, (table, ref.column)
            assert all(c in cols for c in ref.withheld), (table, ref.withheld)
            assert ref.kind_column is None or ref.kind_column in cols, (table, ref.kind_column)


@pytest.mark.parametrize(
    ("table", "column"),
    sorted({(t, ref.column) for t, refs in DOC_REFS.items() for ref in refs}),
)
def test_reference_to_restricted_document_never_ships(table: str, column: str) -> None:
    """Строка-связь не уезжает, у сущности со ссылкой ссылка вырезается."""
    ref = next(r for r in DOC_REFS[table] if r.column == column)
    base: dict[str, object] = {
        "kind": "x.y",
        "state": "accepted",
        "path": "docs/a.md",
        "sensitivity": "internal",
    }
    if ref.kind_column is not None:
        base[ref.kind_column] = sorted(ref.kinds)[0]
    for other in DOC_REFS[table]:
        base[other.column] = 1
        base[sensitivity_key(other.column)] = "internal"
    for label in ("restricted", None, "Internal"):
        row = {**base, sensitivity_key(column): label}
        if ref.whole_row:
            assert row_class(table, row) in {SyncClass.LOCAL, SyncClass.DERIVED}, label
        else:
            assert row_class(table, row) is not SyncClass.LOCAL
            assert {column, *ref.withheld} <= withheld_columns(table, row), label
    cleared = {**base, sensitivity_key(column): "internal"}
    assert withheld_columns(table, cleared) == frozenset()
    # и обратное: разрешённая метка не душит легитимную строку
    assert row_class(table, cleared) is not SyncClass.LOCAL


def test_null_reference_needs_no_label() -> None:
    assert (
        withheld_columns("plan", {"parent_doc_id": None, "completed_log_id": None}) == frozenset()
    )
    assert row_class("story_link", {"to_kind": "task", "to_ref": "ADO-1"}) is SyncClass.AUTHORED


def test_unknown_polymorphic_kind_is_treated_as_document() -> None:
    row = {"to_kind": "Document", "to_ref": "docs/a"}
    assert row_class("story_link", row) is SyncClass.LOCAL
    assert row_class("external_ref", {"entity_kind": "secret_kind", "entity_row_id": 1}) is (
        SyncClass.LOCAL
    )


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("project", "root_path"),
        ("project", "config_json"),
        ("approval", "payload_json"),
        ("finding", "payload"),
        ("revision", "run_id"),
        ("activity_event", "run_id"),
        ("document", "row_id"),
        ("document", "no_such_column"),
        ("task", "brand_new_column"),
        ("trace_call", "model"),
    ],
)
def test_sensitive_and_unknown_columns_are_local(table: str, column: str) -> None:
    assert reg.column_class(table, column) is SyncClass.LOCAL


def test_derived_column_is_rebuilt_not_shipped() -> None:
    """``link_suggestion.score`` — derived: каждая реплика пересчитывает его сама.

    Класс колонки — DERIVED, а не LOCAL: «не уезжает» верно для обоих, но
    derived ещё и пересобирается после pull. Уехать её заставила бы только
    запись в ``authored_columns`` — это и проверяется.
    """
    assert reg.column_class("link_suggestion", "score") is SyncClass.DERIVED
    assert "score" not in spec_for("link_suggestion").authored_columns


def test_authored_table_without_shipped_entry_ships_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Забытая запись в ``SHIPPED_COLUMNS`` — ни одна колонка не уезжает."""
    shipped = dict(reg.SHIPPED_COLUMNS)
    del shipped["task"]
    monkeypatch.setattr(reg, "SHIPPED_COLUMNS", shipped)
    assert reg.column_class("task", "title") is SyncClass.LOCAL
    assert reg.column_class("revision", "diff") is SyncClass.JOURNAL


def test_table_spec_mappings_are_immutable() -> None:
    source = {"topic_id": "adr_topic"}
    spec = TableSpec("x", SyncClass.AUTHORED, logical_fks=source)
    source["other_id"] = "task"  # правка исходного dict не просачивается
    assert dict(spec.logical_fks) == {"topic_id": "adr_topic"}
    for s in REGISTRY.values():
        for mapping in (s.logical_fks, s.human_id_refs):
            with pytest.raises(TypeError):
                mapping["x"] = "y"  # type: ignore[index]


def test_column_class_known_columns() -> None:
    assert reg.column_class("task", "title") is SyncClass.AUTHORED
    assert reg.column_class("revision", "diff") is SyncClass.JOURNAL
    assert reg.column_class("link_suggestion", "state") is SyncClass.AUTHORED
    assert reg.column_class("document", "projection_hash") is SyncClass.LOCAL


def test_column_class_unknown_table_raises() -> None:
    with pytest.raises(KeyError):
        reg.column_class("no_such_table", "x")


def test_row_class_unknown_table_raises() -> None:
    with pytest.raises(KeyError):
        row_class("no_such_table", {})


def test_registry_errors_do_not_echo_row_contents() -> None:
    secret = "s3cr3t-token-/Users/someone/.ssh"
    with pytest.raises(KeyError) as exc:
        row_class("no_such_table", {"body": secret, "path": secret})
    assert secret not in str(exc.value)
    with pytest.raises(KeyError) as exc:
        reg.column_class("no_such_table", "body")
    assert secret not in str(exc.value)


# --- машинно-приватные данные (security review CLO-001, второй раунд) --------


@pytest.mark.parametrize(
    "path",
    [
        "/Users/dakh/Git/x.py",
        "~/notes.md",
        "C:\\Users\\x\\a.py",
        "c:/x/a.py",
        " /Users/x/a.py",
        "file:///Users/x/a.py",
        "../secret",
        "foo/../../etc/passwd",
        "subdir\\..\\..\\key",
        "..",
        "a.py\x00",
        "",
        "\\\\host\\share\\a",
    ],
)
@pytest.mark.parametrize(
    ("table", "extra"),
    [
        ("affected_file", {}),
        ("module_code", {}),
        ("document", {"sensitivity": "internal"}),
        ("finding", {"source": "ai_review"}),
    ],
)
def test_absolute_paths_do_not_ship(table: str, extra: dict[str, object], path: str) -> None:
    """Абсолютный путь описывает машину, а не проект: строка не уезжает."""
    assert row_class(table, {**extra, "path": path}) is not SyncClass.AUTHORED


@pytest.mark.parametrize(
    ("table", "extra"),
    [
        ("affected_file", {}),
        ("module_code", {}),
        ("document", {"sensitivity": "internal"}),
        ("finding", {"source": "ai_review"}),
    ],
)
def test_relative_paths_ship(table: str, extra: dict[str, object]) -> None:
    assert row_class(table, {**extra, "path": "cod_doc/infra/db.py"}) is SyncClass.AUTHORED


def test_path_missing_is_local_where_required() -> None:
    assert row_class("affected_file", {}) is SyncClass.LOCAL
    assert row_class("module_code", {}) is SyncClass.LOCAL
    # у находки путь необязателен: NULL — не абсолютный путь
    assert row_class("finding", {"source": "ai_review", "path": None}) is SyncClass.AUTHORED


@pytest.mark.parametrize(
    ("table", "column"),
    [
        ("activity_event", "payload"),
        ("commit_link", "message"),
        ("commit_link", "author"),
    ],
)
def test_machine_and_git_metadata_columns_are_local(table: str, column: str) -> None:
    assert reg.column_class(table, column) is SyncClass.LOCAL


def test_shipped_columns_match_schema() -> None:
    """Новая колонка authored/journal-таблицы роняет гейт: её класс выбирают явно.

    В рантайме неизвестная колонка — local (``column_class``), так что без этой
    записи она не уехала бы; гейт не даёт этому случиться молча.
    """
    tables = _all_model_tables()
    problems = []
    for name, spec in REGISTRY.items():
        if spec.cls not in {SyncClass.AUTHORED, SyncClass.JOURNAL}:
            continue
        shipped = reg.SHIPPED_COLUMNS[name]
        schema = {c.name for c in tables[name].columns} - {"row_id"}
        problems += [
            f"{name}.{c}: не классифицирована"
            for c in sorted(schema - shipped - spec.local_columns)
        ]
        problems += [f"{name}.{c}: нет в схеме" for c in sorted(shipped - schema)]
        problems += [
            f"{name}.{c}: и shipped, и local" for c in sorted(shipped & spec.local_columns)
        ]
    assert set(reg.SHIPPED_COLUMNS) == {
        n for n, s in REGISTRY.items() if s.cls in {SyncClass.AUTHORED, SyncClass.JOURNAL}
    }
    assert problems == []


def test_uid_column_on_every_authored_table() -> None:
    """CLO-002: ``uid`` есть у каждой authored-таблицы, кроме тег-таблиц с составным PK.

    Новая authored-таблица без ``uid`` не адресуема операцией; колонка ``uid``
    вне authored — признак ошибки класса (решение 18).
    """
    tables = _all_model_tables()
    with_uid = {
        name
        for name, table in tables.items()
        if "uid" in table.columns and spec_for(name).cls is SyncClass.AUTHORED
    }
    authored = {spec.table for spec in authored_tables()}
    assert with_uid == authored - reg.COMPOSITE_PK_TABLES == reg.UID_TABLES
    assert authored >= reg.COMPOSITE_PK_TABLES
    for name in reg.COMPOSITE_PK_TABLES:
        assert "row_id" not in tables[name].columns, name
        assert spec_for(name).deterministic_uid, name
    for name in reg.UID_TABLES:
        assert "uid" in reg.SHIPPED_COLUMNS[name], name
        assert any(i.unique and i.name == f"uq_{name}_uid" for i in tables[name].indexes), name


def test_edge_uid_is_deterministic_over_endpoint_uids() -> None:
    row = {"from_task_id": "u-a", "to_task_id": "u-b", "kind": "blocks"}
    assert reg.edge_uid("dependency", row) == reg.edge_uid("dependency", dict(row))
    assert reg.edge_uid("dependency", row) != reg.edge_uid("dependency", {**row, "kind": "relates"})
    # таблица входит в ключ: одинаковые значения разных рёбер не сливаются
    assert reg.edge_uid("task_tag", {"task_id": "x", "tag_id": "y"}) != reg.edge_uid(
        "story_tag", {"story_id": "x", "tag_id": "y"}
    )
    # разделитель внутри значения не склеивает соседние колонки
    assert reg.edge_uid("affected_file", {"task_id": "a,b", "path": "c"}) != reg.edge_uid(
        "affected_file", {"task_id": "a", "path": "b,c"}
    )
    with pytest.raises(KeyError):
        reg.edge_uid("task", {})


def test_owned_rows_point_to_document() -> None:
    tables = _all_model_tables()
    for table in _DOC_OWNED:
        targets = {fk.column.table.name for fk in tables[table].columns["document_id"].foreign_keys}
        assert targets == {"document"}, table


_DECISION: dict[str, object] = {
    "from_section_id": 1,
    "from_section_id.sensitivity": "internal",
    "to_doc_key": "docs/b",
    "to_doc_key.sensitivity": "internal",
    "to_section_id": None,
}


def test_link_suggestion_decision_is_authored() -> None:
    """Q-017: подсказка derived, решение по ней (``state ≠ pending``) уезжает."""
    spec = spec_for("link_suggestion")
    assert spec.cls is SyncClass.DERIVED
    assert "state" in spec.authored_columns
    assert {"score", "evidence"}.isdisjoint(spec.authored_columns)
    assert spec.deterministic_uid == ("from_section_id", "to_doc_key", "to_section_id")
    assert row_class("link_suggestion", {**_DECISION, "state": "pending"}) is SyncClass.LOCAL
    assert row_class("link_suggestion", {**_DECISION, "state": "rejected"}) is SyncClass.DERIVED
    assert spec.row_is_local is not None
    assert not spec.row_is_local({**_DECISION, "state": "accepted"})


def test_routine_findings_are_derived() -> None:
    assert row_class("finding", {"source": "routine"}) is SyncClass.DERIVED
    assert row_class("finding", {"source": "ai_review"}) is SyncClass.AUTHORED


def test_link_and_routine_events_stay_local() -> None:
    assert row_class("activity_event", {"kind": "link.synced"}) is SyncClass.LOCAL
    assert row_class("activity_event", {"kind": "routine.fired"}) is SyncClass.LOCAL
    assert (
        row_class("activity_event", {"kind": "task.created"}) is SyncClass.LOCAL
    )  # без области — local


# --- ссылки ------------------------------------------------------------------


def test_human_id_refs_resolve() -> None:
    assert human_ref_target("adr_task", "task_id", {}) == "task"
    assert human_ref_target("open_question", "resolved_by_adr", {}) == "adr"
    assert human_ref_target("story_link", "to_ref", {"to_kind": "task"}) == "task"
    assert human_ref_target("scenario_link", "to_ref", {"to_kind": "criterion"}) == "user_story"
    assert human_ref_target("open_question_link", "to_ref", {"to_kind": "url"}) is None
    assert human_ref_target("task", "title", {}) is None


def test_human_id_ref_targets_are_leased_or_discriminators() -> None:
    tables = _all_model_tables()
    leased = {
        s.table for s in REGISTRY.values() if any(k.kind is KeyKind.LEASED for k in s.natural_keys)
    }
    for spec in REGISTRY.values():
        for col, target in spec.human_id_refs.items():
            assert target in leased or target in tables[spec.table].columns, (spec.table, col)
            assert target not in REGISTRY or target in leased, (spec.table, col)


def test_entity_kinds_map_to_registered_tables() -> None:
    assert set(ENTITY_KIND_TABLES) == set(EntityKind)
    assert set(ENTITY_KIND_TABLES.values()) <= set(REGISTRY)


def test_spec_for_unknown_table_raises() -> None:
    with pytest.raises(KeyError):
        spec_for("no_such_table")


def test_authored_tables_sorted_and_authored() -> None:
    specs = authored_tables()
    assert [s.table for s in specs] == sorted(s.table for s in specs)
    assert all(s.cls is SyncClass.AUTHORED for s in specs)


def test_duplicate_entry_rejected() -> None:
    with pytest.raises(ValueError, match="дважды"):
        reg._index((TableSpec("x", SyncClass.LOCAL), TableSpec("x", SyncClass.LOCAL)))


# --- зубы гейта на синтетической схеме ---------------------------------------


def _synthetic() -> MetaData:
    md = MetaData()
    Table("parent", md, Column("row_id", Integer, primary_key=True))
    Table(
        "child",
        md,
        Column("row_id", Integer, primary_key=True),
        Column("parent_id", Integer, ForeignKey("parent.row_id")),
        Column("topic_id", Integer),
        Column("task_id", String(32)),
    )
    return md


def test_gate_flags_table_without_entry() -> None:
    tables = _synthetic().tables
    registry = {"parent": TableSpec("parent", SyncClass.AUTHORED)}
    assert missing_entries(tables, registry) == ["child: нет записи в sync_registry"]


def test_gate_flags_unconstrained_fk() -> None:
    tables = _synthetic().tables
    registry = {
        "parent": TableSpec("parent", SyncClass.AUTHORED),
        "child": TableSpec("child", SyncClass.AUTHORED),
    }
    assert uncovered_fks(tables, registry) == [
        "child.topic_id: FK без ограничения не покрыт logical_fks"
    ]
    registry["child"] = TableSpec("child", SyncClass.AUTHORED, logical_fks={"topic_id": "parent"})
    assert uncovered_fks(tables, registry) == []
    registry["child"] = TableSpec("child", SyncClass.AUTHORED, logical_fks={"topic_id": "nope"})
    assert uncovered_fks(tables, registry) == [
        "child.topic_id: logical_fks → неизвестная таблица 'nope'"
    ]


def test_gate_ignores_unshipped_tables() -> None:
    tables = _synthetic().tables
    registry = {
        "parent": TableSpec("parent", SyncClass.AUTHORED),
        "child": TableSpec("child", SyncClass.DERIVED),
    }
    assert uncovered_fks(tables, registry) == []


# --- слои --------------------------------------------------------------------


_DYNAMIC_IMPORT_CALLS = frozenset({"__import__", "import_module"})


def _is_type_checking_guard(node: ast.If) -> bool:
    test = node.test
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def layering_violations(source: str) -> list[str]:
    """Импорты реестра вне stdlib + ``cod_doc.domain``.

    Блок ``if TYPE_CHECKING:`` в рантайме не исполняется — его импорты не
    считаются. Динамический импорт (``__import__``, ``importlib.import_module``)
    статически не проверить — он нарушение сам по себе.
    """
    modules: set[str] = set()
    problems: list[str] = []
    stack: list[ast.AST] = [ast.parse(source)]
    while stack:
        node = stack.pop()
        if isinstance(node, ast.If) and _is_type_checking_guard(node):
            stack.extend(node.orelse)
            continue
        if isinstance(node, ast.Import):
            modules |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Call) and _call_name(node) in _DYNAMIC_IMPORT_CALLS:
            problems.append(f"динамический импорт в строке {node.lineno}")
        stack.extend(ast.iter_child_nodes(node))
    problems += sorted(
        m
        for m in modules
        if m != "__future__"
        and not m.startswith("cod_doc.domain")
        and m.split(".")[0] not in sys.stdlib_module_names
    )
    return problems


def test_registry_imports_only_stdlib_and_domain() -> None:
    """RFC 29 §10.2: stdlib + domain — реестр читают и захват, и сервисы."""
    assert layering_violations(Path(reg.__file__).read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy\n",
        "from cod_doc.services import x\n",
        "import importlib\ndef f():\n    importlib.import_module('cod_doc.services')\n",
        "def f():\n    __import__('sqlalchemy')\n",
        "from importlib import import_module\nimport_module('x')\n",
    ],
)
def test_layering_gate_catches_static_and_dynamic_imports(source: str) -> None:
    assert layering_violations(source) != []


def test_layering_gate_skips_type_checking_block() -> None:
    source = (
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from sqlalchemy import Table\n"
        "import typing\nif typing.TYPE_CHECKING:\n    import cod_doc.services\n"
    )
    assert layering_violations(source) == []


# --- регрессии fail-closed (security review CLO-001, четвёртый раунд) --------
# Каждый случай ниже уже уезжал после одной из правок. Тесты закрепляют:
# ссылка, которую нельзя проверить, считается ссылкой на restricted-документ.


@pytest.mark.parametrize(
    ("table", "row"),
    [
        ("section", {}),
        ("section", {"document_id": None}),
        ("doc_comment", {}),
        ("doc_comment", {"document_id": None}),
        ("document_tag", {"tag_id": 1}),
        ("revision", {"entity_kind": "document"}),
        ("revision", {"entity_kind": "section", "entity_id": None}),
        ("activity_event", {"kind": "doc.x", "scope_kind": "doc"}),
        ("activity_event", {"kind": "doc.x", "scope_kind": "document", "scope_id": None}),
        ("approval_doc_revision_link", {}),
        ("approval_doc_revision_link", {"revision_id": None}),
        ("link_suggestion", {"state": "accepted"}),
        ("link_suggestion", {"state": "accepted", "from_section_id": None}),
        ("story_link", {"to_kind": "document"}),
        ("open_question_link", {"to_kind": "section", "to_ref": None}),
        ("external_ref", {"entity_kind": "document"}),
        ("external_ref", {"entity_kind": "section", "entity_row_id": None}),
    ],
)
def test_unverifiable_document_reference_is_local(table: str, row: dict[str, object]) -> None:
    assert row_class(table, row) is SyncClass.LOCAL


def test_cleared_labels_are_an_allow_list() -> None:
    """Новая метка ``Sensitivity`` не уезжает, пока её не впишут в перечень."""
    assert (
        frozenset({Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL})
        == reg._CLEARED_SENSITIVITY
    )
    assert {s for s in Sensitivity if reg._cleared(s.value)} == reg._CLEARED_SENSITIVITY


def test_doc_ref_nullability_matches_schema() -> None:
    """``nullable`` ссылки нельзя ослабить: NOT NULL колонка с None — не «нет ссылки»."""
    tables = _all_model_tables()
    for table, refs in DOC_REFS.items():
        for ref in refs:
            assert ref.nullable == bool(tables[table].columns[ref.column].nullable), (
                table,
                ref.column,
            )
