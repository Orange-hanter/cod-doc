"""Write-путь планов и их секций (RFC 26 §3.1, ADO-201).

До этого модуля секции и планы писались прямо из MCP-тулов через
``PlanSectionRepository.add()`` / ``PlanRepository.add()``, минуя services/:
ни ревизии, ни activity event, ни ``author`` — нарушение ADO-040. Здесь —
единственный write-путь: каждая реальная мутация пишет revision и activity
event в транзакции самой мутации. Адрес ревизии секции —
``EntityKind.PLAN_SECTION``: у ``plan_section`` своя нумерация ``row_id``.

Водораздел ``reason`` (RFC 26 §3.1): обязателен в ``update_section``,
``move_section`` и ``delete_section``; необязателен в ``create_plan`` и
``create_section`` — эти два тула уже зовут живые агенты, и обязательный
параметр сломал бы их вызовы.

Caller owns the transaction.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cod_doc.domain.entities import EntityKind, Plan
from cod_doc.infra.models import DocumentModel, PlanModel, PlanSectionModel, TaskModel
from cod_doc.infra.repositories import PlanRepository, PlanSectionRepository
from cod_doc.services import activity_service
from cod_doc.services import revision_service as rev
from cod_doc.services.validation import (
    validate_id_prefix,
    validate_plan_section_position,
    validate_plan_section_title,
    validate_section_slug,
)

from ._internals import build_section
from ._types import PlanNotFoundError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import PlanSection


class PlanAlreadyExistsError(ValueError):
    """План с таким scope уже есть — scope уникален на всю БД."""

    def __init__(self, scope: str) -> None:
        super().__init__(f"Plan with scope '{scope}' already exists.")
        self.scope = scope


class SectionNotFoundError(LookupError):
    """Секции с такой буквой в плане нет."""

    def __init__(self, letter: str) -> None:
        super().__init__(f"plan section not found: {letter!r}")
        self.letter = letter


class SectionAlreadyExistsError(ValueError):
    """Буква секции уже занята — она уникальна в пределах плана."""

    def __init__(self, letter: str) -> None:
        super().__init__(f"plan section already exists: {letter!r}")
        self.letter = letter


class SectionHasTasksError(ValueError):
    """Непустая секция удаляется только с ``reassign_to``.

    Флага ``force`` нет: ``task.section_id`` — NOT NULL + CASCADE, и удаление
    секции с задачами осиротило бы их revision и activity_event.
    """

    def __init__(self, letter: str, task_count: int) -> None:
        super().__init__(
            f"plan section {letter!r} still holds {task_count} task(s); pass reassign_to=<letter>"
        )
        self.letter = letter
        self.task_count = task_count


def _diff(op: str, **fields: object) -> str:
    return json.dumps({"op": op, **fields}, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# Мутации                                                                      #
# --------------------------------------------------------------------------- #


def create_plan(
    session: Session,
    *,
    project_id: int,
    scope: str,
    principle: str | None,
    author: str,
    reason: str | None = None,
    id_prefix: str | None = None,
) -> Plan:
    """Завести план. ``scope`` уникален на всю БД, а не в пределах проекта.

    ``id_prefix`` (ADO-243) закрепляет префикс ID новых задач плана
    (``WEB`` → ``WEB-001``). Без него префикс выводится из задач плана, а у
    пустого — из ``scope``; после переноса чужих задач в план
    (``task_service.move_tasks_to_plan``) такой вывод даёт их префикс, поэтому план,
    в который переносят, стоит заводить с явным префиксом.
    """
    repo = PlanRepository(session)
    if repo.get_by_scope(scope) is not None:
        raise PlanAlreadyExistsError(scope)
    if id_prefix is not None:
        validate_id_prefix(id_prefix)

    plan = repo.add(
        Plan(project_id=project_id, scope=scope, principle=principle, id_prefix=id_prefix)
    )
    session.flush()
    assert plan.row_id is not None

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.PLAN,
        entity_id=plan.row_id,
        author=author,
        diff=_diff("create_plan", scope=scope, principle=principle, id_prefix=id_prefix),
        reason=reason or "create_plan",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "plan.created",
        author,
        scope_kind=EntityKind.PLAN.value,
        scope_id=scope,
        payload={"scope": scope, "principle": principle, "id_prefix": id_prefix},
        summary=f"Plan {scope} created",
    )
    return plan


def create_section(
    session: Session,
    *,
    project_id: int,
    plan_scope: str,
    letter: str,
    title: str,
    author: str,
    slug: str | None = None,
    position: int | None = None,
    reason: str | None = None,
) -> PlanSection:
    """Добавить секцию в план. ``position`` по умолчанию — в конец списка.

    Секция собирается через ``build_section`` (ADO-199): буква приводится к
    верхнему регистру и проверяется вместе с заголовком, позицией и явным
    слагом; без слага он генерируется. Нарушение — ``ValidationError``.

    Advisory-предупреждения ``build_section`` (HTML-сущности в заголовке)
    функция не возвращает: их формирует поверхность через
    ``validation.audit_html_escaped_text``.
    """
    plan = _require_plan(session, project_id, plan_scope)
    repo = PlanSectionRepository(session)
    current = repo.list_for_plan(plan.row_id)
    if any(s.letter.upper() == letter.strip().upper() for s in current):
        raise SectionAlreadyExistsError(letter.strip().upper())

    section, _issues = build_section(
        plan_id=plan.row_id,
        letter=letter,
        title=title,
        slug=slug,
        position=position if position is not None else len(current),
    )
    created = repo.add(section)
    session.flush()
    assert created.row_id is not None

    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.PLAN_SECTION,
        entity_id=created.row_id,
        author=author,
        diff=_diff(
            "create_section",
            letter=created.letter,
            title=created.title,
            slug=created.slug,
            position=created.position,
        ),
        reason=reason or "create_section",
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "plan.section_created",
        author,
        scope_kind=EntityKind.PLAN_SECTION.value,
        scope_id=f"{plan_scope}:{created.letter}",
        payload={
            "plan_scope": plan_scope,
            "letter": created.letter,
            "title": created.title,
            "slug": created.slug,
            "position": created.position,
        },
        summary=f"Plan {plan_scope}: section {created.letter} created",
    )
    return created


def update_section(
    session: Session,
    *,
    project_id: int,
    plan_scope: str,
    letter: str,
    author: str,
    reason: str,
    title: str | None = None,
    slug: str | None = None,
    doc_id: int | None = None,
    adopt: bool = False,
) -> PlanSection:
    """Поправить секцию. ``None`` означает «не трогать это поле».

    Идемпотентно: вызов, который ничего не меняет, не пишет ни ревизию, ни
    событие. ``reason`` обязателен (RFC 26 §4): пустой — ``ValueError``.

    ``doc_id`` привязывает секцию к документу того же проекта; чужой или
    несуществующий документ — ``ValueError``. Снять привязку этой функцией
    нельзя: ``None`` значит «не трогать», а не «обнулить».

    ``adopt=True`` берёт в историю секцию, созданную до ADO-201 мимо ревизий
    (живая секция J), чтобы у неё появился head: ревизия пишется, даже если
    менять нечего, данные секции при этом не трогаются, а в diff ложится
    снимок полей. В сочетании с изменениями пишется одна ревизия, а не две.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required for update_section (RFC 26 §4)")
    if title is not None:
        validate_plan_section_title(title)
    if slug is not None:
        slug = slug.strip()
        validate_section_slug(slug)

    plan = _require_plan(session, project_id, plan_scope)
    model = _require_section(session, plan.row_id, letter)
    if doc_id is not None:
        doc = session.get(DocumentModel, doc_id)
        if doc is None or doc.project_id != project_id:
            raise ValueError(f"document #{doc_id} not found in project")

    changes: dict[str, object] = {}
    if title is not None and title != model.title:
        changes["title"] = {"from": model.title, "to": title}
        model.title = title
    if slug is not None and slug != model.slug:
        changes["slug"] = {"from": model.slug, "to": slug}
        model.slug = slug
    if doc_id is not None and doc_id != model.doc_id:
        changes["doc_id"] = {"from": model.doc_id, "to": doc_id}
        model.doc_id = doc_id

    repo = PlanSectionRepository(session)
    if not changes and not adopt:
        current = repo.get(model.row_id)
        assert current is not None
        return current

    session.flush()
    extra: dict[str, object] = {}
    if adopt:
        extra["snapshot"] = {
            "title": model.title,
            "slug": model.slug,
            "position": model.position,
            "doc_id": model.doc_id,
        }
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.PLAN_SECTION,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "update_section",
            plan_scope=plan_scope,
            letter=model.letter,
            changes=changes,
            adopted=adopt,
            **extra,
        ),
        reason=reason,
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "plan.section_updated",
        author,
        scope_kind=EntityKind.PLAN_SECTION.value,
        scope_id=f"{plan_scope}:{model.letter}",
        payload={
            "plan_scope": plan_scope,
            "letter": model.letter,
            "changed": sorted(changes),
            "adopted": adopt,
        },
        summary=f"Plan {plan_scope}: section {model.letter} updated",
    )
    updated = repo.get(model.row_id)
    assert updated is not None
    return updated


