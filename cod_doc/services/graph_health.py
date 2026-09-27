"""Здоровье графа задач: циклы, немые и мёртвые рёбра, кривые секции (RFC 26 §5.3).

Что проверяется:

- **циклы** по рёбрам ``kind='blocks'`` — ``plan_service.audit`` считает их
  только по запросу и только в отчёт; здесь тот же ``_find_cycles``
  (переиспользование, не копия) превращается в находку;
- **немые рёбра** — ``dependency.note`` пуст: ребро без мотивации нельзя ни
  проверить, ни снять осознанно (ADO-202 требует ``note`` на новых рёбрах,
  старые предъявляет эта рутина). Только пока зависимая задача не закрыта:
  у закрытой ребро уже ничего не держит, и мотивировать его задним числом —
  работа без пользы. Замер на живой БД 2026-09-27: 129 из 147 немых рёбер
  висели на ``done``/``cancelled`` и забили бы очередь ``curator_next``;
- **мёртвые рёбра** — блокер закрыт дольше :data:`DEAD_EDGE_AGE`, а зависимая
  задача так и не начата: ребро больше ничего не держит, а очередь его помнит;
- **кросс-плановые рёбра** — задачи разных планов: законны, но аудит плана их
  не видит, поэтому их надо видеть здесь;
- **секции** — позиции плана вне ``0..n-1`` (дубли, пропуски, легаси ``-1``)
  и слаги вне конвенции ``validate_section_slug``.

Модуль чистый в той части, которая решает: ``assess_*`` берут уже прочитанные
строки и не знают про ``Session``. Сборка данных — в :func:`assess`, запись
находок — в :func:`sync`.

**Почему модуль в ``services/``, а не в пакете ``plan_service``.** :func:`sync`
пишет находки, и сканер паритета поверхностей
(``tests/services/test_plan_mutation_surface_parity.py``,
``test_discovered_mutations_exact``) опознал бы его как мутацию
``plan_service`` и потребовал бы своих MCP- и CLI-тулов. RFC 26 §5.3 прямо
говорит «отдельной поверхности не нужно»: результат виден через рутину
``graph_health`` и очередь ``curator_next``. Кто заводит следующую рутину по
образцу ``doc_node_health`` — не кладите её в пакет сервиса, который она
проверяет, иначе спеку паритета придётся заводить механически.

**Почему ``close_after_misses=1``.** Правила детерминированы: один и тот же
граф даёт один и тот же набор находок, промахов не бывает. Гистерезис нужен
LLM-партициям, где вердикт мигает; здесь он лишь задержал бы закрытие
вылеченного на прогон.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from cod_doc.domain.entities import TaskStatus
from cod_doc.services import activity_service, finding_service, task_status_machine
from cod_doc.services.activity_service import _uuid7
from cod_doc.services.finding_service import FindingSeed
from cod_doc.services.plan_service.audit import _find_cycles
from cod_doc.services.validation import ValidationError
from cod_doc.services.validation.structural import validate_section_slug

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

#: Сколько блокер должен пролежать закрытым, чтобы ребро считалось мёртвым.
#: Месяц — дольше любого спринта: за это время незапущенная зависимая задача
#: уже не «ждёт блокер», а просто забыта вместе с ребром.
DEAD_EDGE_AGE = timedelta(days=30)

BLOCKS = "blocks"

SCOPE_TASK = "task"
SCOPE_DEPENDENCY = "dependency"
SCOPE_PLAN = "plan"
SCOPE_SECTION = "plan_section"


@dataclass(frozen=True, slots=True)
class GraphIssue:
    """Одна проблема графа.

    ``code`` и ``scope_id`` идут в fingerprint находки, то есть это контракт:
    переименуешь код или сменишь форму ``scope_id`` — все открытые находки
    осиротеют, а вылеченные не закроются.
    """

    code: str
    scope_kind: str
    scope_id: str
    title: str
    body: str
    severity: str = "minor"


class EdgeRow(NamedTuple):
    """Ребро ``from_task → to_task``: ``from`` зависит от блокера ``to``."""

    from_row_id: int
    to_row_id: int
    from_task_id: str
    to_task_id: str
    kind: str
    note: str | None
    from_status: str
    to_status: str
    from_plan_id: int
    to_plan_id: int
    to_completed_at: datetime | None


class SectionRow(NamedTuple):
    plan_scope: str
    letter: str
    slug: str
    position: int


def assess_cycles(edges: Iterable[EdgeRow]) -> list[GraphIssue]:
    """Циклы по рёбрам ``blocks``; один цикл — одна находка."""
    adjacency: dict[int, list[int]] = {}
    task_id_by_row: dict[int, str] = {}
    for edge in edges:
        if edge.kind != BLOCKS:
            continue
        adjacency.setdefault(edge.from_row_id, []).append(edge.to_row_id)
        adjacency.setdefault(edge.to_row_id, [])
        task_id_by_row[edge.from_row_id] = edge.from_task_id
        task_id_by_row[edge.to_row_id] = edge.to_task_id

    issues: list[GraphIssue] = []
    for cycle in _find_cycles(adjacency):
        ids = [task_id_by_row[rid] for rid in cycle]
        # _find_cycles поворачивает по row_id; отпечаток обязан зависеть от
        # task_id, иначе пересоздание задачи сменило бы идентичность цикла.
        start = ids.index(min(ids))
        ids = ids[start:] + ids[:start]
        path = "→".join(ids)
        issues.append(
            GraphIssue(
                code="cycle",
                scope_kind=SCOPE_TASK,
                scope_id=path,
                title=f"Цикл зависимостей: {path}",
                body=(
                    f"Задачи {path} блокируют друг друга по кругу: ни одна из "
                    f"них не станет готовой. Снимите одно из рёбер."
                ),
                severity="major",
            )
        )
    return issues


def _as_utc(moment: datetime) -> datetime:
    """SQLite отдаёт naive datetime; в схеме всё хранится в UTC."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _is_dead(edge: EdgeRow, now: datetime) -> bool:
    if task_status_machine.normalise(edge.to_status) != TaskStatus.DONE.value:
        return False
    if edge.to_completed_at is None:
        return False
    if task_status_machine.normalise(edge.from_status) != TaskStatus.TODO.value:
        return False
    return _as_utc(now) - _as_utc(edge.to_completed_at) > DEAD_EDGE_AGE


