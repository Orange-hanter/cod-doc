"""ACU-004 (RFC 28 §3.1–3.2, фаза 1): прогон куратора без клиента за рулём.

Диагноз один на всю систему — ``curator_service.next``. Прогон делит его
пункты на три уровня, и уровень — свойство вида пункта, а не решение в
рантайме:

1. **В БД сам.** Импорт ``edited_in_place`` и resync derived-ссылок —
   через ``repair_service.apply``, общий исполнитель фазы D ``update``:
   второй исполнитель завести нельзя, он разъедется с первым в порядке
   протокола ``drift.md``. Из его видов выключены пересчёт реестра хэшей
   (пишет ``MASTER.md`` в чекаут владельца), снятие протухших замков (не дело
   куратора документации) и засев дерева (структурное решение человека).
   Раскладка Инбокса — только документы, для которых сработало правило.
2. **Файлы — никогда в чекаут владельца** (RFC 28 §3.8). ``missing``,
   ``stale_export`` и протухший реестр хэшей едут в клон куратора через
   ``curator_sync_service.export_sync`` — если вызывающий его передал.
   Нет — пункты остаются в отчёте.
3. **Всё прочее — отчёт**: ``conflict``, битые ссылки, ``BROKEN`` в реестре,
   находки, открытые вопросы (закрыть вопрос — решение человека) и любой
   вид, которого прогон не знает.

Потолок ``max_auto`` считает записи уровня 1. Группа, не влезающая в
остаток целиком, не применяется вовсе и уходит в отчёт с пометкой
``capped``: частично применённая починка по протоколу ``drift.md`` хуже
неприменённой. Выгрузка в клон в потолок не входит — её результат ждёт
ревью в PR.

``apply=False`` ничего не пишет: те же расчёты, тот же отчёт.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol
from uuid import uuid4

from cod_doc.services import curator_service, doc_tree_service, repair_service, revision_service
from cod_doc.services.run_context import run_scope

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.config import ProjectEntry
    from cod_doc.services.curator_sync_service import SyncReport

DEFAULT_AUTHOR = curator_service.CURATOR_AUTHOR
DEFAULT_MAX_AUTO = 50

#: ``agent_run.wake_reason`` прогона с записью (ACU-007).
WAKE_REASON = "curator_sweep"

#: Виды исполнителя фазы D, которые куратору не положены (см. докстринг).
_REPAIR_SKIPPED = frozenset(
    {
        repair_service.KIND_HASH_UPDATE,
        repair_service.KIND_RELEASE_STALE,
        repair_service.KIND_TREE_SEED,
    }
)

#: Пункты очереди, которые закрывает выгрузка в клон.
_SYNC_DRIFT = frozenset({"missing", "stale_export"})
_SYNC_MASTER = frozenset({"STALE"})

#: ``curator_service.next`` режет очередь ``limit``-ом; прогону нужна вся.
_WHOLE_QUEUE = 1_000_000


class SyncFn(Protocol):
    """Выгрузка в клон куратора: ``curator_sync_service.export_sync`` без координат."""

    def __call__(self, session: Session, project_id: int) -> SyncReport: ...


@dataclass(slots=True)
class SweepReport:
    """Что прогон сделал сам, что отдал в клон и что оставил человеку."""

    applied: bool
    repair: dict[str, Any] | None = None
    placed: list[str] = field(default_factory=list)
    sync: dict[str, Any] | None = None
    reported: list[dict[str, str]] = field(default_factory=list)
    capped: list[str] = field(default_factory=list)
    #: ACU-007: строка `agent_run` прогона с записью; у сухого — None.
    run_id: str | None = None
    #: Ревизии прогона по видам сущностей (`revision_summary`, ADO-228).
    changes: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """Счётчики для ``agent_run.summary``: сколько сделано, отдано, оставлено."""
        return {
            "applied": self.applied_count,
            "placed": len(self.placed),
            "synced": len((self.sync or {}).get("exported", []))
            + len((self.sync or {}).get("deleted", [])),
            "proposed": 0,  # предложения появятся с секцией D
            "reported": len(self.reported),
            "capped": len(self.capped),
            "changes": self.changes,
        }

    @property
    def applied_count(self) -> int:
        """Сколько записей уровня 1 реально сделано."""
        repaired = int(self.repair["applied"]) if self.repair and self.applied else 0
        return repaired + (len(self.placed) if self.applied else 0)

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "applied_count": self.applied_count}


def sweep(
    session: Session,
    project_id: int,
    *,
    root_path: Path,
    master_path: Path,
    slug: str,
    apply: bool = True,
    max_auto: int = DEFAULT_MAX_AUTO,
    author: str = DEFAULT_AUTHOR,
    sync: SyncFn | None = None,
) -> SweepReport:
    """Один прогон куратора по очереди ``curator_next`` проекта."""
    payload = curator_service.next(
        session,
        project_id=project_id,
        root_path=root_path,
        master_path=master_path,
        limit=_WHOLE_QUEUE,
        project_slug=slug,
    )
    card: dict[str, Any] = payload["card"]
    report = SweepReport(applied=apply)
    coords = _Coords(project_id, root_path, master_path, slug, author, max_auto, sync)

    if not apply:
        _act(session, card, coords, report)
        _collect_reported(payload, card, report, set(), synced_master=False)
        return report

    # ACU-007: запись идёт внутри `run_scope` — строка `agent_run` и `run_id`
    # на каждой ревизии и событии прогона. Сухой прогон строку не пишет: он
    # не пишет ничего.
    with run_scope(
        session, project_id=project_id, run_id=str(uuid4()), wake_reason=WAKE_REASON
    ) as run:
        synced_docs, synced_master = _act(session, card, coords, report)
        _collect_reported(payload, card, report, synced_docs, synced_master=synced_master)
        report.run_id = run.run_id
        report.changes = _changes(session, project_id, since=run.started_at, author=author)
        run.summary = json.dumps(report.summary(), ensure_ascii=False)
    return report


@dataclass(slots=True, frozen=True)
class _Coords:
    project_id: int
    root_path: Path
    master_path: Path
    slug: str
    author: str
    max_auto: int
    sync: SyncFn | None


def _act(
    session: Session, card: dict[str, Any], c: _Coords, report: SweepReport
) -> tuple[set[str], bool]:
    """Уровень 1 и выгрузка в клон; вернуть (что ушло в клон, переписан ли реестр)."""
    budget = _repair(
        session, c.project_id, c.root_path, c.master_path, c.slug, c.author, c.max_auto, report
    )
    _place(session, c.project_id, c.author, budget, report)
    if c.sync is None or not report.applied or not _needs_sync(card):
        return set(), False
    sync_report = c.sync(session, c.project_id)
    report.sync = sync_report.to_dict()
    return set(sync_report.exported) | set(sync_report.deleted), sync_report.hashes_updated > 0


def _collect_reported(
    payload: dict[str, Any],
    card: dict[str, Any],
    report: SweepReport,
    synced_docs: set[str],
    *,
    synced_master: bool,
) -> None:
    handled = _handled_refs(card, report, synced_docs, synced_master)
    report.reported = [
        {"kind": str(item["kind"]), "ref": str(item["ref"]), "reason": str(item["reason"])}
        for item in payload["priority"]
        if (str(item["kind"]), str(item["ref"])) not in handled
    ]


def _changes(
    session: Session, project_id: int, *, since: datetime, author: str
) -> list[dict[str, Any]]:
    """Что прогон записал — по видам сущностей (ADO-228, свой агрегат не пишем)."""
    rows = revision_service.summarize(
        session, project_id, since=since, group_by=["author", "entity_kind"]
    )
    return [
        {"entity_kind": row["entity_kind"], "n": row["n"]}
        for row in rows
        if row.get("author") == author
    ]


def _repair(
    session: Session,
    project_id: int,
    root_path: Path,
    master_path: Path,
    slug: str,
    author: str,
    budget: int,
    report: SweepReport,
) -> int:
    """Уровень 1, первая группа: импорт и ссылки через исполнитель фазы D."""
    coords: dict[str, Any] = {
        "project_id": project_id,
        "root_path": root_path,
        "master_path": master_path,
        "slug": slug,
        "author": author,
        "skip_kinds": _REPAIR_SKIPPED,
    }
    plan = repair_service.apply(session, dry_run=True, **coords)
    needed = sum(1 for action in plan.actions if not action.skipped)
    if needed > budget:
        report.capped.append(f"repair: {needed} действий при остатке {budget}")
        report.repair = plan.as_dict()
        return budget
    if not report.applied or not needed:
        # Сухой прогон расходует бюджет так же, как настоящий: иначе потолок
        # для раскладки считался бы по-разному, и отчёт `apply=False` врал бы.
        report.repair = plan.as_dict()
        return budget - needed
    result = repair_service.apply(session, dry_run=False, **coords)
    report.repair = result.as_dict()
    return budget - result.applied


def _place(
    session: Session, project_id: int, author: str, budget: int, report: SweepReport
) -> None:
    """Уровень 1, вторая группа: раскладка Инбокса — только сработавшие правила."""
    if not doc_tree_service.list_nodes(session, project_id):
        return  # дерева нет: засев — решение человека, пункт уйдёт в отчёт
    planned = doc_tree_service.classify_project(
        session, project_id=project_id, author=author, dry_run=True
    ).placed
    if not planned:
        return
    if len(planned) > budget:
        report.capped.append(f"unplaced: {len(planned)} документов при остатке {budget}")
        return
    report.placed = [p.doc_key for p in planned]
    if report.applied:
        doc_tree_service.classify_project(
            session,
            project_id=project_id,
            author=author,
            dry_run=False,
            reason="curator sweep: раскладка по правилам",
        )


def _needs_sync(card: dict[str, Any]) -> bool:
    drift = any(i["status"] in _SYNC_DRIFT for i in card["drift"]["issues"])
    master = any(e["status"] in _SYNC_MASTER for e in card["master"])
    return drift or master


def _handled_refs(
    card: dict[str, Any],
    report: SweepReport,
    synced_docs: set[str],
    synced_master: bool,
) -> set[tuple[str, str]]:
    """Пункты очереди, закрытые этим прогоном, как пары (kind, ref) очереди."""
    handled: set[tuple[str, str]] = set()
    if not report.applied:
        return handled
    imported = {
        str(action["ref"])
        for action in (report.repair or {}).get("actions", [])
        if action["kind"] == repair_service.KIND_DOC_IMPORT and action["applied"]
    }
    for issue in card["drift"]["issues"]:
        path, status = str(issue["path"]), str(issue["status"])
        if (status == "edited_in_place" and path in imported) or (
            status in _SYNC_DRIFT and path in synced_docs
        ):
            handled.add(("drift", str(issue["doc_key"])))
    if synced_master:
        handled.update(("master", str(e["path"])) for e in card["master"] if e["status"] == "STALE")
    if report.placed and not _still_unplaced(card, report):
        handled.update(("unplaced", ref) for ref in _unplaced_refs(card))
    return handled


def _still_unplaced(card: dict[str, Any], report: SweepReport) -> bool:
    return int(card["unplaced"]["count"]) > len(report.placed)


def _unplaced_refs(card: dict[str, Any]) -> list[str]:
    return [f"{int(card['unplaced']['count'])} docs"]


def sweep_project(
    session: Session,
    project_id: int,
    *,
    entry: ProjectEntry,
    apply: bool,
    sync: bool,
    max_auto: int = DEFAULT_MAX_AUTO,
    author: str = DEFAULT_AUTHOR,
) -> SweepReport:
    """ACU-006: прогон по записи реестра — вход для рутины, CLI и MCP.

    ``apply`` / ``sync`` передаёт вызывающий: рутина берёт их из флагов
    ``curator_auto`` / ``curator_sync``, человек в CLI и агент в MCP — явно.
    """
    from cod_doc.services import curator_sync_service

    def _to_clone(session: Session, project_id: int) -> SyncReport:
        return curator_sync_service.sync_project(session, project_id, entry=entry, author=author)

    return sweep(
        session,
        project_id,
        root_path=entry.root,
        master_path=entry.master_path,
        slug=entry.name,
        apply=apply,
        max_auto=max_auto,
        author=author,
        sync=_to_clone if sync else None,
    )
