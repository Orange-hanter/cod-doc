"""Ссылка `kind:key` ↔ `uid` строки (RFC 39 §3.1).

Конец ребра хранится как `uid`: он не меняется при `doc_rename`, смене якоря и
закреплении провизорного ID. Этот модуль переводит публичный ключ в `uid`
(`resolve`, `resolve_many`) и обратно (`display_refs`) — один запрос на вид.

Лист: импортирует только домен, модели и sqlalchemy, других сервисов не знает
(иначе кольцо SVC-006). От `entity_ref_service` (адресация журналов по
`row_id`) отличается направлением и видами; переиспользовать его нельзя —
у него другой ключ (`row_id`) и свой `EntityRef`.

Публичный ключ по видам:

| вид | ключ |
|---|---|
| task, story, adr, question, scenario | `task_id`, `story_id`, `adr_id`, `question_id`, `scenario_id` |
| criterion | `<story_id>#<position>` (как `scenario_link.criterion`) |
| document | `doc_key` |
| section | `<doc_key>#<anchor>` |
| plan | `scope` |
| plan_section | `<scope>#<letter>` |
| finding | `finding_uid` (отпечаток не годится: уникален только внутри источника) |
| module | `module_id` |
| code, url | не строки БД: `ValueError` |

Закреплённый провизорный ID ищется только по текущему ключу: алиасы
(`leases.current_id`) — это `sync_service`, а лист его не импортирует.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import String, cast, select

from cod_doc.domain.relations import ENTITY_KINDS, EntityRef, RefKind
from cod_doc.infra.models import (
    ADRModel,
    DocumentModel,
    FindingModel,
    ModuleModel,
    OpenQuestionModel,
    PlanModel,
    PlanSectionModel,
    ScenarioModel,
    SectionModel,
    StoryAcceptanceModel,
    TaskModel,
    UserStoryModel,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

# Нарратив истории длинный; в заголовок идёт начало.
_STORY_TITLE_CHARS = 80

# Виды с собственным project_id: модель, ключ, заголовок.
_PLAIN: dict[RefKind, tuple[Any, Any, Any]] = {
    RefKind.TASK: (TaskModel, TaskModel.task_id, TaskModel.title),
    RefKind.STORY: (UserStoryModel, UserStoryModel.story_id, UserStoryModel.narrative),
    RefKind.ADR: (ADRModel, ADRModel.adr_id, ADRModel.title),
    RefKind.QUESTION: (OpenQuestionModel, OpenQuestionModel.question_id, OpenQuestionModel.title),
    RefKind.SCENARIO: (ScenarioModel, ScenarioModel.scenario_id, ScenarioModel.title),
    RefKind.PLAN: (PlanModel, PlanModel.scope, PlanModel.scope),
    RefKind.FINDING: (FindingModel, FindingModel.finding_uid, FindingModel.title),
    RefKind.MODULE: (ModuleModel, ModuleModel.module_id, ModuleModel.name),
    RefKind.DOCUMENT: (DocumentModel, DocumentModel.doc_key, DocumentModel.title),
}


class MissingUidError(RuntimeError):
    """У найденной строки нет `uid`: ребро не на что повесить (захват CLO-006 не прошёл)."""


@dataclass(frozen=True)
class ResolvedRef:
    kind: RefKind
    uid: str
    row_id: int
    key: str
    title: str


def _query(kind: RefKind, project_id: int) -> tuple[Any, Any, Any]:
    """`select(uid, row_id, key, title)` вида, выражение ключа и колонка uid."""
    if kind in _PLAIN:
        model, key, title = _PLAIN[kind]
        stmt = select(model.uid, model.row_id, key, title).where(model.project_id == project_id)
        return stmt, key, model.uid
    # У секции, критерия и секции плана своего project_id нет — он у родителя.
    if kind is RefKind.SECTION:
        key = DocumentModel.doc_key + "#" + SectionModel.anchor
        stmt = (
            select(SectionModel.uid, SectionModel.row_id, key, SectionModel.heading)
            .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
            .where(DocumentModel.project_id == project_id)
        )
        return stmt, key, SectionModel.uid
    if kind is RefKind.CRITERION:
        key = UserStoryModel.story_id + "#" + cast(StoryAcceptanceModel.position, String)
        stmt = (
            select(
                StoryAcceptanceModel.uid,
                StoryAcceptanceModel.row_id,
                key,
                StoryAcceptanceModel.criterion,
            )
            .join(UserStoryModel, UserStoryModel.row_id == StoryAcceptanceModel.story_id)
            .where(UserStoryModel.project_id == project_id)
        )
        return stmt, key, StoryAcceptanceModel.uid
    key = PlanModel.scope + "#" + PlanSectionModel.letter
    stmt = (
        select(PlanSectionModel.uid, PlanSectionModel.row_id, key, PlanSectionModel.title)
        .join(PlanModel, PlanModel.row_id == PlanSectionModel.plan_id)
        .where(PlanModel.project_id == project_id)
    )
    return stmt, key, PlanSectionModel.uid


def _fetch(
    session: Session,
    project_id: int,
    kind: RefKind,
    *,
    keys: set[str] | None = None,
    uids: set[str] | None = None,
) -> list[ResolvedRef]:
    stmt, key_expr, uid_col = _query(kind, project_id)
    if keys is not None:
        stmt = stmt.where(key_expr.in_(keys))
    if uids is not None:
        stmt = stmt.where(uid_col.in_(uids))
    out = []
    for uid, row_id, key, title in session.execute(stmt):
        if not uid:
            msg = f"{kind.value}:{key} (row_id={row_id}) без uid — ребро адресовать нечем"
            raise MissingUidError(msg)
        text = title[:_STORY_TITLE_CHARS] if kind is RefKind.STORY else title
        out.append(ResolvedRef(kind, uid, row_id, key, text))
    return out


def _check_entity(kind: RefKind) -> None:
    if kind not in ENTITY_KINDS:
        msg = f"{kind.value}: не строка БД, у неё нет uid (хранится литералом в to_key)"
        raise ValueError(msg)


def resolve_many(
    session: Session, project_id: int, refs: Iterable[EntityRef]
) -> dict[EntityRef, ResolvedRef]:
    """Найденные ссылки → `ResolvedRef`; ненайденных в ответе нет. Один запрос на вид."""
    by_kind: dict[RefKind, set[str]] = defaultdict(set)
    for ref in refs:
        _check_entity(ref.kind)
        by_kind[ref.kind].add(ref.key)
    found: dict[EntityRef, ResolvedRef] = {}
    for kind, keys in by_kind.items():
        for hit in _fetch(session, project_id, kind, keys=keys):
            found[EntityRef(kind, hit.key)] = hit
    return found


def resolve(session: Session, project_id: int, ref: EntityRef) -> ResolvedRef | None:
    """`None` — сущности нет в проекте; `ValueError` — `code`/`url` (не строки БД)."""
    return resolve_many(session, project_id, [ref]).get(ref)


def display_refs(
    session: Session, project_id: int, items: Iterable[tuple[RefKind, str]]
) -> dict[str, EntityRef]:
    """`uid` → текущий `kind:key` для вывода. Удалённых и чужого проекта в ответе нет."""
    by_kind: dict[RefKind, set[str]] = defaultdict(set)
    for kind, uid in items:
        _check_entity(kind)
        by_kind[kind].add(uid)
    return {
        hit.uid: EntityRef(kind, hit.key)
        for kind, uids in by_kind.items()
        for hit in _fetch(session, project_id, kind, uids=uids)
    }
