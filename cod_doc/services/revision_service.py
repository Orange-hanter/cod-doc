"""RevisionService — append-only history for any entity.

DATA_MODEL §3.5: `revision_id` is a ULID (sortable by time); `parent_revision_id`
chains revisions of the same entity for optimistic concurrency control.

Public API:
- `write` — append a new revision; auto-fills `revision_id` (ULID) and
  `parent_revision_id` (last revision of the same entity, if any).
- `list_for_entity` — full history of an entity, oldest → newest.
- `append_foreign` — ревизия из чужого changeset'а (RFC 29 §3.7, Q-010;
  CLO-024): свои `revision_id`/`author`/`reason`/`at`, родитель — голова
  сущности на этой реплике.

**Голова сущности — последняя записанная на реплике** (`row_id DESC`), а не
самая поздняя по `at`. На одной реплике это одно и то же: `at` — время ULID
в момент записи. После синхронизации — нет: чужая ревизия могла быть
написана раньше своей, ещё не уехавшей, но встаёт поверх неё (Q-010), и
история на реплике остаётся линейной. По `at` голова откатилась бы на свою
ревизию, следующая запись получила бы того же родителя, что и чужая, а
устаревший `expected_parent_revision_id` прошёл бы проверку.

Откат ревизии (`revert`, COD-022) живёт в `revert_service` (SVC-002): он
вызывает сервисы-владельцы сущностей, а они сами пишут сюда ревизии. Пока
откат лежал здесь, журнал ревизий зависел от doc/task/task_doc и замыкал их
в кольцо. `RevertNotSupportedError` и `RevisionNotFoundError` остаются здесь —
это ошибки журнала, их ловят поверхности.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import func, select
from ulid import ULID

from cod_doc.domain.entities import EntityKind, Revision
from cod_doc.infra.models import RevisionModel
from cod_doc.infra.sync_capture import set_author
from cod_doc.services.run_id import get_current_run_id

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import InstrumentedAttribute, Session


class RevisionConflictError(RuntimeError):
    """Raised when `expected_parent_revision_id` does not match the current head.

    Mirrors DATA_MODEL §3.5: writers may pass the parent ULID they last observed;
    if a concurrent writer landed first, the head moved and we refuse the write.
    """


#: Имя из ARCHITECTURE §12.1 и RFC 29 §3.7 (Q-010): тот же класс, не подкласс —
#: поверхности ловят `RevisionConflictError`, и оба имени ловят одно и то же.
OptimisticLockError = RevisionConflictError


# Sentinel: caller didn't pass a parent expectation. Distinct from `None`,
# which is a *valid* expectation ("I expect this is the first revision").
# Public so that other services can use it as a default parameter value.
NO_PARENT_CHECK: Final = object()
# Keep the underscore alias for backward compatibility within this module.
_NO_PARENT_CHECK = NO_PARENT_CHECK


def _to_domain(model: RevisionModel) -> Revision:
    return Revision(
        row_id=model.row_id,
        revision_id=model.revision_id,
        project_id=model.project_id,
        entity_kind=EntityKind(model.entity_kind),
        entity_id=model.entity_id,
        parent_revision_id=model.parent_revision_id,
        author=model.author,
        diff=model.diff,
        at=model.at,
        reason=model.reason,
        commit_sha=model.commit_sha,
        run_id=model.run_id,
    )


def _current_head(
    session: Session, entity_kind: EntityKind, entity_id: int, project_id: int
) -> str | None:
    """Latest `revision_id` for the entity, or None if no revisions yet.

    Единственный запрос «head сущности»: и для ``write`` (родитель новой
    ревизии, проект — у пишущего), и для публичного `head_for_entity`,
    который сужает его проектом вызывающего (AFT-023).
    """
    stmt = select(RevisionModel.revision_id).where(
        RevisionModel.entity_kind == entity_kind.value,
        RevisionModel.entity_id == entity_id,
        RevisionModel.project_id == project_id,
    )
    stmt = stmt.order_by(RevisionModel.row_id.desc()).limit(1)
    return session.execute(stmt).scalar_one_or_none()


def write(
    session: Session,
    *,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    diff: str,
    reason: str | None = None,
    commit_sha: str | None = None,
    expected_parent_revision_id: str | object | None = _NO_PARENT_CHECK,
) -> Revision:
    """Append a revision row.

    `parent_revision_id` is auto-derived from the entity's current head.
    If `expected_parent_revision_id` is passed (including explicit `None` for
    "I expect to be the first writer"), it must equal the current head, or
    `RevisionConflictError` is raised — this is the optimistic concurrency hook.
    """
    # RFC 29 §10.3 (CLO-006): автор транзакции для операций ``sync_op``.
    set_author(session, author)
    head = _current_head(session, entity_kind, entity_id, project_id)
    if expected_parent_revision_id is not _NO_PARENT_CHECK and head != expected_parent_revision_id:
        raise RevisionConflictError(
            f"head moved: expected parent={expected_parent_revision_id!r}, "
            f"actual={head!r} for {entity_kind.value} #{entity_id}"
        )

    rid_obj = ULID.from_datetime(datetime.now(UTC))
    # PCA-031: stamp the active run_id (set by Orchestrator.run_task or
    # tests via run_context.run_scope). NULL = human / external mutation.
    model = RevisionModel(
        revision_id=str(rid_obj),
        project_id=project_id,
        entity_kind=entity_kind.value,
        entity_id=entity_id,
        parent_revision_id=head,
        author=author,
        at=rid_obj.datetime,
        diff=diff,
        reason=reason,
        commit_sha=commit_sha,
        run_id=get_current_run_id(),
    )
    session.add(model)
    session.flush()
    return _to_domain(model)


def append_foreign(
    session: Session,
    *,
    revision_id: str,
    project_id: int,
    entity_kind: EntityKind,
    entity_id: int,
    author: str,
    at: datetime,
    diff: str,
    reason: str | None = None,
    commit_sha: str | None = None,
) -> Revision:
    """Ревизия из чужого changeset'а поверх локальной головы (RFC 29 §3.7, Q-010).

    `revision_id`, `author`, `reason`, `at` — как у источника: ревизия одна на
    всех репликах, на неё ссылаются `approval_doc_revision_link` и
    `task_document.current_revision_id`. `parent_revision_id` — голова
    сущности на этой реплике в момент применения: история линейна, и
    оптимистическая блокировка работает как на одной реплике. `run_id` — NULL:
    прогон оркестратора — телеметрия чужой машины (ADR-012).

    Автора транзакции (`set_author`) не ставит: применение идёт под
    `sync_capture.suspended`, операций не пишет.
    """
    model = RevisionModel(
        revision_id=revision_id,
        project_id=project_id,
        entity_kind=entity_kind.value,
        entity_id=entity_id,
        parent_revision_id=_current_head(session, entity_kind, entity_id, project_id),
        author=author,
        at=at,
        diff=diff,
        reason=reason,
        commit_sha=commit_sha,
        run_id=None,
    )
    session.add(model)
    session.flush()
    return _to_domain(model)


#: Превью diff — первая строка, не длиннее этого.
DIFF_PREVIEW_CHARS = 200
#: Больше этого diff в превью не парсится: лента ревизий разбирает JSON на
#: каждой строке страницы, а тело секции в diff размера не ограничено.
DIFF_PREVIEW_PARSE_MAX = 64 * 1024


def diff_preview(diff: str | None, *, limit: int = DIFF_PREVIEW_CHARS) -> str:
    """Первая строка diff для ленты ревизий, с читаемой кириллицей.

    Сервисы пишут diff с ``ensure_ascii=False`` (ARG-010), но ревизии,
    записанные раньше, хранят ``\\u0425…`` вместо «Хранение». Такой
    JSON-объект или массив переписывается с ``ensure_ascii=False`` при
    показе; всё прочее — unified diff тела секции, JSON-скаляр, текст с
    обратным слешем — показывается байт-в-байт.

    Ветка переходная: новые ревизии escape-кодов не несут, а старые не
    переписываются — ревизия это история, а не данные для нормализации.
    """
    if not diff:
        return ""
    text = diff
    if len(diff) <= DIFF_PREVIEW_PARSE_MAX and diff.lstrip()[:1] in ("{", "[") and "\\u" in diff:
        try:
            parsed = json.loads(diff)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, (dict, list)):
            text = json.dumps(parsed, ensure_ascii=False)
    return text.splitlines()[0][:limit]


def _revision_in_project(
    session: Session, revision_id: str, project_id: int
) -> RevisionModel | None:
    """Строка ревизии в скоупе проекта — общий запрос `get` и `revert`."""
    return session.execute(
        select(RevisionModel).where(
            RevisionModel.revision_id == revision_id, RevisionModel.project_id == project_id
        )
    ).scalar_one_or_none()


def get(session: Session, revision_id: str, *, project_id: int) -> Revision | None:
    """Ревизия по ULID — только ревизия проекта ``project_id``.

    ``project_id`` обязателен и не принимает ``None`` (AFT-022): MCP
    ``revision_get`` и CLI ``revision show`` иначе отдавали бы diff, автора и
    причину чужого проекта общей hub-БД по одному лишь ULID.
    """
    model = _revision_in_project(session, revision_id, project_id)
    return _to_domain(model) if model is not None else None


def list_for_entity(
    session: Session,
    entity_kind: EntityKind,
    entity_id: int,
    *,
    limit: int | None = None,
    project_id: int,
) -> list[Revision]:
    """History for the entity, oldest → newest.

    ``project_id`` обязателен (AFT-023): ``entity_id`` — row_id, общий на всю
    hub-БД, и без фильтра перебором id читалась бы история чужого проекта.

    With `limit=None` (default) the full history is returned, exactly as
    before. With `limit` set, SQL fetches only the last `limit` revisions
    (ORDER BY at DESC, row_id DESC LIMIT limit) and the result is reversed
    back to oldest → newest — the caller gets the same tail that
    `[-limit:]` used to give, but without loading the whole history.
    """
    if limit is None:
        stmt = (
            select(RevisionModel)
            .where(
                RevisionModel.entity_kind == entity_kind.value,
                RevisionModel.entity_id == entity_id,
                RevisionModel.project_id == project_id,
            )
            .order_by(RevisionModel.at.asc(), RevisionModel.row_id.asc())
        )
        return [_to_domain(m) for m in session.execute(stmt).scalars()]

    stmt = (
        select(RevisionModel)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id == entity_id,
            RevisionModel.project_id == project_id,
        )
        .order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc())
        .limit(limit)
    )
    models = list(session.execute(stmt).scalars())
    models.reverse()
    return [_to_domain(m) for m in models]


def head_for_entity(
    session: Session,
    entity_kind: EntityKind,
    entity_id: int,
    *,
    project_id: int,
) -> str | None:
    """Return the current head revision_id for an entity, or None.

    Used by web write-paths for optimistic concurrency tokens — the form
    embeds this as a hidden `expected_parent_revision_id`, the handler
    passes it to `patch_section` (or similar), and a mid-flight write
    raises `RevisionConflictError`.

    ``project_id`` обязателен (AFT-023): ``entity_id`` — row_id на всю hub-БД,
    и head чужого проекта иначе выдавался бы как токен конкурентности.
    """
    return _current_head(session, entity_kind, entity_id, project_id)


def heads_for_entities(
    session: Session,
    entity_kind: EntityKind,
    entity_ids: Sequence[int],
    *,
    project_id: int,
) -> dict[int, str | None]:
    """Batch-вариант :func:`head_for_entity`: head каждой сущности одним SELECT.

    Порядок тот же, что у ``_current_head`` (row_id DESC — последняя записанная
    на реплике), head сущности — первая строка её группы. Ключи — все переданные id; у сущности
    без ревизий значение None. Пустой ``entity_ids`` — ``{}`` без запроса.
    """
    if not entity_ids:
        return {}
    heads: dict[int, str | None] = dict.fromkeys(entity_ids)
    stmt = (
        select(RevisionModel.revision_id, RevisionModel.entity_id)
        .where(
            RevisionModel.entity_kind == entity_kind.value,
            RevisionModel.entity_id.in_(heads),
            RevisionModel.project_id == project_id,
        )
        .order_by(RevisionModel.row_id.desc())
    )
    seen: set[int] = set()
    for revision_id, entity_id in session.execute(stmt):
        if entity_id not in seen:
            seen.add(entity_id)
            heads[entity_id] = revision_id
    return heads


def list_recent_for_project(
    session: Session,
    project_id: int,
    *,
    limit: int = 5,
) -> list[Revision]:
    """Most recent revisions across all entities of a project, newest → oldest.

    Used by the project overview dashboard (WEB-014). `RevisionModel` carries
    `project_id` directly, so this is a single index-supported query — no
    join chain through entity tables needed.
    """
    return list_for_project(session, project_id, limit=limit)


def list_for_project(
    session: Session,
    project_id: int,
    *,
    limit: int = 50,
    entity_kind: EntityKind | None = None,
    entity_id: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    author: str | None = None,
) -> list[Revision]:
    """Newest-first revisions of a project, optionally narrowed by filters.

    This is the project-wide revision feed for MCP and CLI (RFC 27 F9), not
    only for the web revisions log page (WEB-021). When both `entity_kind`
    and `entity_id` are passed, behaves like `list_for_entity` but order is
    flipped to newest-first to match the timeline UX. `since` filters to
    revisions with `at >= since`; datetimes are compared in UTC — callers
    pass values normalized by `task_service.parse_since`. `until` filters to
    revisions with `at <= until`. `author` filters by exact equality.
    """
    stmt = select(RevisionModel).where(RevisionModel.project_id == project_id)
    if entity_kind is not None:
        stmt = stmt.where(RevisionModel.entity_kind == entity_kind.value)
    if entity_id is not None:
        stmt = stmt.where(RevisionModel.entity_id == entity_id)
    if since is not None:
        stmt = stmt.where(RevisionModel.at >= since)
    if until is not None:
        stmt = stmt.where(RevisionModel.at <= until)
    if author is not None:
        stmt = stmt.where(RevisionModel.author == author)
    stmt = stmt.order_by(RevisionModel.at.desc(), RevisionModel.row_id.desc()).limit(limit)
    return [_to_domain(m) for m in session.execute(stmt).scalars()]


REVISION_SUMMARY_GROUP_BY: tuple[str, ...] = ("day", "entity_kind", "author")

_SIMPLE_GROUP_COLUMNS: dict[str, InstrumentedAttribute[str]] = {
    "entity_kind": RevisionModel.entity_kind,
    "author": RevisionModel.author,
}


def summarize(
    session: Session,
    project_id: int,
    *,
    since: datetime,
    until: datetime | None = None,
    group_by: Sequence[str],
) -> list[dict[str, Any]]:
    """Aggregate revisions with a single GROUP BY query (RFC 27 F9, N7).

    Находка N7 аудита agent-fit (AFT-008): «ревизии за сегодня по видам
    сущностей» — один вызов вместо выгрузки ленты и группировки на клиенте,
    как ``activity_service.summarize`` для событий. Фильтры совпадают с
    :func:`list_for_project` (``project_id``/``since``/``until``), поэтому
    сумма ``n`` равна длине ленты за тот же период при достаточном limit.
    Функция только читает: revision/activity не пишет.
    """
    if (
        not group_by
        or len(set(group_by)) != len(group_by)
        or any(key not in REVISION_SUMMARY_GROUP_BY for key in group_by)
    ):
        allowed = ", ".join(REVISION_SUMMARY_GROUP_BY)
        raise ValueError(f"invalid group_by {list(group_by)!r}; допустимые ключи: {allowed}")

    columns: list[Any] = []
    for key in group_by:
        if key == "day":
            columns.append(func.date(RevisionModel.at).label("day"))
        else:
            columns.append(_SIMPLE_GROUP_COLUMNS[key])

    stmt = (
        select(*columns, func.count().label("n"))
        .where(RevisionModel.project_id == project_id)
        .where(RevisionModel.at >= since)
    )
    if until is not None:
        stmt = stmt.where(RevisionModel.at <= until)
    stmt = stmt.group_by(*columns).order_by(*columns)

    result: list[dict[str, Any]] = []
    for row in session.execute(stmt).mappings():
        item: dict[str, Any] = {}
        for key in group_by:
            value = row[key]
            item[key] = str(value) if key == "day" else value
        item["n"] = int(row["n"])
        result.append(item)
    return result


class RevertNotSupportedError(NotImplementedError):
    """Raised for revision ops or entity kinds that cannot be auto-reverted."""


class RevisionNotFoundError(LookupError):
    """Ревизии с таким ULID нет в проекте вызывающего.

    Отдельный подкласс, чтобы поверхности отличали «нет ревизии» от «ревизия
    есть, но её сущность удалена или лежит в другом проекте» — второе тоже
    `LookupError`, и его текст не должен подменяться на «not found».
    """
