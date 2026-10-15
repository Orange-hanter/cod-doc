"""Finding deduplication: atomic upsert + source-run tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from cod_doc.infra import sync_capture
from cod_doc.infra.models import FindingModel, FindingSourceRunModel
from cod_doc.services import search_service
from cod_doc.services.activity_service import _uuid7

if TYPE_CHECKING:
    from sqlalchemy import Table
    from sqlalchemy.orm import Session

_TABLE = "finding"
#: Колонки, которые меняет конфликтная ветка upsert'а.
_ON_CONFLICT = frozenset({"last_seen_at", "times_seen"})


@dataclass(slots=True)
class FindingSeed:
    """A pre-fingerprinted finding ready for deduplicated persistence."""

    fingerprint: str
    source: str
    title: str
    severity: str
    source_ref: str | None = None
    kind: str | None = None
    body: str | None = None
    path: str | None = None
    line: int | None = None
    confidence: float | None = None
    payload: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None


class IngestResult:
    """Counters returned by :func:`ingest_findings`."""

    def __init__(self) -> None:
        self.created = 0
        self.updated = 0

    def as_dict(self) -> dict[str, int]:
        return {"created": self.created, "updated": self.updated}


def ingest_findings(
    session: Session,
    *,
    project_id: int,
    source_run_id: str,
    seeds: list[FindingSeed],
    run_id: str | None = None,
) -> IngestResult:
    """Upsert findings and record every ingest pass in ``finding_source_run``.

    Uses SQLite ``INSERT … ON CONFLICT`` so concurrent ingests of the same
    finding are serialised by the database (WAL + ``busy_timeout``) rather than
    racing in Python. Returns the number of newly-created and updated rows.

    Conflict key: ``UNIQUE (project_id, source, fingerprint)``.
    On conflict: ``times_seen += 1``, ``last_seen_at = now``.

    The upsert bypasses the unit of work, so the ``sync_op`` operation is
    written explicitly by ``sync_capture.record`` in the same transaction
    (RFC 29 §3.4, CLO-007); see :func:`_record_upsert`.
    """
    result = IngestResult()
    now = datetime.now(UTC)
    table = cast("Table", FindingModel.__table__)

    for seed in seeds:
        payload = seed.payload or {}
        # Прежний образ строки — до upsert'а: RETURNING в SQLite отдаёт только
        # новый, а ``base`` операции — значения до записи (§10.3).
        # ``last_seen_at`` NOT NULL: ``None`` — строки ещё нет.
        last_seen_before: object = session.execute(
            select(table.c.last_seen_at).where(
                table.c.project_id == project_id,
                table.c.source == seed.source,
                table.c.fingerprint == seed.fingerprint,
            )
        ).scalar_one_or_none()
        insert_stmt = sqlite_insert(FindingModel).values(
            project_id=project_id,
            uid=_uuid7(),
            finding_uid=_uuid7(),
            source=seed.source,
            source_ref=seed.source_ref,
            fingerprint=seed.fingerprint,
            severity=seed.severity,
            kind=seed.kind,
            title=seed.title,
            body=seed.body,
            path=seed.path,
            line=seed.line,
            confidence=seed.confidence,
            first_seen_at=now,
            last_seen_at=now,
            times_seen=1,
            payload=payload,
            run_id=run_id,
        )
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=["project_id", "source", "fingerprint"],
            set_={
                "last_seen_at": now,
                "times_seen": FindingModel.times_seen + 1,
                # Строка, вставленная мимо захвата до CLO-007, получает uid здесь.
                "uid": func.coalesce(FindingModel.uid, insert_stmt.excluded.uid),
            },
        ).returning(FindingModel.row_id, FindingModel.times_seen)
        row = session.execute(stmt).one()
        finding_id: int = row.row_id
        times_seen: int = row.times_seen
        _record_upsert(
            session,
            project_id=project_id,
            finding_id=finding_id,
            last_seen_before=last_seen_before,
            author=f"system:ingest:{seed.source}",
        )

        if times_seen == 1:
            result.created += 1
        else:
            result.updated += 1

        session.add(
            FindingSourceRunModel(
                finding_id=finding_id,
                source_run_id=source_run_id,
                ts=now,
                severity_at_run=seed.severity,
                raw=seed.raw,
            )
        )
        session.flush()

        # CUR-012: an ingested finding is searchable at once, without a
        # manual `search --reindex`.
        finding = session.get(FindingModel, finding_id)
        if finding is not None:
            search_service.index_finding(session, finding)

    return result


def _record_upsert(
    session: Session,
    *,
    project_id: int,
    finding_id: int,
    last_seen_before: object,
    author: str,
) -> None:
    """Операция upsert'а находки в ``sync_op`` (RFC 29 §3.4, CLO-007).

    Upsert — SQL мимо unit-of-work, хук его не видит; операцию пишет
    ``sync_capture.record`` в той же транзакции. Новая строка уезжает целиком
    с пустым ``base``; повтор — только ``last_seen_at``/``times_seen``.
    Routine-находки и находки с машинным путём — derived (``row_is_local``
    реестра): ``shipped_fields`` вернёт ``None``, операции нет.

    ``base.times_seen`` — ``times_seen - 1``: инкремент атомарный, значение
    точное и при гонке ingest'ов. ``base.last_seen_at`` прочитан до
    upsert'а; если строку вставил параллельный ingest между чтением и
    upsert'ом, прежнего значения нет, и колонка уезжает без ``base``.

    Автор — ``system:ingest:<source>``: у ingest'а нет параметра ``author``,
    находку порождает внешний источник, а не человек.
    """
    table = cast("Table", FindingModel.__table__)
    now = dict(session.execute(select(table).where(table.c.row_id == finding_id)).mappings().one())
    uid = now["uid"]
    assert isinstance(uid, str)
    times_seen = cast("int", now["times_seen"])
    if times_seen == 1:
        fields = sync_capture.shipped_fields(session, _TABLE, now)
        base: dict[str, object] | None = {}
    else:
        fields = sync_capture.shipped_fields(session, _TABLE, now, _ON_CONFLICT)
        prior: dict[str, object] = {"times_seen": times_seen - 1}
        if last_seen_before is not None:
            prior["last_seen_at"] = last_seen_before
        base = sync_capture.shipped_fields(session, _TABLE, {**now, **prior}, prior)
    if fields is None or base is None:
        return
    sync_capture.record(
        session,
        project_id=project_id,
        table=_TABLE,
        entity_uid=uid,
        kind="upsert",
        fields=fields,
        base=base,
        author=author,
    )
