"""Repo index — files / symbols / imports (OBI-030).

Mirrors migration ``0022_repo_index``.
"""

from __future__ import annotations

from datetime import datetime  # noqa: TC003

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, _utcnow


class RepoFileModel(Base):
    """Indexed file metadata for a project."""

    __tablename__ = "repo_file"
    __table_args__ = (
        UniqueConstraint("project_id", "path", name="uq_repo_file_project_path"),
        Index("ix_repo_file_lang", "project_id", "language"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    language: Mapped[str | None] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha1: Mapped[str] = mapped_column(String(40), nullable=False)
    scanned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class RepoSymbolModel(Base):
    """Symbol extracted from a Python (or similar) source file."""

    __tablename__ = "repo_symbol"
    __table_args__ = (
        Index("ix_repo_symbol_file", "file_id"),
        Index("ix_repo_symbol_name", "name"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repo_file.row_id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_name: Mapped[str | None] = mapped_column(String(255))


class RepoImportModel(Base):
    """Import statement extracted from a source file."""

    __tablename__ = "repo_import"
    __table_args__ = (
        Index("ix_repo_import_file", "file_id"),
        Index("ix_repo_import_module", "module"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    file_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("repo_file.row_id", ondelete="CASCADE"), nullable=False
    )
    module: Mapped[str] = mapped_column(String(255), nullable=False)
    line: Mapped[int] = mapped_column(Integer, nullable=False)