def assess_edges(edges: Iterable[EdgeRow], *, now: datetime) -> list[GraphIssue]:
    """Правила одного ребра. Одно ребро может дать несколько находок разных кодов."""
    issues: list[GraphIssue] = []
    for edge in edges:
        scope_id = f"{edge.from_task_id}->{edge.to_task_id}:{edge.kind}"
        label = f"{edge.from_task_id} → {edge.to_task_id} ({edge.kind})"
        silent = edge.note is None or not edge.note.strip()
        if silent and not task_status_machine.is_terminal(edge.from_status):
            issues.append(
                GraphIssue(
                    code="edge_no_note",
                    scope_kind=SCOPE_DEPENDENCY,
                    scope_id=scope_id,
                    title=f"Немое ребро {label}",
                    body=(
                        "У ребра нет note: непонятно, зачем оно поставлено и когда "
                        "его можно снять. Мотивация пишется через task_add_dependency."
                    ),
                )
            )
        if _is_dead(edge, now):
            issues.append(
                GraphIssue(
                    code="edge_dead",
                    scope_kind=SCOPE_DEPENDENCY,
                    scope_id=scope_id,
                    title=f"Мёртвое ребро {label}",
                    body=(
                        f"Блокер {edge.to_task_id} закрыт дольше "
                        f"{DEAD_EDGE_AGE.days} дней, а {edge.from_task_id} так и не "
                        f"начата: ребро ничего не держит. Возьмите задачу или "
                        f"снимите ребро."
                    ),
                )
            )
        if edge.from_plan_id != edge.to_plan_id:
            issues.append(
                GraphIssue(
                    code="edge_cross_plan",
                    scope_kind=SCOPE_DEPENDENCY,
                    scope_id=scope_id,
                    title=f"Ребро между планами {label}",
                    body=(
                        "Задачи ребра лежат в разных планах: аудит плана его не "
                        "видит. Убедитесь, что зависимость настоящая."
                    ),
                )
            )
    return issues


def assess_sections(sections: Iterable[SectionRow]) -> list[GraphIssue]:
    """Позиции секций плана вне ``0..n-1`` и слаги вне конвенции. Только чтение."""
    issues: list[GraphIssue] = []
    positions_by_plan: dict[str, list[int]] = {}
    for section in sections:
        positions_by_plan.setdefault(section.plan_scope, []).append(section.position)
        try:
            validate_section_slug(section.slug)
        except ValidationError:
            issues.append(
                GraphIssue(
                    code="section_slug",
                    scope_kind=SCOPE_SECTION,
                    scope_id=f"{section.plan_scope}:{section.letter}",
                    title=(
                        f"Слаг секции {section.letter} плана {section.plan_scope} вне конвенции"
                    ),
                    body=(
                        f"Слаг {section.slug!r} не вида '<LETTER>-<KebabSlug>' "
                        f"(например 'A-Data-Core')."
                    ),
                )
            )

    for plan_scope, positions in sorted(positions_by_plan.items()):
        ordered = sorted(positions)
        if ordered != list(range(len(ordered))):
            issues.append(
                GraphIssue(
                    code="section_positions",
                    scope_kind=SCOPE_PLAN,
                    scope_id=plan_scope,
                    title=f"Позиции секций плана {plan_scope} вне 0..{len(ordered) - 1}",
                    body=(
                        f"Позиции секций: {ordered}. Ожидается перестановка "
                        f"0..{len(ordered) - 1} без дублей и пропусков."
                    ),
                )
            )
    return issues


