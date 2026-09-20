"""Document + Section + Link — the documentation tree + outgoing references."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003 — runtime use by Mapped[datetime]
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, _utcnow

if TYPE_CHECKING:
    from .project import ProjectModel


class DocNodeModel(Base):
    """Раздел дерева документации (ADO-116).

    Аналог ``plan_section`` для планов и ``story_section`` для историй:
    стабильный ключ, человеческое название, явный порядок. До него дерево на
    экране документов строилось из ``doc_key.split("/")``, то есть показывало
    файловую кучу, а не информационную архитектуру.

    ``parent_id`` — самоссылка, чтобы крупный раздел можно было разделить на
    подузлы без миграции. Дефолтное дерево сеется плоским.
    """

    __tablename__ = "doc_node"
    __table_args__ = (
        UniqueConstraint("project_id", "node_key", name="uq_doc_node_project_key"),
        Index("ix_doc_node_project", "project_id", "position"),
        Index("ix_doc_node_parent", "parent_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    node_key: Mapped[str] = mapped_column(String(64), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("doc_node.row_id", ondelete="CASCADE")
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    #: Проза о том, что в разделе должно лежать. Её читают и человек в шапке
    #: списка, и агент-куратор, когда предлагает раскладку.
    intent: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Подсказка классификатору, а не ограничение: раздел не отвергает
    #: документ другого типа, он лишь объявляет ожидание.
    expected_types: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    #: Порог «раздел пуст»: ниже него куратор считает раздел непокрытым.
    min_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_inbox: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("0")
    )


class DocNodeSuggestionModel(Base):
    """Предложение раскладки документа в раздел — форма ``link_suggestion``.

    Куратор ничего не применяет молча: предложение живёт в ``pending``, пока
    человек не примет или не отклонит его.
    """

    __tablename__ = "doc_node_suggestion"
    __table_args__ = (
        UniqueConstraint("document_id", "node_id", name="uq_doc_node_suggestion"),
        Index("ix_doc_node_suggestion_state", "state", "document_id"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="CASCADE"), nullable=False
    )
    node_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("doc_node.row_id", ondelete="CASCADE"), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, server_default="0")
    evidence: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class DocumentModel(Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("project_id", "doc_key", name="uq_document_project_key"),
        Index("ix_document_type", "type", "status"),
        Index("ix_document_sensitivity", "sensitivity"),
        Index("ix_document_node", "node_id", "node_position"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    doc_key: Mapped[str] = mapped_column(String(255), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_of_truth: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sensitivity: Mapped[str] = mapped_column(String(16), nullable=False, default="internal")
    owner: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    preamble: Mapped[str] = mapped_column(Text, nullable=False, default="")
    frontmatter_json: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict, server_default=text("'{}'")
    )
    # ADO-010 (F7): verbatim YAML block of the imported file, without the `---`
    # fences. Re-emitted byte-for-byte on export while the DB-authoritative
    # fields still agree with it; NULL for DB-authored docs, which fall back to
    # deterministic serialisation. Keeps key order, flow-style lists and
    # unquoted dates that a JSON round-trip would destroy.
    frontmatter_raw: Mapped[str | None] = mapped_column(Text)
    # ADO-010: did the source file carry an `# H1`? The importer moves it into
    # `title`, so the renderer must put it back — but only where it was.
    # NULL = unknown (DB-authored) and renders the heading, as the projection
    # contract intends.
    title_in_body: Mapped[bool | None] = mapped_column(Boolean)
    projection_hash: Mapped[str | None] = mapped_column(String(64))
    # PCA-928: sha256 of the first 4 KB of the source file at last import.
    # Used by scan_folder() to detect "changed" status without re-parsing.
    content_sha256_head: Mapped[str | None] = mapped_column(String(64))
    # Раздел дерева документации. Nullable и SET NULL: удаление раздела обязано
    # осиротить документ, но не удалить его. Документ без раздела валиден и
    # показывается в Инбоксе — это и есть сигнал «разложить».
    node_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("doc_node.row_id", ondelete="SET NULL")
    )
    #: Ручной порядок внутри раздела; NULL сортируется последним.
    node_position: Mapped[int | None] = mapped_column(Integer)
    created: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_reviewed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[ProjectModel] = relationship(back_populates="documents")
    sections: Mapped[list[SectionModel]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="SectionModel.position",
    )


class SectionModel(Base):
    __tablename__ = "section"
    __table_args__ = (
        UniqueConstraint("document_id", "anchor", name="uq_section_document_anchor"),
        Index("ix_section_position", "document_id", "position"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="CASCADE"), nullable=False
    )
    anchor: Mapped[str] = mapped_column(String(255), nullable=False)
    heading: Mapped[str] = mapped_column(Text, nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    document: Mapped[DocumentModel] = relationship(back_populates="sections")
    outgoing_links: Mapped[list[LinkModel]] = relationship(
        back_populates="from_section",
        cascade="all, delete-orphan",
    )


class LinkModel(Base):
    __tablename__ = "link"
    __table_args__ = (
        Index("ix_link_target_doc", "to_doc_key"),
        Index("ix_link_target_task", "to_task_id"),
        Index("ix_link_target_adr", "to_adr_id"),
        Index("ix_link_target_file", "to_file_path"),
        Index("ix_link_unresolved", "resolved"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    from_section_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("section.row_id", ondelete="CASCADE"), nullable=False
    )
    raw: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    to_doc_key: Mapped[str | None] = mapped_column(String(255))
    to_task_id: Mapped[str | None] = mapped_column(String(32))
    to_story_id: Mapped[str | None] = mapped_column(String(32))
    to_adr_id: Mapped[str | None] = mapped_column(String(16))
    # OBI-020: code-ref fields (kind == 'code')
    to_file_path: Mapped[str | None] = mapped_column(String(512))
    to_symbol: Mapped[str | None] = mapped_column(String(128))
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_checked: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broken_reason: Mapped[str | None] = mapped_column(Text)

    from_section: Mapped[SectionModel] = relationship(back_populates="outgoing_links")
