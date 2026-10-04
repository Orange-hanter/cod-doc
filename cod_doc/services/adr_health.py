"""Здоровье реестра ADR: пробелы, которые список показывает прочерком (ADO-232).

Что проверяется:

- **accepted без даты решения** — ``decided_at IS NULL`` у принятого ADR.
  Дата — единственная опора, чтобы соотнести решение с кодом и ревизиями того
  времени; в списке пробел выглядел обычным «—» и никому не попадался на
  глаза (ADR-015 на живой БД 2026-09-27);
- **решение без текста** — ``decision`` пуст у proposed или accepted ADR:
  запись есть, а что решили — нет. У superseded/deprecated/rejected не
  предъявляем: решение уже не действует, дописывать его задним числом —
  работа без пользы;
- **дата у непринятого** (ARG-003) — ``decided_at`` стоит у proposed: решения
  ещё нет, а дата его изображает. ADR-009 носил дату предложения до
  2026-10-03;
- **долгий черновик** (ARG-003) — proposed дольше :data:`STALE_PROPOSAL_DAYS`
  от ``created``: решение ждёт человека, а в списке выглядит как остальные
  (ADR-009 ждал 139 дней);
- **опора на снятое решение** (ARG-003) — действующий или предложенный ADR
  ``depends_on`` решение, которое отклонено, снято или заменено: принять его
  в этом виде нельзя.

Модуль лежит в ``services/``, а не рядом с ``adr_service``, по той же причине,
что ``graph_health``: :func:`sync` пишет находки, и производитель находок не
должен выглядеть мутацией проверяемого сервиса. Отдельной поверхности нет —
результат виден через рутину ``adr_health`` и очередь ``curator_next``.

``close_after_misses=1``: правила детерминированы, промахов не бывает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.infra.models import ADRModel, ADRRelationModel
from cod_doc.services import activity_service, finding_service
from cod_doc.services.activity_service import _uuid7
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

#: Статусы, для которых пустое решение — пробел: решение действует или
#: вот-вот начнёт действовать.
LIVE_STATUSES = frozenset({"proposed", "accepted"})

#: Статусы, после которых на решение нельзя опираться.
CLOSED_STATUSES = frozenset({"superseded", "deprecated", "rejected"})

#: Сколько дней черновик ждёт без находки. Месяц — порог из RFC 34 §3.2: в
#: списке он же красит срок ожидания янтарём. Константа, а не настройка
#: проекта (RFC 34 §7.1 открыт) — один источник для обоих мест.
STALE_PROPOSAL_DAYS = 30

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
    #: Когда ADR заведён; ``None`` — правило «долгий черновик» молчит.
    created: date | None = None
    #: ``depends_on``-цели: пары (adr_id, status).
    depends_on: tuple[tuple[str, str], ...] = ()


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


def assess_rows(rows: Iterable[ADRRow], *, today: date | None = None) -> list[ADRIssue]:
    """Чистая часть: решает по уже прочитанным строкам, про ``Session`` не знает.

    ``today`` — для теста; по умолчанию текущая дата UTC.
    """
    today = today or datetime.now(UTC).date()
    issues: list[ADRIssue] = []
    for r in rows:
        issues.extend(_lifecycle_issues(r, today))
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


def _lifecycle_issues(r: ADRRow, today: date) -> list[ADRIssue]:
    """ARG-003: правила про путь черновика к решению и про опору на другие ADR."""
    issues: list[ADRIssue] = []
    if r.status == "proposed" and r.has_decided_at:
        issues.append(
            ADRIssue(
                code="adr_proposed_has_date",
                scope_id=r.adr_id,
                title=f"{r.adr_id} не принят, но у него дата решения",
                body=(
                    f"«{r.title}» — proposed, а decided_at заполнен: обычно это дата "
                    f"предложения. Убери её: cod-doc adr sync {r.adr_id} -p <project> "
                    f"--clear-decided-at (MCP: adr_sync_body, clear_decided_at=true). "
                    f"При принятии дата встанет сама."
                ),
            )
        )
    if r.status == "proposed" and r.created is not None:
        waiting = (today - r.created).days
        if waiting > STALE_PROPOSAL_DAYS:
            issues.append(
                ADRIssue(
                    code="adr_stale_proposal",
                    scope_id=r.adr_id,
                    title=f"{r.adr_id} ждёт решения {waiting} дн.",
                    body=(
                        f"«{r.title}» предложен {r.created.isoformat()} и ждёт решения "
                        f"{waiting} дней (порог {STALE_PROPOSAL_DAYS}). Прими "
                        f"(adr_update status=accepted) или отклони (status=rejected)."
                    ),
                )
            )
    if r.status in LIVE_STATUSES:
        for target, target_status in r.depends_on:
            if target_status not in CLOSED_STATUSES:
                continue
            issues.append(
                ADRIssue(
                    code="adr_depends_on_closed",
                    scope_kind="adr_relation",
                    scope_id=f"{r.adr_id}->{target}",
                    title=f"{r.adr_id} опирается на {target}, а тот {target_status}",
                    body=(
                        f"«{r.title}» ({r.status}) опирается на {target}, который "
                        f"{target_status}. Пересмотри опору: перенаправь связь на "
                        f"преемника (adr_unrelate + adr_relate) или пересмотри само решение."
                    ),
                    severity="major",
                )
            )
    return issues


def _depends_on(session: Session, project_id: int) -> dict[str, list[tuple[str, str]]]:
    """``depends_on``-рёбра проекта: adr_id источника → [(adr_id цели, статус цели)]."""
    src = ADRModel.__table__.alias("src")
    dst = ADRModel.__table__.alias("dst")
    out: dict[str, list[tuple[str, str]]] = {}
    for r in session.execute(
        select(src.c.adr_id.label("src_id"), dst.c.adr_id.label("dst_id"), dst.c.status)
        .select_from(ADRRelationModel)
        .join(src, src.c.row_id == ADRRelationModel.from_id)
        .join(dst, dst.c.row_id == ADRRelationModel.to_id)
        .where(src.c.project_id == project_id, ADRRelationModel.kind == "depends_on")
        .order_by(dst.c.adr_id)
    ).all():
        out.setdefault(r.src_id, []).append((r.dst_id, r.status))
    return out


def assess(session: Session, project_id: int) -> list[ADRIssue]:
    deps = _depends_on(session, project_id)
    rows = [
        ADRRow(
            adr_id=r.adr_id,
            title=r.title,
            status=r.status,
            has_decided_at=r.decided_at is not None,
            decision=r.decision,
            created=r.created.date() if r.created else None,
            depends_on=tuple(deps.get(r.adr_id, ())),
        )
        for r in session.execute(
            select(
                ADRModel.adr_id,
                ADRModel.title,
                ADRModel.status,
                ADRModel.decided_at,
                ADRModel.decision,
                ADRModel.created,
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
