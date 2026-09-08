"""Structure snapshot, assessment, scoped indexes, claims, waivers, findings."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow


class CodeStructureSnapshotModel(Base):
    __tablename__ = "code_structure_snapshot"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "kind", "fingerprint", name="uq_structure_snapshot_fingerprint"
        ),
        Index("ix_structure_snapshot_head", "project_id", "head_sha"),
        Index("ix_structure_snapshot_branch", "project_id", "branch_ref", "created"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    branch_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    pr_number: Mapped[int | None] = mapped_column(Integer)
    is_default_branch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    scope: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_zlib: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uncompressed_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    superseded_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("code_structure_snapshot.row_id", ondelete="SET NULL")
    )

    assessments: Mapped[list[StructureAssessmentModel]] = relationship(back_populates="snapshot")


class StructureAssessmentModel(Base):
    __tablename__ = "structure_assessment"
    __table_args__ = (
        UniqueConstraint("project_id", "fingerprint", name="uq_structure_assessment_fp"),
        Index("ix_structure_assessment_facts", "project_id", "facts_fingerprint"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    facts_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    obligations_revision: Mapped[str] = mapped_column(String(128), nullable=False)
    coverage_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    thresholds_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    temporal_alignment: Mapped[str] = mapped_column(String(16), nullable=False)
    trust_tier: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_zlib: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    uncompressed_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    snapshot: Mapped[CodeStructureSnapshotModel] = relationship(back_populates="assessments")


class StructureCurrentModel(Base):
    __tablename__ = "structure_current"
    __table_args__ = (
        UniqueConstraint("project_id", "slot", "slot_key", name="uq_structure_current_slot"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    slot: Mapped[str] = mapped_column(String(32), nullable=False)
    slot_key: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )


class CodeBoundaryModel(Base):
    __tablename__ = "code_boundary"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "observed_id", name="uq_code_boundary_observed"),
        Index("ix_code_boundary_snapshot", "snapshot_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    paths_json: Mapped[list[object]] = mapped_column(JSON, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="inferred")


class CodeEntityModel(Base):
    __tablename__ = "code_entity"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "observed_id", name="uq_code_entity_observed"),
        Index("ix_code_entity_path", "snapshot_id", "path"),
        Index("ix_code_entity_lineage", "lineage_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    observed_id: Mapped[str] = mapped_column(String(255), nullable=False)
    lineage_id: Mapped[str | None] = mapped_column(String(255))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="inferred")


class CodeContractModel(Base):
    __tablename__ = "code_contract"
    __table_args__ = (
        UniqueConstraint("snapshot_id", "contract_id", name="uq_code_contract_id"),
        Index("ix_code_contract_entity", "snapshot_id", "entity_ref"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    contract_id: Mapped[str] = mapped_column(String(255), nullable=False)
    entity_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    path: Mapped[str] = mapped_column(String(512), nullable=False, default="")


class CodeEdgeModel(Base):
    __tablename__ = "code_edge"
    __table_args__ = (
        Index("ix_code_edge_snapshot", "snapshot_id"),
        Index("ix_code_edge_from", "snapshot_id", "from_ref"),
        Index("ix_code_edge_to", "snapshot_id", "to_ref"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
        nullable=False,
    )
    from_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    to_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="inferred")


class DocCodeClaimModel(Base):
    __tablename__ = "doc_code_claim"
    __table_args__ = (
        UniqueConstraint("project_id", "claim_id", name="uq_doc_code_claim_id"),
        Index("ix_doc_code_claim_subject", "project_id", "subject_ref"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    doc_key: Mapped[str | None] = mapped_column(String(255))
    section_anchor: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    expected_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="import")
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class StructureWaiverModel(Base):
    __tablename__ = "structure_waiver"
    __table_args__ = (
        UniqueConstraint("project_id", "finding_fingerprint", name="uq_structure_waiver_finding"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    finding_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class StructureFindingModel(Base):
    __tablename__ = "structure_finding"
    __table_args__ = (
        UniqueConstraint("project_id", "scope", "fingerprint", name="uq_structure_finding_fp"),
        Index("ix_structure_finding_status", "project_id", "status"),
        Index("ix_structure_finding_scope", "project_id", "scope", "status"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    # Партиция, внутри которой находка сверяется. Пустая строка — «весь
    # репозиторий», единственный вариант до появления партиционирования.
    scope: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="open")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    subject_refs_json: Mapped[list[object]] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    remediation_target: Mapped[str] = mapped_column(String(32), nullable=False, default="code")
    evidence_json: Mapped[list[object]] = mapped_column(JSON, nullable=False)
    missing_evidence_json: Mapped[list[object]] = mapped_column(JSON, nullable=False)
    first_seen_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    last_seen_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    resolved_by_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    promoted_task_id: Mapped[str | None] = mapped_column(String(32))
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class StructureLinkSuggestionModel(Base):
    __tablename__ = "structure_link_suggestion"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "obligation_ref",
            "contract_ref",
            name="uq_structure_link_suggestion",
        ),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    obligation_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    contract_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False, default="heuristic")
    reasons_json: Mapped[list[object]] = mapped_column(JSON, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
