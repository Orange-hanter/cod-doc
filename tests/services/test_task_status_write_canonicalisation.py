"""ADO-156: легаси-написание принимается на входе всегда, в БД не пишется никогда.

`pending` ≡ `todo` и `in-progress` ≡ `in_progress` — один бакет, но до
миграции `0037_task_status_canonicalisation` в колонке `task.status` лежали
оба написания сразу: `create()` жёстко писал `pending`, `checkout_service`
выбирал написание по тому, откуда задача пришла, `update_status` клал
переданное as-is. Какое написание окажется в базе, зависело от того, когда
задачу создали, — и каждое место, сравнивающее статус точной строкой,
отвечало на половину базы.

Почему ассерты читают СЫРУЮ колонку через `text("SELECT status …")`, а не
`Task.status`: гидрация через `TaskStatus("pending")` возвращает валидный
член enum, который печатается и сравнивается как обычный статус. Легаси-строка
за ним не видна вовсе — тест на доменном объекте зеленел бы при полностью
легаси-базе.

Три уровня:

1. поведенческий — по каждому write-пути (`create`, `update_status`,
   `checkout`, `complete`, `revision.revert`);
2. инвариант по всей БД — пересечение `TASK_STATUS_ALIASES` с
   `SELECT DISTINCT status FROM task` пусто; новый алиас покрывается
   автоматически, потому что множество берётся из самой карты;
3. AST — в `cod_doc/` не появилось нового присваивания `.status`
   легаси-константой (и нового `status=`/`new_status=` легаси-аргумента).
   Пункты 1–2 доказывают поведение сегодняшних путей; пункт 3 ловит путь,
   которого сегодня нет.
"""

