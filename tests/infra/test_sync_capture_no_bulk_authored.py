"""CLO-008 (RFC 29 §3.4, §10.10 ``SYNC-001``): нет bulk-записи в authored мимо журнала.

Хук захвата (``infra/sync_capture.py``) видит только то, что проходит через
unit-of-work ``Session``. Запрос ``session.execute(update|insert|delete(Model))``
идёт мимо него: строка в БД меняется, операции в ``sync_op`` нет, реплики
молча расходятся. Гейт запрещает такую запись в таблицы под захватом
(``sync_capture.CAPTURED_TABLES``: authored плюс derived с authored-колонками)
во всём ``cod_doc/`` кроме ``infra/`` — там живёт сам захват (``_late_edge_uid``
дописывает uid ребра UPDATE'ом изнутри ``after_flush``) и миграции.

Что считается записью:

- конструкторы DML из SQLAlchemy под любым именем: ``update``/``insert``/
  ``delete`` из ``sqlalchemy`` и диалектов (``sqlite_insert``, ``_update`` —
  алиасы разрешаются по импортам модуля, включая импорты в теле функции),
  ``sa.update(...)`` через модуль, ``Model.__table__.update()``;
- ORM-bulk ``session.query(Model)….update(...)``/``.delete()``;
- ``text("UPDATE|INSERT INTO|DELETE FROM|REPLACE INTO <table> …")``.

Модель разрешается в таблицу по метаданным ``cod_doc.infra.models``.

Когда запись разрешена:

1. ``update`` с литеральным ``.values(k=…)``, где ни одна колонка не уезжает
   (``sync_registry.column_class`` — не ``AUTHORED``): local-колонки
   authored-таблицы (``document.projection_hash``) и derived-колонки
   (``link_suggestion.score``) журналу не нужны (RFC §3.4: «места, бьющие в
   derived/local, проходят гейт как есть»). ``.values(**d)``, позиционный
   словарь, ``update`` без ``.values`` (ORM bulk по списку) — колонки
   неизвестны, значит запрещено: fail-closed.
2. Функция зовёт ``sync_capture.record(..., table="<та же таблица>")`` — сама
   или через функцию того же модуля (транзитивно): ``ingest_findings`` пишет
   upsert, а ``record`` зовёт её помощник ``_record_upsert``. Таблица —
   литерал или модульная строковая константа; иное — не засчитывается.
   «Те же строки» статически не проверить: гейт сверяет таблицу, строки —
   тест захвата ``test_sync_capture_write_paths.py``.

Allowlist нет: исключение из правила выражается вызовом ``record``.
"""

from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

import cod_doc.infra.models.link_suggestions  # noqa: F401 — маппер регистрируется лениво
from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.models import Base

REPO_ROOT = Path(__file__).resolve().parents[2]
SCANNED_ROOT = REPO_ROOT / "cod_doc"
EXCLUDED = (SCANNED_ROOT / "infra",)

DML_VERBS = frozenset({"insert", "update", "delete"})
_QUERY_BULK = frozenset({"update", "delete"})
_SQL_DML = re.compile(
    r"^\s*(?:UPDATE(?:\s+OR\s+\w+)?|(?:INSERT|REPLACE)(?:\s+OR\s+\w+)?\s+INTO|DELETE\s+FROM)"
    r"\s+[\"`\[]?(\w+)",
    re.IGNORECASE,
)
_SYNC_CAPTURE = "cod_doc.infra.sync_capture"


def _model_tables() -> dict[str, str]:
    return {m.class_.__name__: str(m.local_table.name) for m in Base.registry.mappers}  # type: ignore[attr-defined]


def _attribute_columns() -> dict[str, dict[str, str]]:
    """Таблица → (атрибут модели → колонка): ``.values()`` принимает атрибуты."""
    out: dict[str, dict[str, str]] = {}
    for mapper in Base.registry.mappers:
        table = str(mapper.local_table.name)  # type: ignore[attr-defined]
        out[table] = {
            prop.key: col.name
            for prop in mapper.column_attrs
            for col in prop.columns[:1]
            if hasattr(col, "name")
        }
    return out


MODEL_TABLES = _model_tables()
ATTR_COLUMNS = _attribute_columns()


