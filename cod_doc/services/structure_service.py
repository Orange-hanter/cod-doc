"""Blob-first structure snapshot ingest, query, replay and current pointers.

Snapshots are not ``ai_review`` findings. Untrusted artifacts are stored for
inspection but never become current and never create findings or tasks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from cod_doc.infra.models.structure import (
    CodeBoundaryModel,
    CodeContractModel,
    CodeEdgeModel,
    CodeEntityModel,
    CodeStructureSnapshotModel,
    DocCodeClaimModel,
    StructureAssessmentModel,
    StructureCurrentModel,
    StructureFindingModel,
)
from cod_doc.services import activity_service
from cod_doc.services.structure_drift import (
    compute_structure_drift,
    persist_link_suggestions,
    reconcile_findings,
    suggest_obligation_links,
)
from cod_doc.services.structure_obligations import export_obligations
from cod_doc.services.structure_protocol import (
    NORMALIZER_VERSION,
    PUBLISHABLE_TRUST,
    RETENTION_PER_BRANCH,
    StructureProtocolError,
    as_list,
    as_object,
    can_publish_current,
    compress_payload,
    decompress_payload,
    is_more_trusted,
    normalize_trust_tier,
    parse_cursor,
    sha256_text,
    stable_id,
    validate_structure_assessment,
    validate_structure_facts,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from sqlalchemy.orm import Session

logger = logging.getLogger("cod_doc.services.structure")

_DEFAULT_BRANCHES = frozenset({"main", "master"})


def _header(row: CodeStructureSnapshotModel) -> dict[str, object]:
    return {
        "snapshotId": row.row_id,
        "kind": row.kind,
        "fingerprint": row.fingerprint,
        "schemaRef": row.schema_ref,
        "normalizerVersion": row.normalizer_version,
        "headSha": row.head_sha,
        "branchRef": row.branch_ref,
        "prNumber": row.pr_number,
        "isDefaultBranch": row.is_default_branch,
        "scope": row.scope,
        "trustTier": row.trust_tier,
        "payloadSha256": row.payload_sha256,
        "uncompressedBytes": row.uncompressed_bytes,
        "truncated": row.truncated,
        "created": row.created.isoformat() if row.created else None,
    }


def get_snapshot_by_fingerprint(
    session: Session,
    project_id: int,
    fingerprint: str,
) -> CodeStructureSnapshotModel | None:
    return session.execute(
        select(CodeStructureSnapshotModel).where(
            CodeStructureSnapshotModel.project_id == project_id,
            CodeStructureSnapshotModel.kind == "structure_facts",
            CodeStructureSnapshotModel.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()


def get_snapshot_payload(row: CodeStructureSnapshotModel) -> dict[str, object]:
    return decompress_payload(row.payload_zlib, expected_sha256=row.payload_sha256)


def get_assessment_payload(row: StructureAssessmentModel) -> dict[str, object]:
    return decompress_payload(row.payload_zlib, expected_sha256=row.payload_sha256)


def _get_or_create_snapshot(
    session: Session,
    project_id: int,
    payload: dict[str, object],
    *,
    trust_tier: str,
) -> tuple[CodeStructureSnapshotModel, bool]:
    validated = validate_structure_facts(payload)
    fingerprint = str(validated["fingerprint"])
    incoming_provenance = as_object(validated.get("provenance"), label="provenance")
    existing = get_snapshot_by_fingerprint(session, project_id, fingerprint)
    if existing is not None:
        incoming_head = str(incoming_provenance.get("headSha") or "")
        incoming_branch = str(incoming_provenance.get("branchRef") or "unknown")
        incoming_digest = compress_payload(validated)[1]
        if incoming_digest != existing.payload_sha256:
            raise StructureProtocolError(
                f"fingerprint {fingerprint[:12]} already stored with different facts "
                f"(payload sha256 {existing.payload_sha256[:12]} vs {incoming_digest[:12]})"
            )
        if incoming_head != existing.head_sha or incoming_branch != existing.branch_ref:
            # Фингерпринт продюсера включает headSha, поэтому расхождение
            # означает подделанный или несогласованный payload. Молча вернуть
            # старый снапшот нельзя: вызов отрапортовал бы успех, а у ветки/PR
            # не появилось бы ни указателя, ни адресуемого по SHA снапшота.
            raise StructureProtocolError(
                f"fingerprint {fingerprint[:12]} already stored for "
                f"{existing.branch_ref}@{existing.head_sha[:12]}, "
                f"payload claims {incoming_branch}@{incoming_head[:12]}"
            )
        # Тир не должен залипать на том, который приехал первым: те же факты,
        # подтверждённые подписанным CI, обязаны поднять доверие снапшота.
        if is_more_trusted(trust_tier, existing.trust_tier):
            existing.trust_tier = trust_tier
            session.flush()
            # Повышение тира — это первый доверенный приход этих фактов: ниже
            # по коду должны отработать публикация, ретеншн и draft-claims,
            # которые на недоверенном приходе намеренно пропускались.
            return existing, False
        return existing, True
    provenance = incoming_provenance
    compressed, digest, size = compress_payload(validated)
    now = datetime.now(UTC)
    pr_raw = provenance.get("prNumber")
    pr_number = int(pr_raw) if isinstance(pr_raw, int) else None
    row = CodeStructureSnapshotModel(
        project_id=project_id,
        kind="structure_facts",
        fingerprint=fingerprint,
        schema_ref=str(validated.get("schemaRef") or ""),
        normalizer_version=NORMALIZER_VERSION,
        head_sha=str(provenance.get("headSha") or ""),
        branch_ref=str(provenance.get("branchRef") or "unknown"),
        pr_number=pr_number,
        is_default_branch=bool(provenance.get("isDefaultBranch")),
        scope=str(provenance.get("scope") or ""),
        trust_tier=trust_tier,
        payload_sha256=digest,
        payload_zlib=compressed,
        uncompressed_bytes=size,
        truncated=bool(provenance.get("truncated")),
        created=now,
    )
    try:
        with session.begin_nested():
            session.add(row)
            session.flush()
    except IntegrityError:
        raced = get_snapshot_by_fingerprint(session, project_id, fingerprint)
        if raced is None:
            raise
        return raced, True
    return row, False


def _clear_indexes(session: Session, snapshot_id: int) -> None:
    session.execute(delete(CodeEdgeModel).where(CodeEdgeModel.snapshot_id == snapshot_id))
    session.execute(delete(CodeContractModel).where(CodeContractModel.snapshot_id == snapshot_id))
    session.execute(delete(CodeEntityModel).where(CodeEntityModel.snapshot_id == snapshot_id))
    session.execute(delete(CodeBoundaryModel).where(CodeBoundaryModel.snapshot_id == snapshot_id))


def _objects(rows: Iterable[object], *, label: str) -> list[dict[str, object]]:
    return [as_object(raw, label=label) for raw in rows if isinstance(raw, dict)]


def _unique_by(
    rows: Iterable[object], *, label: str, key: Callable[[dict[str, object]], str]
) -> tuple[list[dict[str, object]], int]:
    """Оставить первую запись на каждый id, вернуть её и число отброшенных.

    Продюсер не гарантирует уникальность производных id: на реальном снапшоте
    cod-doc два symbol-узла из JSON-схем схлопывались в один ``observedId``.
    Индексы — производный артефакт под UNIQUE(snapshot_id, observed_id), поэтому
    дубль здесь отбрасывается, а не роняет весь ingest. Blob остаётся полным:
    ``replay_snapshot`` перестроит индексы из него, когда id станут уникальными.
    """
    seen: set[str] = set()
    kept: list[dict[str, object]] = []
    dropped = 0
    for item in _objects(rows, label=label):
        observed = key(item)
        if observed in seen:
            dropped += 1
            continue
        seen.add(observed)
        kept.append(item)
    return kept, dropped


def materialize_indexes(session: Session, snapshot: CodeStructureSnapshotModel) -> dict[str, int]:
    payload = get_snapshot_payload(snapshot)
    facts = as_object(payload.get("facts"), label="facts")
    _clear_indexes(session, snapshot.row_id)
    dropped: dict[str, int] = {}

    boundary_rows, dropped["boundaries"] = _unique_by(
        as_list(facts.get("boundaries"), label="boundaries"),
        label="boundary",
        key=lambda item: str(item.get("id") or ""),
    )
    for item in boundary_rows:
        session.add(
            CodeBoundaryModel(
                snapshot_id=snapshot.row_id,
                observed_id=str(item.get("id") or ""),
                kind=str(item.get("kind") or "unknown"),
                name=str(item.get("name") or ""),
                paths_json=list(as_list(item.get("paths") or [], label="paths")),
                confidence=str(item.get("confidence") or "inferred"),
            )
        )
    entity_rows, dropped["entities"] = _unique_by(
        as_list(facts.get("entities"), label="entities"),
        label="entity",
        key=lambda item: str(item.get("observedId") or item.get("id") or ""),
    )
    for item in entity_rows:
        session.add(
            CodeEntityModel(
                snapshot_id=snapshot.row_id,
                observed_id=str(item.get("observedId") or item.get("id") or ""),
                lineage_id=str(item.get("lineageId") or "") or None,
                kind=str(item.get("kind") or "unknown"),
                name=str(item.get("name") or item.get("label") or ""),
                path=str(item.get("path") or item.get("file") or ""),
                confidence=str(item.get("confidence") or "inferred"),
            )
        )
    contract_rows, dropped["contracts"] = _unique_by(
        as_list(facts.get("contracts"), label="contracts"),
        label="contract",
        key=lambda item: str(item.get("id") or ""),
    )
    for item in contract_rows:
        session.add(
            CodeContractModel(
                snapshot_id=snapshot.row_id,
                contract_id=str(item.get("id") or ""),
                entity_ref=str(item.get("entityRef") or item.get("entityId") or ""),
                name=str(item.get("name") or ""),
                kind=str(item.get("kind") or "unknown"),
                path=str(item.get("path") or item.get("file") or ""),
            )
        )
    # code_edge не имеет UNIQUE-ограничения: параллельные рёбра легитимны.
    edge_rows = _objects(as_list(facts.get("dependencies"), label="dependencies"), label="edge")
    for item in edge_rows:
        session.add(
            CodeEdgeModel(
                snapshot_id=snapshot.row_id,
                from_ref=str(item.get("from") or item.get("fromRef") or ""),
                to_ref=str(item.get("to") or item.get("toRef") or ""),
                relation=str(item.get("relation") or "depends"),
                confidence=str(item.get("confidence") or item.get("evidence") or "inferred"),
            )
        )
    duplicates = sum(dropped.values())
    if duplicates:
        logger.warning(
            "structure snapshot %s: %d дублирующихся id отброшено при построении "
            "индексов (%s); blob сохранён целиком",
            snapshot.fingerprint[:12],
            duplicates,
            ", ".join(f"{name}={count}" for name, count in sorted(dropped.items()) if count),
        )
    snapshot.normalizer_version = NORMALIZER_VERSION
    session.flush()
    return {
        "boundaries": len(boundary_rows),
        "entities": len(entity_rows),
        "contracts": len(contract_rows),
        "edges": len(edge_rows),
        "duplicates": duplicates,
    }


def _upsert_current(
    session: Session,
    project_id: int,
    *,
    slot: str,
    slot_key: str,
    snapshot_id: int,
) -> None:
    row = session.execute(
        select(StructureCurrentModel).where(
            StructureCurrentModel.project_id == project_id,
            StructureCurrentModel.slot == slot,
            StructureCurrentModel.slot_key == slot_key,
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            StructureCurrentModel(
                project_id=project_id,
                slot=slot,
                slot_key=slot_key,
                snapshot_id=snapshot_id,
            )
        )
    else:
        row.snapshot_id = snapshot_id
    session.flush()


def _publish_current(session: Session, snapshot: CodeStructureSnapshotModel) -> bool:
    if not can_publish_current(snapshot.trust_tier):
        return False
    if snapshot.pr_number is not None:
        _upsert_current(
            session,
            snapshot.project_id,
            slot="latest_pr",
            slot_key=str(snapshot.pr_number),
            snapshot_id=snapshot.row_id,
        )
        return True
    if snapshot.is_default_branch or snapshot.branch_ref in _DEFAULT_BRANCHES:
        _upsert_current(
            session,
            snapshot.project_id,
            slot="latest_main",
            slot_key=snapshot.branch_ref,
            snapshot_id=snapshot.row_id,
        )
        return True
    _upsert_current(
        session,
        snapshot.project_id,
        slot="latest_branch",
        slot_key=snapshot.branch_ref,
        snapshot_id=snapshot.row_id,
    )
    return True


def _gc_branch(session: Session, project_id: int, branch_ref: str, scope: str = "") -> int:
    # Ретеншн считается на (ветку, партицию): иначе N партиций делили бы одну
    # квоту в RETENTION_PER_BRANCH снапшотов и вытесняли бы друг друга.
    rows = list(
        session.execute(
            select(CodeStructureSnapshotModel)
            .where(
                CodeStructureSnapshotModel.project_id == project_id,
                CodeStructureSnapshotModel.branch_ref == branch_ref,
                CodeStructureSnapshotModel.scope == scope,
            )
            .order_by(CodeStructureSnapshotModel.created.desc())
        ).scalars()
    )
    keep = {row.row_id for row in rows[:RETENTION_PER_BRANCH]}
    referenced: set[int | None] = set()
    for column in (
        StructureFindingModel.last_seen_snapshot_id,
        StructureFindingModel.first_seen_snapshot_id,
        StructureFindingModel.resolved_by_snapshot_id,
    ):
        # Держим все снапшоты, на которые ссылается ЛЮБАЯ находка: колонки без
        # FK, и удалённый снапшот оставил бы в отчёте висячий id.
        referenced.update(
            session.execute(
                select(column).where(StructureFindingModel.project_id == project_id)
            ).scalars()
        )
    referenced.update(
        session.execute(
            select(StructureCurrentModel.snapshot_id).where(
                StructureCurrentModel.project_id == project_id
            )
        ).scalars()
    )
    deleted = 0
    for row in rows[RETENTION_PER_BRANCH:]:
        if row.row_id in keep or row.row_id in referenced:
            continue
        session.delete(row)
        deleted += 1
    return deleted


def _bootstrap_draft_claims(
    session: Session,
    project_id: int,
    snapshot: CodeStructureSnapshotModel,
) -> int:
    payload = get_snapshot_payload(snapshot)
    facts = as_object(payload.get("facts"), label="facts")
    now = datetime.now(UTC)
    created = 0
    # Существующие claim_id забираем одним запросом: раньше на каждый контракт
    # уходил отдельный SELECT внутри транзакции ingest.
    known_claim_ids = set(
        session.execute(
            select(DocCodeClaimModel.claim_id).where(DocCodeClaimModel.project_id == project_id)
        ).scalars()
    )
    for raw in as_list(facts.get("contracts"), label="contracts"):
        if not isinstance(raw, dict):
            continue
        item = as_object(raw, label="contract")
        subject = str(item.get("id") or "")
        if not subject:
            continue
        claim_id = stable_id("draft", subject)
        if claim_id in known_claim_ids:
            continue
        known_claim_ids.add(claim_id)
        session.add(
            DocCodeClaimModel(
                project_id=project_id,
                claim_id=claim_id,
                kind="exports",
                status="draft",
                content_hash=sha256_text(subject),
                subject_ref=subject,
                expected_json={"bootstrap": True},
                provenance="structure-bootstrap",
                created=now,
                updated=now,
            )
        )
        created += 1
    session.flush()
    return created


def ingest_structure(
    session: Session,
    project_id: int,
    *,
    facts: dict[str, object],
    assessment: dict[str, object] | None = None,
    trust_tier: str = "untrusted",
    project_slug: str = "project",
    actor: str = "cli",
) -> dict[str, object]:
    """Idempotent blob-first ingest. Publishes current only for trusted tiers."""
    started = datetime.now(UTC)
    tier = normalize_trust_tier(trust_tier)
    snapshot, idempotent = _get_or_create_snapshot(session, project_id, facts, trust_tier=tier)
    counts = {"boundaries": 0, "entities": 0, "contracts": 0, "edges": 0}
    if not idempotent:
        counts = materialize_indexes(session, snapshot)
        published = _publish_current(session, snapshot)
        # Недоверенный снапшот хранится для осмотра, но не двигает общее
        # состояние. Ретеншн считается по ветке, которую называет сам payload,
        # поэтому серия untrusted-ingest'ов с `branchRef: main` вытесняла
        # доверенную историю; draft-claims из непроверенного payload — та же
        # категория. REST-ingest смонтирован без аутентификации, так что это
        # единственная преграда.
        if can_publish_current(snapshot.trust_tier):
            _gc_branch(session, project_id, snapshot.branch_ref, snapshot.scope)
            bootstrap = _bootstrap_draft_claims(session, project_id, snapshot)
        else:
            bootstrap = 0
    else:
        published = _publish_current(session, snapshot)
        bootstrap = 0

    assessment_header: dict[str, object] | None = None
    drift: dict[str, object] | None = None
    if assessment is not None:
        assessment_header, drift = _ingest_assessment(
            session,
            project_id,
            snapshot,
            assessment,
            trust_tier=tier,
            project_slug=project_slug,
        )

    duration_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
    result: dict[str, object] = {
        "idempotent": idempotent,
        "publishedCurrent": published,
        "trustTier": tier,
        "snapshot": _header(snapshot),
        "indexes": counts,
        "draftClaims": bootstrap,
        "assessment": assessment_header,
        "drift": drift,
        "durationMs": duration_ms,
        "bytes": snapshot.uncompressed_bytes,
    }
    activity_service.emit(
        session,
        project_id,
        "structure.ingested",
        actor_kind="system",
        actor_id=actor,
        scope_kind="structure_snapshot",
        scope_id=str(snapshot.row_id),
        payload={
            "idempotent": idempotent,
            "fingerprint": snapshot.fingerprint,
            "bytes": snapshot.uncompressed_bytes,
            "durationMs": duration_ms,
            "trustTier": tier,
            "publishedCurrent": published,
            "duplicatesDropped": counts.get("duplicates", 0),
        },
        summary=f"structure ingest {snapshot.fingerprint[:12]} idempotent={idempotent}",
    )
    return result


def _ingest_assessment(
    session: Session,
    project_id: int,
    snapshot: CodeStructureSnapshotModel,
    payload: dict[str, object],
    *,
    trust_tier: str,
    project_slug: str,
) -> tuple[dict[str, object], dict[str, object] | None]:
    validated = validate_structure_assessment(payload)
    if str(validated.get("factsFingerprint")) != snapshot.fingerprint:
        raise StructureProtocolError("assessment factsFingerprint does not match snapshot")
    fingerprint = str(validated["fingerprint"])
    existing = session.execute(
        select(StructureAssessmentModel).where(
            StructureAssessmentModel.project_id == project_id,
            StructureAssessmentModel.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    if existing is None:
        compressed, digest, size = compress_payload(validated)
        existing = StructureAssessmentModel(
            project_id=project_id,
            snapshot_id=snapshot.row_id,
            fingerprint=fingerprint,
            facts_fingerprint=str(validated["factsFingerprint"]),
            obligations_revision=str(validated.get("obligationsRevision") or "none"),
            coverage_hash=str(validated["coverageHash"]),
            thresholds_hash=str(validated["thresholdsHash"]),
            temporal_alignment=str(validated["temporalAlignment"]),
            trust_tier=trust_tier,
            schema_ref=str(validated.get("schemaRef") or ""),
            normalizer_version=NORMALIZER_VERSION,
            payload_sha256=digest,
            payload_zlib=compressed,
            uncompressed_bytes=size,
            created=datetime.now(UTC),
        )
        try:
            with session.begin_nested():
                session.add(existing)
                session.flush()
        except IntegrityError:
            existing = session.execute(
                select(StructureAssessmentModel).where(
                    StructureAssessmentModel.project_id == project_id,
                    StructureAssessmentModel.fingerprint == fingerprint,
                )
            ).scalar_one()
    header = {
        "assessmentId": existing.row_id,
        "fingerprint": existing.fingerprint,
        "factsFingerprint": existing.facts_fingerprint,
        "temporalAlignment": existing.temporal_alignment,
        "trustTier": existing.trust_tier,
        "uncompressedBytes": existing.uncompressed_bytes,
    }
    if not can_publish_current(snapshot.trust_tier) or not can_publish_current(trust_tier):
        return header, None
    facts_payload = get_snapshot_payload(snapshot)
    obligations = export_obligations(
        session, project_id, project_slug=project_slug, head_sha=snapshot.head_sha
    )
    claims = list(
        session.execute(
            select(DocCodeClaimModel).where(
                DocCodeClaimModel.project_id == project_id,
                DocCodeClaimModel.status == "confirmed",
            )
        ).scalars()
    )
    claim_dicts = [
        {
            "claim_id": claim.claim_id,
            "kind": claim.kind,
            "status": claim.status,
            "subject_ref": claim.subject_ref,
            "content_hash": claim.content_hash,
        }
        for claim in claims
    ]
    suggestions = suggest_obligation_links(
        as_list(obligations.get("obligations"), label="obligations"), facts_payload
    )
    persist_link_suggestions(session, project_id, suggestions)
    drift = compute_structure_drift(
        facts_payload=facts_payload,
        assessment_payload=validated,
        obligations_export=obligations,
        confirmed_claims=claim_dicts,
        link_suggestions=suggestions,
        scope=snapshot.scope,
    )
    reconcile_findings(
        session,
        project_id,
        drift,
        snapshot_id=snapshot.row_id,
        temporal_alignment=str(validated.get("temporalAlignment") or "unknown"),
        truncated=snapshot.truncated,
        scope=snapshot.scope,
    )
    return header, drift


def replay_snapshot(session: Session, project_id: int, snapshot_id: int) -> dict[str, object]:
    row = session.get(CodeStructureSnapshotModel, snapshot_id)
    if row is None or row.project_id != project_id:
        raise StructureProtocolError("snapshot not found")
    counts = materialize_indexes(session, row)
    return {
        "snapshotId": snapshot_id,
        "normalizerVersion": row.normalizer_version,
        "indexes": counts,
    }


def get_latest(
    session: Session,
    project_id: int,
    *,
    head_sha: str | None = None,
    branch_ref: str | None = None,
    pr_number: int | None = None,
) -> CodeStructureSnapshotModel | None:
    if not head_sha and pr_number is None and not branch_ref:
        raise StructureProtocolError("latest without branch context is forbidden")
    if head_sha:
        # Тир фильтруется здесь же: ветка по headSha минует ``structure_current``,
        # и без фильтра недоверенный снапшот с тем же headSha выдавался бы как
        # закреплённый граф — вопреки инварианту «untrusted не становится current».
        return session.execute(
            select(CodeStructureSnapshotModel)
            .where(
                CodeStructureSnapshotModel.project_id == project_id,
                CodeStructureSnapshotModel.head_sha == head_sha,
                CodeStructureSnapshotModel.trust_tier.in_(tuple(PUBLISHABLE_TRUST)),
            )
            .order_by(CodeStructureSnapshotModel.created.desc())
            .limit(1)
        ).scalar_one_or_none()
    if pr_number is not None:
        slot, key = "latest_pr", str(pr_number)
    elif branch_ref in _DEFAULT_BRANCHES:
        slot, key = "latest_main", str(branch_ref)
    else:
        slot, key = "latest_branch", str(branch_ref)
    current = session.execute(
        select(StructureCurrentModel).where(
            StructureCurrentModel.project_id == project_id,
            StructureCurrentModel.slot == slot,
            StructureCurrentModel.slot_key == key,
        )
    ).scalar_one_or_none()
    if current is None:
        return None
    return session.get(CodeStructureSnapshotModel, current.snapshot_id)


def require_pinned_snapshot(
    session: Session,
    project_id: int,
    *,
    head_sha: str | None = None,
    snapshot_fingerprint: str | None = None,
) -> CodeStructureSnapshotModel:
    if not head_sha and not snapshot_fingerprint:
        raise StructureProtocolError("structure_context requires headSha or snapshotFingerprint")
    if snapshot_fingerprint:
        row = get_snapshot_by_fingerprint(session, project_id, snapshot_fingerprint)
        if row is None:
            raise StructureProtocolError("snapshot fingerprint not found")
        if not can_publish_current(row.trust_tier):
            # Тот же инвариант, что и на ветке по headSha: недоверенный снапшот
            # хранится для осмотра, но закреплённым контекстом не становится.
            raise StructureProtocolError("snapshot is untrusted and cannot be pinned")
        return row
    row = get_latest(session, project_id, head_sha=head_sha)
    if row is None:
        raise StructureProtocolError("snapshot for headSha not found")
    return row


def list_entities(
    session: Session,
    snapshot_id: int,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    return _page(
        session.execute(
            select(CodeEntityModel)
            .where(CodeEntityModel.snapshot_id == snapshot_id)
            .order_by(CodeEntityModel.observed_id)
        ).scalars(),
        cursor=cursor,
        limit=limit,
        render=_entity_dict,
    )


def list_contracts(
    session: Session,
    snapshot_id: int,
    *,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, object]:
    return _page(
        session.execute(
            select(CodeContractModel)
            .where(CodeContractModel.snapshot_id == snapshot_id)
            .order_by(CodeContractModel.contract_id)
        ).scalars(),
        cursor=cursor,
        limit=limit,
        render=_contract_dict,
    )


def diff_snapshots(
    session: Session,
    project_id: int,
    left_id: int,
    right_id: int,
) -> dict[str, object]:
    left = session.get(CodeStructureSnapshotModel, left_id)
    right = session.get(CodeStructureSnapshotModel, right_id)
    if (
        left is None
        or right is None
        or left.project_id != project_id
        or right.project_id != project_id
    ):
        raise StructureProtocolError("snapshot not found")
    left_entities = {
        row.observed_id: row
        for row in session.execute(
            select(CodeEntityModel).where(CodeEntityModel.snapshot_id == left_id)
        ).scalars()
    }
    right_entities = {
        row.observed_id: row
        for row in session.execute(
            select(CodeEntityModel).where(CodeEntityModel.snapshot_id == right_id)
        ).scalars()
    }
    removed = sorted(set(left_entities) - set(right_entities))
    created = sorted(set(right_entities) - set(left_entities))
    moved: list[dict[str, str]] = []
    changed: list[str] = []
    for observed_id, right_row in right_entities.items():
        left_row = left_entities.get(observed_id)
        if left_row is None:
            continue
        if left_row.path != right_row.path:
            moved.append({"observedId": observed_id, "from": left_row.path, "to": right_row.path})
        elif left_row.kind != right_row.kind or left_row.name != right_row.name:
            changed.append(observed_id)
    return {
        "left": _header(left),
        "right": _header(right),
        "created": created,
        "removed": removed,
        "moved": moved,
        "changed": changed,
        "unknown": [],
    }


def _entity_dict(row: CodeEntityModel) -> dict[str, object]:
    return {
        "observedId": row.observed_id,
        "lineageId": row.lineage_id,
        "kind": row.kind,
        "name": row.name,
        "path": row.path,
        "confidence": row.confidence,
    }


def _contract_dict(row: CodeContractModel) -> dict[str, object]:
    return {
        "id": row.contract_id,
        "entityRef": row.entity_ref,
        "name": row.name,
        "kind": row.kind,
        "path": row.path,
    }


def _page[T](
    rows: Iterable[T],
    *,
    cursor: str | None,
    limit: int,
    render: Callable[[T], dict[str, object]],
) -> dict[str, object]:
    items = list(rows)
    start = parse_cursor(cursor)
    chunk = items[start : start + limit]
    next_cursor = str(start + limit) if start + limit < len(items) else None
    return {
        "items": [render(item) for item in chunk],
        "nextCursor": next_cursor,
        "total": len(items),
    }
