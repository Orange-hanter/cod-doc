"""Blob-first structure snapshots, assessments, scoped indexes and drift state.

Stores immutable zlib-compressed producer payloads (facts + assessment)
separately from DB↔Markdown projection drift. Scoped code_* indexes are
materialized after a successful ingest for query/context slices.

Revision ID: 0030_structure_platform
Revises: 0029_drop_audit_log
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030_structure_platform"
down_revision = "0029_drop_audit_log"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "code_structure_snapshot",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("schema_ref", sa.String(128), nullable=False),
        sa.Column("normalizer_version", sa.Integer, nullable=False),
        sa.Column("head_sha", sa.String(64), nullable=False),
        sa.Column("branch_ref", sa.String(255), nullable=False),
        sa.Column("pr_number", sa.Integer, nullable=True),
        sa.Column("is_default_branch", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("scope", sa.String(512), nullable=False, server_default=""),
        sa.Column("trust_tier", sa.String(32), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload_zlib", sa.LargeBinary, nullable=False),
        sa.Column("uncompressed_bytes", sa.Integer, nullable=False),
        sa.Column("truncated", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "superseded_by",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "project_id", "kind", "fingerprint", name="uq_structure_snapshot_fingerprint"
        ),
    )
    op.create_index(
        "ix_structure_snapshot_head",
        "code_structure_snapshot",
        ["project_id", "head_sha"],
    )
    op.create_index(
        "ix_structure_snapshot_branch",
        "code_structure_snapshot",
        ["project_id", "branch_ref", "created"],
    )

    op.create_table(
        "structure_assessment",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("facts_fingerprint", sa.String(64), nullable=False),
        sa.Column("obligations_revision", sa.String(128), nullable=False),
        sa.Column("coverage_hash", sa.String(64), nullable=False),
        sa.Column("thresholds_hash", sa.String(64), nullable=False),
        sa.Column("temporal_alignment", sa.String(16), nullable=False),
        sa.Column("trust_tier", sa.String(32), nullable=False),
        sa.Column("schema_ref", sa.String(128), nullable=False),
        sa.Column("normalizer_version", sa.Integer, nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("payload_zlib", sa.LargeBinary, nullable=False),
        sa.Column("uncompressed_bytes", sa.Integer, nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "fingerprint", name="uq_structure_assessment_fp"),
    )
    op.create_index(
        "ix_structure_assessment_facts",
        "structure_assessment",
        ["project_id", "facts_fingerprint"],
    )

    op.create_table(
        "structure_current",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("slot", sa.String(32), nullable=False),
        sa.Column("slot_key", sa.String(128), nullable=False),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("project_id", "slot", "slot_key", name="uq_structure_current_slot"),
    )

    op.create_table(
        "code_boundary",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_id", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("paths_json", sa.JSON, nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False, server_default="inferred"),
        sa.UniqueConstraint("snapshot_id", "observed_id", name="uq_code_boundary_observed"),
    )
    op.create_index("ix_code_boundary_snapshot", "code_boundary", ["snapshot_id"])

    op.create_table(
        "code_entity",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("observed_id", sa.String(255), nullable=False),
        sa.Column("lineage_id", sa.String(255), nullable=True),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("path", sa.String(512), nullable=False, server_default=""),
        sa.Column("confidence", sa.String(32), nullable=False, server_default="inferred"),
        sa.UniqueConstraint("snapshot_id", "observed_id", name="uq_code_entity_observed"),
    )
    op.create_index("ix_code_entity_path", "code_entity", ["snapshot_id", "path"])
    op.create_index("ix_code_entity_lineage", "code_entity", ["lineage_id"])

    op.create_table(
        "code_contract",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contract_id", sa.String(255), nullable=False),
        sa.Column("entity_ref", sa.String(255), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("path", sa.String(512), nullable=False, server_default=""),
        sa.UniqueConstraint("snapshot_id", "contract_id", name="uq_code_contract_id"),
    )
    op.create_index("ix_code_contract_entity", "code_contract", ["snapshot_id", "entity_ref"])

    op.create_table(
        "code_edge",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "snapshot_id",
            sa.Integer,
            sa.ForeignKey("code_structure_snapshot.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_ref", sa.String(255), nullable=False),
        sa.Column("to_ref", sa.String(255), nullable=False),
        sa.Column("relation", sa.String(32), nullable=False),
        sa.Column("confidence", sa.String(32), nullable=False, server_default="inferred"),
    )
    op.create_index("ix_code_edge_snapshot", "code_edge", ["snapshot_id"])
    op.create_index("ix_code_edge_from", "code_edge", ["snapshot_id", "from_ref"])
    op.create_index("ix_code_edge_to", "code_edge", ["snapshot_id", "to_ref"])

    op.create_table(
        "doc_code_claim",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("claim_id", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("doc_key", sa.String(255), nullable=True),
        sa.Column("section_anchor", sa.String(255), nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("subject_ref", sa.String(255), nullable=False),
        sa.Column("expected_json", sa.JSON, nullable=False),
        sa.Column("provenance", sa.String(32), nullable=False, server_default="import"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "claim_id", name="uq_doc_code_claim_id"),
    )
    op.create_index("ix_doc_code_claim_subject", "doc_code_claim", ["project_id", "subject_ref"])

    op.create_table(
        "structure_waiver",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("finding_fingerprint", sa.String(128), nullable=False),
        sa.Column("owner", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text, nullable=False),
        sa.Column("scope", sa.String(255), nullable=False, server_default=""),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id", "finding_fingerprint", name="uq_structure_waiver_finding"
        ),
    )

    op.create_table(
        "structure_finding",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scope", sa.String(255), nullable=False, server_default=""),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("rule_id", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(16), nullable=False, server_default="medium"),
        sa.Column("subject_refs_json", sa.JSON, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("remediation_target", sa.String(32), nullable=False, server_default="code"),
        sa.Column("evidence_json", sa.JSON, nullable=False),
        sa.Column("missing_evidence_json", sa.JSON, nullable=False),
        sa.Column("first_seen_snapshot_id", sa.Integer, nullable=True),
        sa.Column("last_seen_snapshot_id", sa.Integer, nullable=True),
        sa.Column("resolved_by_snapshot_id", sa.Integer, nullable=True),
        sa.Column("promoted_task_id", sa.String(32), nullable=True),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("project_id", "scope", "fingerprint", name="uq_structure_finding_fp"),
    )
    op.create_index("ix_structure_finding_status", "structure_finding", ["project_id", "status"])
    op.create_index(
        "ix_structure_finding_scope", "structure_finding", ["project_id", "scope", "status"]
    )

    op.create_table(
        "structure_link_suggestion",
        sa.Column("row_id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "project_id",
            sa.Integer,
            sa.ForeignKey("project.row_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("obligation_ref", sa.String(255), nullable=False),
        sa.Column("contract_ref", sa.String(255), nullable=False),
        sa.Column("score", sa.Integer, nullable=False, server_default="0"),
        sa.Column("confidence", sa.String(32), nullable=False, server_default="heuristic"),
        sa.Column("reasons_json", sa.JSON, nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("created", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "project_id",
            "obligation_ref",
            "contract_ref",
            name="uq_structure_link_suggestion",
        ),
    )


def downgrade() -> None:
    op.drop_table("structure_link_suggestion")
    op.drop_index("ix_structure_finding_status", table_name="structure_finding")
    op.drop_table("structure_finding")
    op.drop_table("structure_waiver")
    op.drop_index("ix_doc_code_claim_subject", table_name="doc_code_claim")
    op.drop_table("doc_code_claim")
    op.drop_index("ix_code_edge_to", table_name="code_edge")
    op.drop_index("ix_code_edge_from", table_name="code_edge")
    op.drop_index("ix_code_edge_snapshot", table_name="code_edge")
    op.drop_table("code_edge")
    op.drop_index("ix_code_contract_entity", table_name="code_contract")
    op.drop_table("code_contract")
    op.drop_index("ix_code_entity_lineage", table_name="code_entity")
    op.drop_index("ix_code_entity_path", table_name="code_entity")
    op.drop_table("code_entity")
    op.drop_index("ix_code_boundary_snapshot", table_name="code_boundary")
    op.drop_table("code_boundary")
    op.drop_table("structure_current")
    op.drop_index("ix_structure_assessment_facts", table_name="structure_assessment")
    op.drop_table("structure_assessment")
    op.drop_index("ix_structure_snapshot_branch", table_name="code_structure_snapshot")
    op.drop_index("ix_structure_snapshot_head", table_name="code_structure_snapshot")
    op.drop_table("code_structure_snapshot")