from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import (
    TASK_STATUS_ALIASES,
    EntityKind,
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import checkout_service as checkout
from cod_doc.services import revision_service as rev
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Сканируем весь пакет, а не только `services/`: статус пишут и
#: поверхности — `api/`, `cli/`, `mcp/` — и репозитории в `infra/`.
COD_DOC_DIR = Path(__file__).resolve().parents[2] / "cod_doc"


# --------------------------------------------------------------------------- #
# Хелперы                                                                      #
# --------------------------------------------------------------------------- #


def _seed_project(session: Session) -> tuple[int, int, int]:
    """Проект / план / секция — один набор на тест, сколько бы задач ни было."""
    existing = session.execute(
        text(
            "SELECT p.row_id, pl.row_id, s.row_id FROM project p "
            "JOIN plan pl ON pl.project_id = p.row_id "
            "JOIN plan_section s ON s.plan_id = pl.row_id WHERE p.slug = 'ca'"
        )
    ).first()
    if existing is not None:
        return int(existing[0]), int(existing[1]), int(existing[2])

    now = datetime.now(UTC)
    proj = ProjectModel(slug="ca", title="CA", root_path="/tmp/ca", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    plan = PlanModel(project_id=proj.row_id, scope="ca-plan", created=now, last_updated=now)
    session.add(plan)
    session.flush()
    section = PlanSectionModel(
        plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0
    )
    session.add(section)
    session.flush()
    return proj.row_id, plan.row_id, section.row_id


def _make_task(session: Session, task_id: str, *, raw_status: str | None = None) -> int:
    """Создать задачу и, если нужно, ПРИНУДИТЕЛЬНО положить сырой статус.

    `raw_status` идёт мимо сервисов — так выглядит строка из БД, ещё не
    поднятой на `0037`, и только так можно проверить, что write-путь
    приводит её к канону, а не сохраняет как есть.
    """
    project_id, plan_id, section_id = _seed_project(session)
    task = tasks.create(
        session,
        project_id=project_id,
        plan_id=plan_id,
        section_id=section_id,
        task_id=task_id,
        title=f"task {task_id}",
        type=TaskType.FEATURE,
        priority=Priority.MEDIUM,
        author="human:test",
    )
    if raw_status is not None:
        model = session.get(TaskModel, task.row_id)
        assert model is not None
        model.status = raw_status
        session.flush()
    assert task.row_id is not None
    return task.row_id


def _raw_status(session: Session, task_id: str) -> str:
    """Статус так, как он лежит в колонке, без гидрации через enum."""
    return str(
        session.execute(
            text("SELECT status FROM task WHERE task_id = :t"), {"t": task_id}
        ).scalar_one()
    )


def _stored_statuses(session: Session) -> set[str]:
    return {str(row[0]) for row in session.execute(text("SELECT DISTINCT status FROM task")).all()}


# --------------------------------------------------------------------------- #
# 1. Поведение каждого write-пути                                              #
# --------------------------------------------------------------------------- #


def test_create_writes_the_canonical_spelling(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Новая задача рождается в `todo`, а не в легаси `pending`."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _make_task(session, "CA-001")
        assert _raw_status(session, "CA-001") == TaskStatus.TODO.value


def test_create_revision_records_the_status_it_actually_wrote(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Ревизия create не должна рассказывать про строку, которой в колонке нет."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        row_id = _make_task(session, "CA-002")
        revisions = rev.list_for_entity(session, EntityKind.TASK, row_id)
        create_diffs = [
            json.loads(r.diff) for r in revisions if json.loads(r.diff).get("op") == "create"
        ]
        assert create_diffs, "create обязан писать ревизию"
        assert create_diffs[0]["status"] == _raw_status(session, "CA-002")


@pytest.mark.parametrize(
    ("start_raw", "legacy_target", "expected"),
    [
        ("backlog", TaskStatus.PENDING, "todo"),
        ("blocked", TaskStatus.IN_PROGRESS, "in_progress"),
    ],
    ids=["pending→todo", "in-progress→in_progress"],
)
def test_update_status_canonicalises_a_legacy_enum_member(  # type: ignore[no-untyped-def]
    engine_with_schema,
    start_raw: str,
    legacy_target: TaskStatus,
    expected: str,
) -> None:
    """Легаси-член enum на входе принимается, но в колонку идёт канон."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _make_task(session, "CA-003", raw_status=start_raw)
        tasks.update_status(
            session,
            task_id="CA-003",
            new_status=legacy_target,
            author="human:test",
        )
        assert _raw_status(session, "CA-003") == expected


def test_update_status_to_the_same_bucket_is_not_a_transition(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`todo → pending` — смена написания, а не статуса: ни записи, ни ревизии.

    Без канонизации раннего возврата этот вызов прошёл бы как настоящий
    переход и вернул задачу в легаси-написание.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        row_id = _make_task(session, "CA-004")
        before = len(rev.list_for_entity(session, EntityKind.TASK, row_id))
        tasks.update_status(
            session,
            task_id="CA-004",
            new_status=TaskStatus.PENDING,
            author="human:test",
        )
        assert _raw_status(session, "CA-004") == TaskStatus.TODO.value
        assert len(rev.list_for_entity(session, EntityKind.TASK, row_id)) == before


def test_checkout_of_a_legacy_row_lands_canonical(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Задача из немигрированного `pending` уходит в `in_progress`, не в `in-progress`."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _make_task(session, "CA-005", raw_status="pending")
        result = checkout.checkout(session, "CA-005", agent="agent:test")
        assert result.new_status == TaskStatus.IN_PROGRESS_NEW.value
        assert _raw_status(session, "CA-005") == TaskStatus.IN_PROGRESS_NEW.value
        # Снимок «что было до чекаута» — это история, её не переписываем.
        assert result.expected_status_at_checkout == "pending"


def test_complete_from_a_legacy_row_leaves_no_legacy_behind(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Авточекаут-нога `complete()` тоже идёт через канонизирующий write-путь."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        row_id = _make_task(session, "CA-006", raw_status="pending")
        tasks.complete(session, task_id="CA-006", author="human:test")
        assert _raw_status(session, "CA-006") == TaskStatus.DONE.value
        status_diffs = [
            json.loads(r.diff)
            for r in rev.list_for_entity(session, EntityKind.TASK, row_id)
            if json.loads(r.diff).get("op") == "status"
        ]
        assert status_diffs, "авточекаут обязан оставить ревизию перехода"
        assert all(d["new"] not in TASK_STATUS_ALIASES for d in status_diffs)


def test_revert_of_a_historical_legacy_revision_lands_canonical(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """`revision.diff` полон исторических `"pending"` — реверт обязан их канонизировать.

    Историю не переписываем: ревизия и дальше говорит `"old": "pending"`.
    Канонизировать её нечем и незачем — это запись о том, что было правдой
    тогда. Приземлиться реверт должен в `todo`.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        row_id = _make_task(session, "CA-007")
        tasks.complete(session, task_id="CA-007", author="human:test")
        model = session.get(TaskModel, row_id)
        assert model is not None
        historical = rev.write(
            session,
            project_id=model.project_id,
            entity_kind=EntityKind.TASK,
            entity_id=row_id,
            author="human:test",
            diff=json.dumps({"op": "status", "old": "pending", "new": "done"}),
            reason="ревизия из до-канонической эпохи",
        )

        rev.revert(session, historical.revision_id, author="human:test")

        assert _raw_status(session, "CA-007") == TaskStatus.TODO.value


# --------------------------------------------------------------------------- #
# 2. Инвариант по всей БД                                                      #
# --------------------------------------------------------------------------- #


def test_no_write_path_leaves_a_legacy_spelling_in_the_column(engine_with_schema) -> None:  # type: ignore[no-untyped-def]
    """Прогнать все пути подряд и посмотреть на колонку целиком.

    Множество запрещённых строк берётся из `TASK_STATUS_ALIASES`, поэтому
    новый алиас покрывается этим тестом без единой правки здесь.

    `expected_status_at_checkout` в инвариант не входит намеренно: это
    снимок статуса ДО чекаута, и у строки из немигрированной БД он
    легитимно легаси.
    """
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as session:
        _make_task(session, "CA-010")
        _make_task(session, "CA-011", raw_status="backlog")
        tasks.update_status(
            session, task_id="CA-011", new_status=TaskStatus.PENDING, author="human:test"
        )
        _make_task(session, "CA-012")
        checkout.checkout(session, "CA-012", agent="agent:test")
        _make_task(session, "CA-013")
        tasks.complete(session, task_id="CA-013", author="human:test")

        leaked = _stored_statuses(session) & set(TASK_STATUS_ALIASES)
        assert not leaked, f"в колонку просочилось легаси-написание: {sorted(leaked)}"


# --------------------------------------------------------------------------- #
# 3. AST: новый write-путь с легаси-константой                                 #
# --------------------------------------------------------------------------- #

#: Легаси-члены `TaskStatus` — выводятся из карты, а не перечисляются руками.
_LEGACY_MEMBERS: frozenset[str] = frozenset(
    member.name for member in TaskStatus if member.value in TASK_STATUS_ALIASES
)

#: Аргументы, которые несут статус задачи в сервисах.
_STATUS_KEYWORDS: frozenset[str] = frozenset({"status", "new_status"})

#: Разрешённые записи: (файл относительно `cod_doc/`, функция) →
#: обоснование. Ratchet: устаревшая запись роняет тест, поэтому список может
#: только сокращаться.
ALLOWED_LEGACY_WRITES: dict[tuple[str, str], str] = {
    ("core/project.py", "from_dict"): (
        "Другой `TaskStatus` — пятизначный StrEnum из `core/project.py` "
        "поверх `tasks.yaml` (pending | in_progress | done | failed | "
        "blocked). Колонки `task.status` не касается вовсе, а `in_progress` "
        "там уже каноническое написание. Страж сработал на совпадение имени "
        "члена: AST не отличает два одноимённых enum'а."
    ),
    ("agent/orchestrator.py", "run_task"): (
        "То же: оркестратор ходит в `core/project.py::Project.update_task`, "
        "то есть в YAML, а не в БД. См. запись про `core/project.py`."
    ),
    ("services/approval_service.py", "request"): (
        "Это `approval.status`, а не статус задачи: свой словарь из пяти "
        "значений ('pending' | 'approved' | 'denied' | 'cancelled' | "
        "'expired', `approval_service.VALID_STATUSES`), где `pending` "
        "каноническое и алиаса не имеет. Совпало только слово."
    ),
}


def _bare_legacy(node: ast.AST) -> str | None:
    """Имя легаси-константы, если узел — именно она, без обхода вглубь."""
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    if isinstance(node, ast.Constant) and node.value in TASK_STATUS_ALIASES:
        return repr(node.value)
    if (
        isinstance(node, ast.Attribute)
        and node.attr in _LEGACY_MEMBERS
        and isinstance(node.value, ast.Name)
        and node.value.id == "TaskStatus"
    ):
        return f"TaskStatus.{node.attr}"
    return None


def _legacy_literal(node: ast.AST) -> str | None:
    """Легаси-константа где угодно внутри выражения, а не только на верхнем уровне.

    Проверка верхнего узла пропускала ровно тот код, ради которого страж и
    писался: `checkout_service` месяцами нёс

        m.status = "in-progress" if was_legacy_pending else "in_progress"

    — это `ast.IfExp`, и точное сравнение по типу узла давало ноль попаданий.
    Поэтому обходим всё поддерево значения.

    Чего этот страж по-прежнему не видит — и это честнее записать, чем
    делать вид, что видит: запись через промежуточное имя
    (`m.status = spelling`) или через индекс (`m.status = MAP[k]`). Такую
    подмену ловит не AST, а инвариант по всей БД в
    :func:`test_no_legacy_spelling_survives_any_write_path`.
    """
    direct = _bare_legacy(node)
    if direct is not None:
        return direct
    for child in ast.walk(node):
        if child is node:
            continue
        nested = _bare_legacy(child)
        if nested is not None:
            return nested
    return None


class _LegacyStatusWriteFinder(ast.NodeVisitor):
    """Собирает `x.status = <легаси>` и `f(status=<легаси>)` с именем функции."""

    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.scope: list[str] = []
        self.hits: list[tuple[tuple[str, str], str, int]] = []

    def _enter(self, node: ast.AST, name: str) -> None:
        self.scope.append(name)
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter(node, node.name)

    def _record(self, what: str, lineno: int) -> None:
        scope = self.scope[-1] if self.scope else "<module>"
        self.hits.append(((self.relative_path, scope), what, lineno))

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr == "status":
                literal = _legacy_literal(node.value)
                if literal is not None:
                    self._record(f".status = {literal}", node.lineno)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        for keyword in node.keywords:
            if keyword.arg in _STATUS_KEYWORDS:
                literal = _legacy_literal(keyword.value)
                if literal is not None:
                    self._record(f"{keyword.arg}={literal}", node.lineno)
        self.generic_visit(node)


def _scan_services() -> list[tuple[tuple[str, str], str, int]]:
    hits: list[tuple[tuple[str, str], str, int]] = []
    for path in sorted(COD_DOC_DIR.rglob("*.py")):
        relative = str(path.relative_to(COD_DOC_DIR))
        finder = _LegacyStatusWriteFinder(relative)
        finder.visit(ast.parse(path.read_text(encoding="utf-8")))
        hits.extend(finder.hits)
    return hits


def test_no_service_writes_a_legacy_status_constant() -> None:
    """Новый write-путь с `pending` / `in-progress` — регресс, а не стиль."""
    unexpected = [hit for hit in _scan_services() if hit[0] not in ALLOWED_LEGACY_WRITES]
    assert not unexpected, "легаси-константа статуса в write-пути:\n" + "\n".join(
        f"  cod_doc/{key[0]}:{lineno} в {key[1]}(): {what}" for key, what, lineno in unexpected
    )


def test_legacy_write_allowlist_is_current() -> None:
    """Ratchet: запись, которой в коде больше нет, обязана уйти из списка."""
    live = {hit[0] for hit in _scan_services()}
    stale = sorted(set(ALLOWED_LEGACY_WRITES) - live)
    assert not stale, f"устаревшие записи в ALLOWED_LEGACY_WRITES: {stale}"


def test_every_allowlist_entry_carries_a_justification() -> None:
    """Запись без обоснования — это не исключение, а необъяснённый долг."""
    thin = sorted(key for key, why in ALLOWED_LEGACY_WRITES.items() if len(why.strip()) < 40)
    assert not thin, f"обоснование слишком короткое: {thin}"