def move_section(
    session: Session,
    *,
    project_id: int,
    plan_scope: str,
    letter: str,
    author: str,
    reason: str,
    before: str | None = None,
    after: str | None = None,
    position: int | None = None,
) -> list[PlanSection]:
    """Переставить секцию; вернуть новый порядок секций плана целиком.

    Ровно один из ``before`` / ``after`` / ``position``, иначе ``ValueError``.
    ``before`` / ``after`` — буква другой секции того же плана: своя буква —
    ``ValueError``, неизвестная — ``SectionNotFoundError``. ``position``
    проходит ``validate_plan_section_position``; больше ``n-1`` — зажимается в
    конец.

    Позиция перестаёт быть полем, которое выставляют руками: после вызова все
    секции плана получают плотный порядок ``0..n-1``, а приём ``position=-1``
    закрыт (RFC 26 §3.1). Текущий порядок — сортировка по ``(position,
    row_id)``, поэтому легаси-позиции ``-1`` и дубли встают детерминированно и
    нормализуются этим же вызовом.

    Ревизия пишется по каждой секции, чья позиция реально изменилась; событие
    ``plan.section_moved`` — одно на вызов. Ничего не сдвинулось — ни ревизий,
    ни события. ``reason`` обязателен: пустой — ``ValueError``.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required for move_section (RFC 26 §4)")
    given = [arg is not None for arg in (before, after, position)]
    if sum(given) != 1:
        raise ValueError("move_section needs exactly one of before, after, position")
    if position is not None:
        validate_plan_section_position(position)

    plan = _require_plan(session, project_id, plan_scope)
    target = _require_section(session, plan.row_id, letter)
    anchor_letter = before if before is not None else after
    anchor: PlanSectionModel | None = None
    if anchor_letter is not None:
        if anchor_letter.strip().upper() == target.letter.upper():
            raise ValueError(f"section {target.letter!r} cannot be moved relative to itself")
        anchor = _require_section(session, plan.row_id, anchor_letter)

    ordered = list(
        session.execute(
            select(PlanSectionModel)
            .where(PlanSectionModel.plan_id == plan.row_id)
            .order_by(PlanSectionModel.position, PlanSectionModel.row_id)
        ).scalars()
    )
    ordered.remove(target)
    if anchor is not None:
        index = ordered.index(anchor) + (1 if after is not None else 0)
    else:
        assert position is not None
        index = min(position, len(ordered))
    ordered.insert(index, target)

    moved: list[tuple[PlanSectionModel, int, int]] = []
    for new_position, model in enumerate(ordered):
        if model.position != new_position:
            moved.append((model, model.position, new_position))
            model.position = new_position

    if moved:
        session.flush()
        for model, old, new in moved:
            rev.write(
                session,
                project_id=project_id,
                entity_kind=EntityKind.PLAN_SECTION,
                entity_id=model.row_id,
                author=author,
                diff=_diff(
                    "move_section",
                    plan_scope=plan_scope,
                    letter=model.letter,
                    position={"from": old, "to": new},
                    moved=target.letter,
                ),
                reason=reason,
            )
        activity_service.emit_for_write(
            session,
            project_id,
            "plan.section_moved",
            author,
            scope_kind=EntityKind.PLAN_SECTION.value,
            scope_id=f"{plan_scope}:{target.letter}",
            payload={
                "plan_scope": plan_scope,
                "letter": target.letter,
                "order": [m.letter for m in ordered],
            },
            summary=f"Plan {plan_scope}: section {target.letter} moved",
        )

    repo = PlanSectionRepository(session)
    result = []
    for model in ordered:
        section = repo.get(model.row_id)
        assert section is not None
        result.append(section)
    return result


def delete_section(
    session: Session,
    *,
    project_id: int,
    plan_scope: str,
    letter: str,
    author: str,
    reason: str,
    reassign_to: str | None = None,
) -> int:
    """Удалить секцию; вернуть число перенесённых задач.

    Секция без задач удаляется сразу. Задачи есть — нужен ``reassign_to``,
    буква другой секции того же плана (своя — ``ValueError``, неизвестная —
    ``SectionNotFoundError``); без него — ``SectionHasTasksError``. Задачи
    переезжают сменой ``task.section_id``, ``plan_id`` остаётся прежним.

    Флага ``force`` нет намеренно: ``task.section_id`` — NOT NULL +
    ``ondelete=CASCADE`` (``infra/models/plans.py``), и force снёс бы задачи,
    их рёбра и affected_file, а их revision и activity_event остались бы
    сиротами на мёртвых ``entity_id``.

    Ревизия пишется до удаления и несёт снимок исчезающей сущности. Оставшиеся
    секции не перенумеровываются: плотность порядка — забота ``move_section``.
    ``reason`` обязателен: пустой — ``ValueError``.
    """
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason is required for delete_section (RFC 26 §4)")

    plan = _require_plan(session, project_id, plan_scope)
    model = _require_section(session, plan.row_id, letter)
    target: PlanSectionModel | None = None
    if reassign_to is not None:
        if reassign_to.strip().upper() == model.letter.upper():
            raise ValueError(f"section {model.letter!r} cannot be reassigned to itself")
        target = _require_section(session, plan.row_id, reassign_to)

    tasks = list(
        session.execute(
            select(TaskModel).where(TaskModel.section_id == model.row_id).order_by(TaskModel.row_id)
        ).scalars()
    )
    if tasks and target is None:
        raise SectionHasTasksError(model.letter, len(tasks))

    moved_task_ids = [t.task_id for t in tasks]
    if target is not None:
        for task in tasks:
            task.section_id = target.row_id
    session.flush()
    # Иначе delete-orphan на relationship ``tasks`` унёс бы уже перенесённые
    # задачи по устаревшей коллекции.
    session.expire(model, ["tasks"])

    target_letter = target.letter if target is not None else None
    rev.write(
        session,
        project_id=project_id,
        entity_kind=EntityKind.PLAN_SECTION,
        entity_id=model.row_id,
        author=author,
        diff=_diff(
            "delete_section",
            plan_scope=plan_scope,
            letter=model.letter,
            title=model.title,
            slug=model.slug,
            position=model.position,
            reassign_to=target_letter,
            moved_task_ids=moved_task_ids,
        ),
        reason=reason,
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "plan.section_deleted",
        author,
        scope_kind=EntityKind.PLAN_SECTION.value,
        scope_id=f"{plan_scope}:{model.letter}",
        payload={
            "plan_scope": plan_scope,
            "letter": model.letter,
            "reassign_to": target_letter,
            "moved": len(moved_task_ids),
        },
        summary=f"Plan {plan_scope}: section {model.letter} deleted",
    )
    session.delete(model)
    session.flush()
    return len(moved_task_ids)


def _require_plan(session: Session, project_id: int, plan_scope: str) -> PlanModel:
    # PlanRepository.get_by_scope скоупа проекта не знает: scope уникален на
    # БД, и без сверки project_id чужой план резолвился бы из любого проекта.
    # Текст ошибки одинаковый в обоих случаях — чужой план не раскрывается.
    model = session.execute(
        select(PlanModel).where(PlanModel.scope == plan_scope)
    ).scalar_one_or_none()
    if model is None or model.project_id != project_id:
        raise PlanNotFoundError(f"Plan '{plan_scope}' not found in project")
    return model


def _require_section(session: Session, plan_id: int, letter: str) -> PlanSectionModel:
    normalized = letter.strip().upper()
    model = session.execute(
        select(PlanSectionModel).where(
            PlanSectionModel.plan_id == plan_id,
            func.upper(PlanSectionModel.letter) == normalized,
        )
    ).scalar_one_or_none()
    if model is None:
        raise SectionNotFoundError(normalized)
    return model
