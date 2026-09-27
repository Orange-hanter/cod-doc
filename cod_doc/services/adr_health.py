"""Здоровье реестра ADR: пробелы, которые список показывает прочерком (ADO-232).

Что проверяется:

- **accepted без даты решения** — ``decided_at IS NULL`` у принятого ADR.
  Дата — единственная опора, чтобы соотнести решение с кодом и ревизиями того
  времени; в списке пробел выглядел обычным «—» и никому не попадался на
  глаза (ADR-015 на живой БД 2026-09-27);
- **решение без текста** — ``decision`` пуст у proposed или accepted ADR:
  запись есть, а что решили — нет. У superseded/deprecated/rejected не
  предъявляем: решение уже не действует, дописывать его задним числом —
  работа без пользы.

Модуль лежит в ``services/``, а не рядом с ``adr_service``, по той же причине,
что ``graph_health``: :func:`sync` пишет находки, и производитель находок не
должен выглядеть мутацией проверяемого сервиса. Отдельной поверхности нет —
результат виден через рутину ``adr_health`` и очередь ``curator_next``.

``close_after_misses=1``: правила детерминированы, промахов не бывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import ADRModel
from cod_doc.services import activity_service, finding_service
from cod_doc.services.activity_service import _uuid7
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

#: Статусы, для которых пустое решение — пробел: решение действует или
#: вот-вот начнёт действовать.
LIVE_STATUSES = frozenset({"proposed", "accepted"})

FINDING_SOURCE = "routine"
FINDING_SOURCE_REF = "adr_health"
CLOSE_AFTER_MISSES = 1
DEFAULT_AUTHOR = "routine:adr_health"


@dataclass(frozen=True)
class ADRRow:
    adr_id: str
    title: str
    status: str
    has_decided_at: bool
    decision: str | None


@dataclass(frozen=True)
class ADRIssue:
    """Одна проблема ADR.

    ``code`` и ``scope_id`` идут в fingerprint находки — это контракт: смена
    формы осиротит открытые находки, а вылеченные не закроются.
    """

    code: str
    scope_id: str
    title: str
    body: str
    severity: str = "minor"
    scope_kind: str = "adr"


def assess_rows(rows: Iterable[ADRRow]) -> list[ADRIssue]:
    """Чистая часть: решает по уже прочитанным строкам, про ``Session`` не знает."""
    issues: list[ADRIssue] = []
    for r in rows:
        if r.status == "accepted" and not r.has_decided_at:
            issues.append(
                ADRIssue(
                    code="adr_missing_decided_at",
                    scope_id=r.adr_id,
                    title=f"{r.adr_id} accepted без даты решения",
                    # update() у accepted заморожен; дату догоняет sync_body —
                    # это запись о решении, а не правка решения.
                    body=(
                        f"«{r.title}» принят, но decided_at пуст. "
                        f"Поставь дату: cod-doc adr sync {r.adr_id} -p <project> "
                        f"--decided-at YYYY-MM-DD (MCP: adr_sync_body)."
                    ),
                )
            )
        if r.status in LIVE_STATUSES and not (r.decision or "").strip():
            issues.append(
                ADRIssue(
                    code="adr_empty_decision",
                    scope_id=r.adr_id,
                    title=f"{r.adr_id} без текста решения",
                    body=f"«{r.title}» ({r.status}): раздел Decision пуст.",
                    severity="major",
                )
            )
    return issues


def assess(session: Session, project_id: int) -> list[ADRIssue]:
    rows = [
        ADRRow(
            adr_id=r.adr_id,
            title=r.title,
            status=r.status,
            has_decided_at=r.decided_at is not None,
            decision=r.decision,
        )
        for r in session.execute(
            select(
                ADRModel.adr_id,
                ADRModel.title,
                ADRModel.status,
                ADRModel.decided_at,
                ADRModel.decision,
            )
            .where(ADRModel.project_id == project_id)
            .order_by(ADRModel.adr_id)
        ).all()
    ]
    return assess_rows(rows)


def _seed_for(issue: ADRIssue) -> FindingSeed:
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
    author: str = DEFAULT_AUTHOR,
) -> dict[str, int]:
    """Посчитать пробелы реестра и свести их с находками в БД.

    Отпечатки передаются в сверку всегда, в том числе пустые: реестр без
    ADR — это реестр без пробелов, и закрыть всё вылеченное законно.
    """
    issues = assess(session, project_id)
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
        "adr.health_synced",
        author,
        scope_kind="project",
        scope_id=project_slug,
        payload=result,
        summary=f"ADR health: {len(issues)} issue(s) for {project_slug}",
    )
    return result
