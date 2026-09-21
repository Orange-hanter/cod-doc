"""ADO-192: исполнитель автопочинки состояния проекта (фаза D команды ``update``).

Диагноз целиком делегирован :func:`cod_doc.services.curator_service.next` —
второй диагност завести нельзя, он обязан разъехаться с первым. Новое здесь
только исполнение: куратор кладёт на каждый пункт очереди готовую команду в
``suggested_action``, но сам ничего не делает.

Чинятся ровно четыре класса находок:

===================  =========================================================
``edited_in_place``  файл правлен на диске, БД отстала → ``import_document``
hash ``STALE``       реестр ``MASTER.md`` разошёлся с файлом → ``update_hashes``
битые ссылки         derived-таблица ``link`` протухла → sync + resolve секций
протухшие замки      ``checked_out_at`` старше TTL → ``release_stale``
===================  =========================================================

Не чинятся никогда: ``stale_export`` (в files-are-source режиме это норма),
``missing`` (вслепую не пересоздаём), hash ``BROKEN`` (пересчёт только
предупредит, файла всё равно нет), ``unplaced`` и внешние findings. Все они
уходят в ``RepairResult.reported_only`` счётчиками. ``doc export`` не зовётся
вовсе — он под guard'ом до byte-identical round-trip.

Порядок исполнения задан протоколом ``plugins/cod-doc/commands/drift.md`` и
менять его нельзя: если импортированный файл входит в реестр хэшей корневого
``MASTER.md``, после импорта идёт ``update_hashes``, и только затем
``doc import MASTER.md`` — иначе реестр разъедется с файлами.

.. warning::

   ``dry_run`` **не** покрывается одним ``commit=False``.
   ``hash_calc.update_hashes`` пишет ``MASTER.md`` на диск безусловно и
   dry-run не имеет. Поэтому :func:`apply` принимает ``dry_run`` явно и при
   нём не зовёт ``update_hashes`` вовсе: план строится из
   ``check_stale_refs``, который уже возвращает ``path/expected/actual`` —
   это и есть diff.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.core.hash_calc import LINK_PATTERN, update_hashes
from cod_doc.infra.models import DocumentModel, SectionModel, TaskModel
from cod_doc.services import activity_service, checkout_service, curator_service, doc_service
from cod_doc.services import link_service as link_svc
from cod_doc.services.projection_service import DriftStatus, import_document

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from sqlalchemy.orm import Session

__all__ = [
    "DEFAULT_TTL_MINUTES",
    "RepairAction",
    "RepairPlan",
    "RepairResult",
    "apply",
    "backfill_project_links",
    "diagnose",
]

log = logging.getLogger("cod_doc.services.repair_service")

#: TTL замка по умолчанию — тот же, что у ``checkout_service.release_stale``.
DEFAULT_TTL_MINUTES = 30

#: Виды действий. Строки попадают в отчёт и в JSON, поэтому это контракт.
KIND_DOC_IMPORT = "doc_import"
KIND_HASH_UPDATE = "hash_update"
KIND_LINK_BACKFILL = "link_backfill"
KIND_RELEASE_STALE = "release_stale"

#: Все четыре вида присутствуют в ``curable`` всегда, даже нулями: форма
#: отчёта не должна зависеть от того, что нашлось в конкретном прогоне.
_KINDS: tuple[str, ...] = (
    KIND_DOC_IMPORT,
    KIND_HASH_UPDATE,
    KIND_LINK_BACKFILL,
    KIND_RELEASE_STALE,
)

#: Статусы реестра хэшей из ``hash_calc.check_stale_refs``.
_MASTER_STALE = "STALE"
_MASTER_BROKEN = "BROKEN"

#: Сводное событие write-пути (ADO-040). Точечные ``doc.imported`` /
#: ``task.released`` эмитят вызываемые сервисы сами.
_ACTIVITY_KIND = "project.repaired"

#: ``curator_service.next`` режет ``limit``-ом только очередь ``priority``;
#: план починки строится по полной карточке, поэтому очередь не нужна вовсе.
_QUEUE_NOT_NEEDED = 0


@dataclass(slots=True)
class RepairAction:
    """Одно действие починки: что сделали (или сделали бы) и с чем."""

    kind: str
    ref: str
    applied: bool = False
    detail: str | None = None
    error: str | None = None


@dataclass(slots=True)
class RepairPlan:
    """Что чинится и что только докладывается — до всякой записи."""

    project: str
    curable: dict[str, int] = field(default_factory=dict)
    reported_only: dict[str, int] = field(default_factory=dict)
    actions: list[RepairAction] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Плоский JSON-safe снимок — симметрично :meth:`RepairResult.as_dict`.

        ``update_service`` сериализует план дважды — в ``--json`` и в
        resume-payload для дочернего процесса. Два ручных
        ``dataclasses.asdict`` в разных местах разъедутся, поэтому форма
        живёт здесь, рядом с полями.
        """
        return {
            "project": self.project,
            "curable": dict(self.curable),
            "reported_only": dict(self.reported_only),
            "actions": [asdict(action) for action in self.actions],
        }


