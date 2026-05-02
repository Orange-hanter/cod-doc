"""Module + ModuleDependency + ModuleCode."""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class ModuleModel(Base):
    __tablename__ = "module"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("project.row_id", ondelete="CASCADE"), nullable=False
    )
    module_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    spec_doc_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("document.row_id", ondelete="SET NULL")
    )
    plan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("plan.row_id", ondelete="SET NULL")
    )

    code_paths: Mapped[list[ModuleCodeModel]] = relationship(
        back_populates="module",
        cascade="all, delete-orphan",
    )
    outgoing_deps: Mapped[list[ModuleDependencyModel]] = relationship(
        back_populates="from_module_rel",
        cascade="all, delete-orphan",
        foreign_keys="ModuleDependencyModel.from_module",
    )


class ModuleDependencyModel(Base):
    __tablename__ = "module_dependency"
    __table_args__ = (
        UniqueConstraint("from_module", "to_module", name="uq_module_dependency_edge"),
        CheckConstraint("from_module <> to_module", name="ck_module_dependency_no_self_loop"),
    )

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_module: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    to_module: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(Text)

    from_module_rel: Mapped[ModuleModel] = relationship(
        back_populates="outgoing_deps",
        foreign_keys=[from_module],
    )
    to_module_rel: Mapped[ModuleModel] = relationship(foreign_keys=[to_module])


class ModuleCodeModel(Base):
    __tablename__ = "module_code"

    row_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    module_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("module.row_id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)

    module: Mapped[ModuleModel] = relationship(back_populates="code_paths")