@dataclass(frozen=True, slots=True)
class Violation:
    path: str
    lineno: int
    function: str
    table: str
    what: str

    def __str__(self) -> str:
        return f"{self.path}:{self.lineno} {self.function}: {self.what} в {self.table}"


@dataclass(frozen=True, slots=True)
class _Write:
    table: str
    lineno: int
    what: str
    #: Колонки ``.values()``; ``None`` — неизвестны или вся строка.
    columns: frozenset[str] | None


class _Imports(ast.NodeVisitor):
    """Имена, под которыми модуль видит DML-конструкторы, ``text`` и ``record``."""

    def __init__(self) -> None:
        self.verbs: dict[str, str] = {}  # алиас → insert/update/delete
        self.sa_modules: set[str] = set()  # ``import sqlalchemy as sa``
        self.text: set[str] = set()
        self.capture_modules: set[str] = set()  # ``from cod_doc.infra import sync_capture``
        self.record: set[str] = set()  # ``from …sync_capture import record``

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.name.split(".")[0] == "sqlalchemy":
                self.sa_modules.add(alias.asname or alias.name.split(".")[0])
            if alias.name == _SYNC_CAPTURE and alias.asname:
                self.capture_modules.add(alias.asname)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            bound = alias.asname or alias.name
            if module.split(".")[0] == "sqlalchemy":
                if alias.name in DML_VERBS:
                    self.verbs[bound] = alias.name
                elif alias.name == "text":
                    self.text.add(bound)
                elif alias.name.islower():
                    self.sa_modules.add(bound)  # ``from sqlalchemy.dialects import sqlite``
            elif f"{module}.{alias.name}" == _SYNC_CAPTURE:
                self.capture_modules.add(bound)
            elif module == _SYNC_CAPTURE and alias.name == "record":
                self.record.add(bound)


def _parents(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    return {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}


def _model_table(node: ast.expr | None) -> str | None:
    """``TaskModel`` / ``models.TaskModel`` / ``TaskModel.__table__`` → имя таблицы."""
    if isinstance(node, ast.Attribute) and node.attr == "__table__":
        node = node.value
    if isinstance(node, ast.Name):
        return MODEL_TABLES.get(node.id)
    if isinstance(node, ast.Attribute):
        return MODEL_TABLES.get(node.attr)
    return None


def _chained_values(node: ast.expr, parents: dict[ast.AST, ast.AST]) -> frozenset[str] | None:
    """Колонки ``.values(k=…)`` в цепочке методов над конструктором ``node``."""
    columns: set[str] = set()
    seen_values = False
    current: ast.AST = node
    while True:
        attr = parents.get(current)
        if not isinstance(attr, ast.Attribute) or attr.value is not current:
            break
        call = parents.get(attr)
        if not isinstance(call, ast.Call) or call.func is not attr:
            break
        if attr.attr == "values":
            if call.args or any(k.arg is None for k in call.keywords):
                return None
            seen_values = True
            columns.update(k.arg for k in call.keywords if k.arg is not None)
        current = call
    return frozenset(columns) if seen_values else None


def _query_table(node: ast.expr) -> str | None:
    """Таблица ``session.query(Model)`` где-то в цепочке ``….update()``."""
    current: ast.expr = node
    while isinstance(current, ast.Call | ast.Attribute):
        if isinstance(current, ast.Call):
            func = current.func
            if isinstance(func, ast.Attribute) and func.attr == "query" and current.args:
                return _model_table(current.args[0])
            current = func
        else:
            current = current.value
    return None


def _literal_keys(node: ast.expr | None) -> frozenset[str] | None:
    if not isinstance(node, ast.Dict):
        return None
    keys: set[str] = set()
    for key in node.keys:
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            keys.add(key.value)
        elif isinstance(key, ast.Attribute):
            keys.add(key.attr)  # ``{TaskModel.status: …}``
        else:
            return None
    return frozenset(keys)


def _write_of(call: ast.Call, imports: _Imports, parents: dict[ast.AST, ast.AST]) -> _Write | None:
    func = call.func
    verb: str | None = None
    target: ast.expr | None = call.args[0] if call.args else None
    if isinstance(func, ast.Name):
        if func.id in imports.text:
            arg = call.args[0] if call.args else None
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                hit = _SQL_DML.match(arg.value)
                if hit is not None:
                    return _Write(hit.group(1).lower(), call.lineno, "text(DML)", None)
            return None
        verb = imports.verbs.get(func.id)
    elif isinstance(func, ast.Attribute) and func.attr in DML_VERBS:
        owner = func.value
        if isinstance(owner, ast.Name) and owner.id in imports.sa_modules:
            verb = func.attr
        elif isinstance(owner, ast.Attribute) and owner.attr == "__table__":
            verb, target = func.attr, owner
        elif func.attr in _QUERY_BULK:
            table = _query_table(owner)
            if table is not None:
                columns = _literal_keys(call.args[0] if call.args else None)
                return _Write(table, call.lineno, f"query().{func.attr}()", columns)
            return None
    if verb is None:
        return None
    table = _model_table(target)
    if table is None:
        return None
    columns = _chained_values(call, parents) if verb == "update" else None
    return _Write(table, call.lineno, f"{verb}()", columns)


def _ships(write: _Write) -> bool:
    """Задевает ли запись то, что уезжает в журнал."""
    if write.table not in cap.CAPTURED_TABLES:
        return False
    if write.columns is None:
        return True
    attrs = ATTR_COLUMNS.get(write.table, {})
    for key in write.columns:
        column = attrs.get(key)
        if column is None or reg.column_class(write.table, column) is reg.SyncClass.AUTHORED:
            return True
    return False


def _module_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign | ast.AnnAssign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            out.update({t.id: node.value.value for t in targets if isinstance(t, ast.Name)})
    return out


def _is_record(func: ast.expr, imports: _Imports) -> bool:
    if isinstance(func, ast.Name):
        return func.id in imports.record
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "record"
        and isinstance(func.value, ast.Name)
        and func.value.id in imports.capture_modules
    )