@dataclass(slots=True)
class RepairResult:
    """Итог починки одного проекта."""

    project: str
    dry_run: bool
    actions: list[RepairAction] = field(default_factory=list)
    reported_only: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Ни одной ошибки — ни на уровне проекта, ни в отдельном действии."""
        return not self.errors and all(action.error is None for action in self.actions)

    @property
    def applied(self) -> int:
        """Сколько действий реально применено."""
        return sum(1 for action in self.actions if action.applied)

    def as_dict(self) -> dict[str, Any]:
        """Плоский JSON-safe снимок: ни Path, ни datetime наружу не уходят."""
        return {
            "project": self.project,
            "dry_run": self.dry_run,
            "ok": self.ok,
            "applied": self.applied,
            "actions": [asdict(action) for action in self.actions],
            "reported_only": dict(self.reported_only),
            "errors": list(self.errors),
        }


@dataclass(slots=True)
class _Context:
    """Разрешённые координаты проекта плюс состояние одного прогона.

    ``released`` — мемо на весь прогон: ``checkout_service.release_stale``
    снимает все протухшие замки проекта одним вызовом, а действий в плане
    столько, сколько замков. Мемо нужно и после проектного фильтра
    (ADO-192): фильтр сузил чистку до проекта, но не сделал её поштучной —
    без мемо второе действие звало бы уже пустую чистку и докладывало «не
    снят» о том, что снято первым.
    """

    project_id: int
    root_path: Path
    master_path: Path
    master_rel: str
    slug: str
    ttl_minutes: int
    author: str
    released: set[str] | None = None


# --------------------------------------------------------------------- #
# Диагноз                                                                #
# --------------------------------------------------------------------- #


def _relative(path: Path, root: Path) -> str:
    """Путь относительно корня проекта; вне корня — как есть.

    Тот же приём, что у ``curator_service.next``: сравнивать с ``Document.path``
    можно только repo-relative строку.
    """
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _registry_paths(master_path: Path) -> set[str]:
    """Пути из реестра гибридных ссылок ``MASTER.md``.

    Принадлежность реестру решает, нужен ли после импорта ``update_hashes``:
    правка файла на диске меняет его sha, а реестр об этом не знает.
    """
    if not master_path.exists():
        return set()
    content = master_path.read_text(encoding="utf-8")
    return {m.group("path").lstrip("/") for m in LINK_PATTERN.finditer(content)}


def _stale_locks(session: Session, project_id: int, ttl_minutes: int) -> list[str]:
    """``task_id`` замков этого проекта старше TTL — поимённо, для плана.

    Тот же предикат, что у ``checkout_service.release_stale`` с
    ``project_id``: план обязан перечислять ровно то, что снимет исполнение,
    иначе он врёт о последствиях.
    """
    cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
    rows = session.execute(
        select(TaskModel.task_id)
        .where(
            TaskModel.project_id == project_id,
            TaskModel.checked_out_at.is_not(None),
            TaskModel.checked_out_at < cutoff,
        )
        .order_by(TaskModel.task_id)
    ).scalars()
    return [str(task_id) for task_id in rows]


def _stale_hash_detail(entries: Sequence[dict[str, str]]) -> str:
    """Diff реестра без записи на диск — ровно то, что вернул ``check_stale_refs``."""
    if not entries:
        return "файл реестра правился на диске — пересчитать записи"
    return ", ".join(
        f"{entry['path']}: {entry['expected']}→{entry.get('actual', '?')}" for entry in entries
    )


def _master_is_document(session: Session, project_id: int, master_rel: str) -> bool:
    """Зарегистрирован ли сам ``MASTER.md`` документом проекта.

    Если нет — после ``update_hashes`` импортировать нечего, и планировать
    третий шаг протокола не нужно.
    """
    return any(doc.path == master_rel for doc in doc_service.list_for_project(session, project_id))


def _build_actions(
    session: Session,
    card: dict[str, Any],
    ctx: _Context,
) -> list[RepairAction]:
    """Действия в порядке исполнения — он же порядок протокола ``drift.md``."""
    issues: list[dict[str, Any]] = card["drift"]["issues"]
    edited = [i for i in issues if i["status"] == DriftStatus.EDITED_IN_PLACE.value]
    stale_refs = [e for e in card["master"] if e["status"] == _MASTER_STALE]
    registry = _registry_paths(ctx.master_path)

    actions = [
        RepairAction(
            kind=KIND_DOC_IMPORT,
            ref=str(issue["path"]),
            detail=f"doc_key={issue['doc_key']}",
        )
        for issue in edited
        if issue["path"] != ctx.master_rel
    ]

    # Шаг 2 протокола: реестр пересчитывается ПОСЛЕ импорта правленых файлов.
    touches_registry = bool(stale_refs) or any(i["path"] in registry for i in edited)
    if touches_registry:
        actions.append(
            RepairAction(
                kind=KIND_HASH_UPDATE,
                ref=ctx.master_rel,
                detail=_stale_hash_detail(stale_refs),
            )
        )

    # Шаг 3: и только теперь сам MASTER.md едет в БД — иначе в неё попадёт
    # реестр, который вот-вот перепишут.
    master_edited = any(i["path"] == ctx.master_rel for i in edited)
    if (touches_registry or master_edited) and _master_is_document(
        session, ctx.project_id, ctx.master_rel
    ):
        actions.append(
            RepairAction(
                kind=KIND_DOC_IMPORT,
                ref=ctx.master_rel,
                detail="реестр переписан — правка едет в БД",
            )
        )

    if card["links"]:
        actions.append(
            RepairAction(
                kind=KIND_LINK_BACKFILL,
                ref=ctx.slug,
                detail=f"нерезолвящихся ссылок: {len(card['links'])}",
            )
        )

    actions.extend(
        RepairAction(
            kind=KIND_RELEASE_STALE,
            ref=task_id,
            detail=f"замок старше {ctx.ttl_minutes} мин",
        )
        for task_id in _stale_locks(session, ctx.project_id, ctx.ttl_minutes)
    )
    return actions


def _curable_counts(actions: Sequence[RepairAction]) -> dict[str, int]:
    counts = dict.fromkeys(_KINDS, 0)
    for action in actions:
        counts[action.kind] += 1
    return counts


def _reported_only(card: dict[str, Any]) -> dict[str, int]:
    """Счётчики того, что не чинится никогда — но и молчать о чём нельзя."""
    issues: list[dict[str, Any]] = card["drift"]["issues"]
    return {
        DriftStatus.STALE_EXPORT.value: sum(
            1 for i in issues if i["status"] == DriftStatus.STALE_EXPORT.value
        ),
        DriftStatus.MISSING.value: sum(
            1 for i in issues if i["status"] == DriftStatus.MISSING.value
        ),
        "hash_broken": sum(1 for e in card["master"] if e["status"] == _MASTER_BROKEN),
        "unplaced": int(card["unplaced"]["count"]),
        "findings": len(card["findings"]),
    }


def _make_context(
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    ttl_minutes: int,
    author: str,
) -> _Context:
    return _Context(
        project_id=project_id,
        root_path=root_path,
        master_path=master_path,
        master_rel=_relative(master_path, root_path),
        slug=slug,
        ttl_minutes=ttl_minutes,
        author=author,
    )


def _diagnose_with_context(session: Session, ctx: _Context) -> RepairPlan:
    card: dict[str, Any] = curator_service.next(
        session,
        project_id=ctx.project_id,
        root_path=ctx.root_path,
        master_path=ctx.master_path,
        limit=_QUEUE_NOT_NEEDED,
        project_slug=ctx.slug,
    )["card"]
    actions = _build_actions(session, card, ctx)
    return RepairPlan(
        project=ctx.slug,
        curable=_curable_counts(actions),
        reported_only=_reported_only(card),
        actions=actions,
    )


def diagnose(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> RepairPlan:
    """Собрать план починки. Read-only: звать под ``transactional(sf, commit=False)``.

    Источник находок — ``curator_service.next``; отбор — whitelist из
    докстринга модуля.
    """
    ctx = _make_context(
        project_id=project_id,
        root_path=root_path,
        master_path=master_path,
        slug=slug,
        ttl_minutes=ttl_minutes,
        author="system:diagnose",
    )
    return _diagnose_with_context(session, ctx)


# --------------------------------------------------------------------- #
# Исполнение                                                             #
# --------------------------------------------------------------------- #


def _run_doc_import(session: Session, action: RepairAction, ctx: _Context) -> None:
    report = import_document(
        session,
        ctx.project_id,
        ctx.root_path / action.ref,
        author=ctx.author,
        root_path=ctx.root_path,
    )
    if report is None:
        raise LookupError(f"{action.ref}: файл не зарегистрирован документом проекта")
    action.applied = True
    if report.warnings:
        action.detail = f"{action.detail}; предупреждений: {len(report.warnings)}"


def _run_hash_update(session: Session, action: RepairAction, ctx: _Context) -> None:
    del session  # реестр живёт на диске, а не в БД
    updated, warnings = update_hashes(ctx.master_path)
    action.applied = True
    action.detail = f"{action.detail}; переписано записей: {updated}"
    if warnings:
        action.detail = f"{action.detail}; {'; '.join(warnings)}"


def _run_link_backfill(session: Session, action: RepairAction, ctx: _Context) -> None:
    sections, links, docs = backfill_project_links(session, ctx.project_id)
    action.applied = True
    action.detail = f"секций {sections}, ссылок {links}, документов {docs}"


def _run_release_stale(session: Session, action: RepairAction, ctx: _Context) -> None:
    if ctx.released is None:
        ctx.released = set(
            checkout_service.release_stale(
                session,
                ttl_minutes=ctx.ttl_minutes,
                project_id=ctx.project_id,
            )
        )
    action.applied = action.ref in ctx.released
    if not action.applied:
        action.detail = "замок снят до чистки — TTL уже не истёк"


_RUNNERS: dict[str, Callable[[Session, RepairAction, _Context], None]] = {
    KIND_DOC_IMPORT: _run_doc_import,
    KIND_HASH_UPDATE: _run_hash_update,
    KIND_LINK_BACKFILL: _run_link_backfill,
    KIND_RELEASE_STALE: _run_release_stale,
}


def _execute(session: Session, action: RepairAction, ctx: _Context) -> None:
    runner = _RUNNERS.get(action.kind)
    if runner is None:
        raise ValueError(f"неизвестный вид починки: {action.kind}")
    runner(session, action, ctx)


def _emit_summary(session: Session, ctx: _Context, result: RepairResult) -> None:
    """ADO-040: одно сводное событие на прогон, в той же транзакции."""
    activity_service.emit_for_write(
        session,
        ctx.project_id,
        _ACTIVITY_KIND,
        ctx.author,
        scope_kind="project",
        scope_id=ctx.slug,
        payload={
            "applied": result.applied,
            "curable": _curable_counts(result.actions),
            "reported_only": dict(result.reported_only),
            "errors": list(result.errors),
        },
        summary=(
            f"Repair {ctx.slug}: применено {result.applied} из {len(result.actions)}, "
            f"ошибок {len(result.errors)}"
        ),
    )


def apply(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
    dry_run: bool = False,
    author: str = "cli:update",
) -> RepairResult:
    """Применить починку. Одна упавшая чинилка не обрывает остальные.

    Семантика та же, что у ``project_service.migrate_registered_projects``:
    ошибка попадает в отчёт, а не наверх.

    ADO-040: при ``not dry_run`` эмитит одно сводное событие
    ``project.repaired`` в той же транзакции. Точечные ``doc.imported`` /
    ``task.released`` эмитят вызываемые сервисы сами.
    """
    ctx = _make_context(
        project_id=project_id,
        root_path=root_path,
        master_path=master_path,
        slug=slug,
        ttl_minutes=ttl_minutes,
        author=author,
    )
    plan = _diagnose_with_context(session, ctx)
    result = RepairResult(
        project=slug,
        dry_run=dry_run,
        actions=plan.actions,
        reported_only=plan.reported_only,
    )
    if dry_run:
        # Ни одного вызова чинилок: `update_hashes` пишет MASTER.md на диск
        # безусловно, и откатить его `commit=False` не может.
        return result

    for action in plan.actions:
        try:
            _execute(session, action, ctx)
        except Exception as exc:
            action.error = str(exc)
            result.errors.append(f"{action.kind} {action.ref}: {exc}")
            log.warning("repair %s: %s %s failed: %s", slug, action.kind, action.ref, exc)

    _emit_summary(session, ctx, result)
    return result


# --------------------------------------------------------------------- #
# Ссылки                                                                 #
# --------------------------------------------------------------------- #


def backfill_project_links(session: Session, project_id: int) -> tuple[int, int, int]:
    """Пересобрать derived-таблицу ``link`` по всем секциям проекта.

    Возвращает ``(секций, ссылок, документов)``. Логика переехала сюда из
    ``cli/link.py::link_backfill`` (COD-079), которая ходила в БД прямым
    ``select(SectionModel…)`` из presentation — против правила «прямых
    SQL-запросов из presentation нет». CLI теперь зовёт эту функцию.
    """
    rows = session.execute(
        select(SectionModel.row_id, DocumentModel.doc_key)
        .join(DocumentModel, DocumentModel.row_id == SectionModel.document_id)
        .where(DocumentModel.project_id == project_id)
        .order_by(DocumentModel.doc_key, SectionModel.position)
    ).all()

    sections_done = 0
    links_total = 0
    docs_seen: set[str] = set()
    for sec_id, doc_key in rows:
        try:
            link_svc.sync_section(session, int(sec_id))
            links = link_svc.resolve_section(session, int(sec_id))
        except Exception as exc:
            log.warning("backfill skipped %s: %s", doc_key, exc)
            continue
        sections_done += 1
        links_total += len(links)
        docs_seen.add(doc_key)
    return sections_done, links_total, len(docs_seen)
