"""SQLAlchemy ORM models — infra-side mapping of domain entities.

Schema mirrors docs/system/DATA_MODEL.md §3.1-3.13 + §4.3a (document_body view).
Sections 3.14 / 3.15 (embedding, proposal) — later migrations.

Convention: timestamp columns (`created`, `last_updated`, `at`) are NOT NULL
without a server_default — they are always populated through the ORM
(Python-side default `_utcnow`). Raw SQL inserts must specify them explicitly.

Modules in this package each cover one DATA_MODEL chapter — kept tiny so
adding a column or relationship doesn't require navigating a 500-line wall.
The `from .X import …` lines below are NOT optional: SQLAlchemy resolves
string-form relationships through `Base.metadata`, which only knows about
classes that have been imported. Keep this file as the single registration
point.
"""

from __future__ import annotations

from .base import Base, _utcnow
from .documents import DocumentModel, LinkModel, SectionModel
from .modules import ModuleCodeModel, ModuleDependencyModel, ModuleModel
from .plans import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    TaskModel,
)
from .project import ProjectModel
from .revisions import AuditLogModel, RevisionModel
from .stories import StoryAcceptanceModel, StoryLinkModel, UserStoryModel
from .tags import DocumentTagModel, StoryTagModel, TagModel, TaskTagModel
from .traces import TraceCallModel

__all__ = [
    "AffectedFileModel",
    "AuditLogModel",
    "Base",
    "DependencyModel",
    "DocumentModel",
    "DocumentTagModel",
    "LinkModel",
    "ModuleCodeModel",
    "ModuleDependencyModel",
    "ModuleModel",
    "PlanModel",
    "PlanSectionModel",
    "ProjectModel",
    "RevisionModel",
    "SectionModel",
    "StoryAcceptanceModel",
    "StoryLinkModel",
    "StoryTagModel",
    "TagModel",
    "TaskModel",
    "TaskTagModel",
    "TraceCallModel",
    "UserStoryModel",
    "_utcnow",
]