def _own_nodes(fn: ast.AST) -> list[ast.AST]:
    """Узлы тела функции без вложенных функций (у них свой скоуп)."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        out.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return out


def scan_source(source: str, path: str) -> list[Violation]:
    tree = ast.parse(source)
    imports = _Imports()
    imports.visit(tree)
    parents = _parents(tree)
    constants = _module_constants(tree)

    scopes: list[tuple[str, ast.AST]] = [("<module>", tree)]
    scopes += [
        (n.name, n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    records: dict[str, set[str]] = {}
    calls: dict[str, set[str]] = {}
    writes: dict[str, list[_Write]] = {}
    for name, scope in scopes:
        for node in _own_nodes(scope):
            if not isinstance(node, ast.Call):
                continue
            if _is_record(node.func, imports):
                table = next((k.value for k in node.keywords if k.arg == "table"), None)
                if isinstance(table, ast.Constant) and isinstance(table.value, str):
                    records.setdefault(name, set()).add(table.value)
                elif isinstance(table, ast.Name) and table.id in constants:
                    records.setdefault(name, set()).add(constants[table.id])
                continue
            if isinstance(node.func, ast.Name):
                calls.setdefault(name, set()).add(node.func.id)
            write = _write_of(node, imports, parents)
            if write is not None and _ships(write):
                writes.setdefault(name, []).append(write)

    # Транзитивно по функциям модуля: помощник с ``record`` засчитывается вызывающему.
    changed = True
    while changed:
        changed = False
        for name, callees in calls.items():
            reached = set().union(*(records.get(c, set()) for c in callees))
            if not reached <= records.get(name, set()):
                records.setdefault(name, set()).update(reached)
                changed = True

    return [
        Violation(path, w.lineno, name, w.table, w.what)
        for name, ws in writes.items()
        for w in ws
        if w.table not in records.get(name, set())
    ]


def _scanned_files() -> list[Path]:
    return sorted(
        p
        for p in SCANNED_ROOT.rglob("*.py")
        if not any(p.is_relative_to(excluded) for excluded in EXCLUDED)
    )


def test_no_bulk_write_into_captured_tables() -> None:
    """SYNC-001: ни одной bulk-записи в таблицы под захватом без ``record``."""
    violations = [
        v
        for path in _scanned_files()
        for v in scan_source(path.read_text(encoding="utf-8"), str(path.relative_to(REPO_ROOT)))
    ]
    assert violations == [], "bulk-запись мимо журнала (RFC 29 §3.4):\n" + "\n".join(
        map(str, violations)
    )


def test_scan_covers_services_and_presentation() -> None:
    scanned = {p.relative_to(SCANNED_ROOT).parts[0] for p in _scanned_files()}
    assert {"services", "api", "cli", "mcp"} <= scanned
    assert "infra" not in scanned


# --- зубы: синтетические источники ---------------------------------------------


def _scan(snippet: str) -> list[str]:
    return [
        f"{v.function}:{v.table}:{v.what}" for v in scan_source(textwrap.dedent(snippet), "x.py")
    ]


def test_catches_bulk_update_of_task() -> None:
    """Acceptance CLO-008: ``session.execute(update(TaskModel)…)`` в сервисе — красное."""
    assert _scan(
        """
        from sqlalchemy import update
        from cod_doc.infra.models import TaskModel

        def close(session, row_id):
            session.execute(
                update(TaskModel).where(TaskModel.row_id == row_id).values(status="done")
            )
        """
    ) == ["close:task:update()"]


def test_passes_function_that_records_the_same_table() -> None:
    """Acceptance CLO-008: функция с ``sync_capture.record`` той же таблицы — проходит."""
    assert (
        _scan(
            """
        from sqlalchemy import update
        from cod_doc.infra import sync_capture
        from cod_doc.infra.models import TaskModel

        def close(session, row_id, uid, pid):
            session.execute(
                update(TaskModel).where(TaskModel.row_id == row_id).values(status="done")
            )
            sync_capture.record(
                session, project_id=pid, table="task", entity_uid=uid, kind="upsert",
                fields={"status": "done"}, base={"status": "todo"}, author="human:x",
            )
        """
        )
        == []
    )


def test_record_of_another_table_does_not_count() -> None:
    assert _scan(
        """
        from sqlalchemy import update
        from cod_doc.infra import sync_capture as cap
        from cod_doc.infra.models import TaskModel

        def close(session, row_id):
            session.execute(update(TaskModel).values(status="done"))
            cap.record(session, project_id=1, table="story", entity_uid="u", kind="upsert",
                       fields={}, base={}, author="human:x")
        """
    ) == ["close:task:update()"]


def test_record_through_module_helper_counts() -> None:
    """Как в ``finding_service/dedup.py``: upsert в одной функции, ``record`` — в помощнике."""
    assert (
        _scan(
            """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from cod_doc.infra import sync_capture
        from cod_doc.infra.models import FindingModel

        _TABLE = "finding"

        def ingest(session):
            session.execute(sqlite_insert(FindingModel).values(title="t"))
            _record(session)

        def _record(session):
            sync_capture.record(session, project_id=1, table=_TABLE, entity_uid="u",
                                kind="upsert", fields={}, base={}, author="system:x")
        """
        )
        == []
    )


@pytest.mark.parametrize(
    ("snippet", "expected"),
    [
        pytest.param(
            """
            def f(session):
                from sqlalchemy import update as _update
                from cod_doc.infra.models.documents import DocumentModel
                session.execute(_update(DocumentModel).values(title="x"))
            """,
            ["f:document:update()"],
            id="local-import-alias",
        ),
        pytest.param(
            """
            import sqlalchemy as sa
            from cod_doc.infra import models
            def f(session):
                session.execute(sa.delete(models.ScenarioStepModel))
            """,
            ["f:scenario_step:delete()"],
            id="module-attribute",
        ),
        pytest.param(
            """
            from cod_doc.infra.models import TaskModel
            def f(session):
                session.execute(TaskModel.__table__.delete())
            """,
            ["f:task:delete()"],
            id="table-method",
        ),
        pytest.param(
            """
            from cod_doc.infra.models import TaskModel
            def f(session):
                session.query(TaskModel).filter(TaskModel.row_id == 1).update({"status": "done"})
            """,
            ["f:task:query().update()"],
            id="orm-query-bulk",
        ),
        pytest.param(
            """
            from sqlalchemy import text
            def f(session):
                session.execute(text("UPDATE task SET status = 'done' WHERE row_id = :r"))
            """,
            ["f:task:text(DML)"],
            id="raw-sql",
        ),
        pytest.param(
            """
            from sqlalchemy import update
            from cod_doc.infra.models import TaskModel
            def f(session, rows):
                session.execute(update(TaskModel), rows)
            """,
            ["f:task:update()"],
            id="orm-bulk-by-pk-unknown-columns",
        ),
        pytest.param(
            """
            from sqlalchemy import update
            from cod_doc.infra.models import TaskModel
            def f(session, changes):
                session.execute(update(TaskModel).values(**changes))
            """,
            ["f:task:update()"],
            id="values-kwargs-unknown",
        ),
        pytest.param(
            """
            from sqlalchemy import update
            from cod_doc.infra.models import TaskModel
            session = None
            session.execute(update(TaskModel).values(status="x"))
            """,
            ["<module>:task:update()"],
            id="module-level",
        ),
    ],
)
def test_catches_bulk_write_forms(snippet: str, expected: list[str]) -> None:
    assert _scan(snippet) == expected


@pytest.mark.parametrize(
    "snippet",
    [
        pytest.param(
            """
            from sqlalchemy import update
            from cod_doc.infra.models.documents import DocumentModel
            def f(session):
                session.execute(update(DocumentModel).values(projection_hash="h"))
            """,
            id="local-column-of-authored-table",
        ),
        pytest.param(
            """
            from sqlalchemy import delete
            from cod_doc.infra.models import CodeEdgeModel
            def f(session):
                session.execute(delete(CodeEdgeModel))
            """,
            id="derived-table",
        ),
        pytest.param(
            """
            from sqlalchemy import update
            from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
            def f(session):
                session.execute(update(LinkSuggestionModel).values(score=0.9, evidence="{}"))
            """,
            id="derived-column-of-table-with-authored-columns",
        ),
        pytest.param(
            """
            from sqlalchemy import text
            def f(session):
                session.execute(text("DELETE FROM db_search_idx WHERE project_id = :p"))
            """,
            id="raw-sql-into-local-index",
        ),
        pytest.param(
            """
            from cod_doc.services import task_service
            def f(session):
                task_service.update(session, task_id="X", title="t")
            """,
            id="service-named-update",
        ),
    ],
)
def test_passes_writes_the_journal_does_not_need(snippet: str) -> None:
    assert _scan(snippet) == []


def test_local_columns_in_fixtures_are_really_local() -> None:
    """Зубы выше опираются на реестр: если колонка станет authored, тест скажет."""
    assert reg.column_class("document", "projection_hash") is not reg.SyncClass.AUTHORED
    assert reg.column_class("link_suggestion", "score") is not reg.SyncClass.AUTHORED
    assert reg.column_class("link_suggestion", "state") is reg.SyncClass.AUTHORED
    assert "code_edge" not in cap.CAPTURED_TABLES


# --- красное до CLO-007: три места, которые гейт ловил бы ------------------------

#: Выдержки кода до CLO-007 (``git show 92ef2a2:<path>``), сокращённые до записи.
PRE_CLO_007 = {
    "link_service/semantic.py::update_suggestion_state": """
        def update_suggestion_state(session, row_id, new_state, *, project_id):
            from sqlalchemy import update
            from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
            session.execute(
                update(LinkSuggestionModel)
                .where(LinkSuggestionModel.row_id == row_id, _in_project(project_id))
                .values(state=new_state, updated_at=datetime.now(UTC))
            )
            return True
        """,
    "link_service/semantic.py::_auto_accept_above": """
        def _auto_accept_above(session, from_section_id, threshold):
            from sqlalchemy import update
            from cod_doc.infra.models.link_suggestions import LinkSuggestionModel
            session.execute(
                update(LinkSuggestionModel)
                .where(LinkSuggestionModel.from_section_id == from_section_id)
                .values(state="accepted", updated_at=datetime.now(UTC))
            )
        """,
    "scenario_service/steps.py::set_steps": """
        from sqlalchemy import delete
        from cod_doc.infra.models import ScenarioStepModel
        def set_steps(session, *, model):
            session.execute(
                delete(ScenarioStepModel).where(ScenarioStepModel.scenario_row_id == model.row_id)
            )
        """,
    "finding_service/dedup.py::ingest_findings": """
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from cod_doc.infra.models import FindingModel
        def ingest_findings(session, *, project_id, seeds):
            for seed in seeds:
                stmt = sqlite_insert(FindingModel).values(project_id=project_id)
                session.execute(stmt)
        """,
}


@pytest.mark.parametrize("name", sorted(PRE_CLO_007))
def test_pre_clo_007_code_is_red(name: str) -> None:
    assert len(_scan(PRE_CLO_007[name])) == 1, name