def assess(session: Session, project_id: int, *, now: datetime | None = None) -> list[GraphIssue]:
    """Прочитать рёбра и секции проекта двумя запросами и посчитать проблемы."""
    from sqlalchemy import select
    from sqlalchemy.orm import aliased

    from cod_doc.infra.models import DependencyModel, PlanModel, PlanSectionModel, TaskModel

    src = aliased(TaskModel)
    dst = aliased(TaskModel)
    edges = [
        EdgeRow(*row)
        for row in session.execute(
            select(
                src.row_id,
                dst.row_id,
                src.task_id,
                dst.task_id,
                DependencyModel.kind,
                DependencyModel.note,
                src.status,
                dst.status,
                src.plan_id,
                dst.plan_id,
                dst.completed_at,
            )
            .join(src, DependencyModel.from_task_id == src.row_id)
            .join(dst, DependencyModel.to_task_id == dst.row_id)
            .where(src.project_id == project_id, dst.project_id == project_id)
            .order_by(src.task_id, dst.task_id, DependencyModel.kind)
        ).all()
    ]
    sections = [
        SectionRow(*row)
        for row in session.execute(
            select(
                PlanModel.scope,
                PlanSectionModel.letter,
                PlanSectionModel.slug,
                PlanSectionModel.position,
            )
            .join(PlanModel, PlanSectionModel.plan_id == PlanModel.row_id)
            .where(PlanModel.project_id == project_id)
            .order_by(PlanModel.scope, PlanSectionModel.letter)
        ).all()
    ]

    moment = now if now is not None else datetime.now(UTC)
    return assess_cycles(edges) + assess_edges(edges, now=moment) + assess_sections(sections)


# --------------------------------------------------------------------------- #
# Запись находок                                                               #
# --------------------------------------------------------------------------- #

#: ``source`` — словарь RFC 22 §3.2, внутренний производитель приходит как
#: ``routine``; ``source_ref`` отличает проверку и служит партицией
#: автозакрытия.
FINDING_SOURCE = "routine"
FINDING_SOURCE_REF = "graph_health"

#: Детерминированная партиция: закрываем с первого промаха (см. докстринг модуля).
CLOSE_AFTER_MISSES = 1

DEFAULT_AUTHOR = "routine:graph_health"


def _seed_for(issue: GraphIssue) -> FindingSeed:
    fingerprint, _basis = finding_service.fingerprint_routine(
        source=FINDING_SOURCE,
        check_name=issue.code,
        scope_kind=issue.scope_kind,
        scope_id=issue.scope_id,
    )
    return FindingSeed(
        fingerprint=fingerprint,
        source=FINDING_SOURCE,
        source_ref=FINDING_SOURCE_REF,
        kind=issue.code,
        title=issue.title,
        body=issue.body,
        severity=issue.severity,
        payload={"scope_kind": issue.scope_kind, "scope_id": issue.scope_id},
    )


def sync(
    session: Session,
    *,
    project_id: int,
    project_slug: str,
    now: datetime | None = None,
    author: str = DEFAULT_AUTHOR,
) -> dict[str, int]:
    """Посчитать проблемы графа и свести их с находками в БД.

    Идемпотентно: повторный прогон поднимает ``times_seen``, а не плодит
    строки. Вылеченное закрывается, вернувшееся переоткрывается — см.
    ``finding_service.reconcile_partition``.

    Отпечатки текущего прогона передаются в сверку **всегда, в том числе
    пустые**. В отличие от ``doc_node_health`` без дерева, здесь нет
    состояния «фича не заведена»: граф без рёбер и секций — это граф без
    проблем, и исчезновение всех проблем законно закрывает все находки.
    """
    issues = assess(session, project_id, now=now)
    seeds = [_seed_for(issue) for issue in issues]

    ingested = finding_service.ingest_findings(
        session,
        project_id=project_id,
        source_run_id=f"{FINDING_SOURCE_REF}:{_uuid7()}",
        seeds=seeds,
    )
    reconciled = finding_service.reconcile_partition(
        session,
        project_id=project_id,
        source=FINDING_SOURCE,
        source_ref=FINDING_SOURCE_REF,
        seen_fingerprints={seed.fingerprint for seed in seeds},
        author=author,
        close_after_misses=CLOSE_AFTER_MISSES,
    )

    result = {
        "issues": len(issues),
        "created": ingested.created,
        "updated": ingested.updated,
        "resolved": reconciled["resolved"],
        "reopened": reconciled["reopened"],
    }
    activity_service.emit_for_write(
        session,
        project_id,
        "plan.graph_health_synced",
        author,
        scope_kind="project",
        scope_id=project_slug,
        payload=result,
        summary=f"Graph health: {len(issues)} issue(s) for {project_slug}",
    )
    return result
