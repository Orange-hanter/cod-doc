"""Finding deduplication: atomic upsert + source-run tracking."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from cod_doc.infra.models import FindingModel, FindingSourceRunModel
from cod_doc.services.activity_service import _uuid7

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


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
    """
    result = IngestResult()
    now = datetime.now(UTC)

    for seed in seeds:
        payload = seed.payload or {}
        stmt = (
            sqlite_insert(FindingModel)
            .values(
                project_id=project_id,
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
            .on_conflict_do_update(
                index_elements=["project_id", "source", "fingerprint"],
                set_={
                    "last_seen_at": now,
                    "times_seen": FindingModel.times_seen + 1,
                },
            )
            .returning(FindingModel.row_id, FindingModel.times_seen)
        )
        row = session.execute(stmt).one()
        finding_id: int = row.row_id
        times_seen: int = row.times_seen

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

    return result
