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

from .activity import ActivityEventModel
from .adrs import ADRDiagramModel, ADRModel, ADRSupersedeModel, ADRTaskModel
from .approvals import ApprovalDocRevisionLinkModel, ApprovalModel, ApprovalTaskLinkModel
from .base import Base, _utcnow
from .comments import DocCommentModel
from .commits import CommitLinkModel
from .documents import DocumentModel, LinkModel, SectionModel
from .metrics import TaskMetricsModel
from .modules import ModuleCodeModel, ModuleDependencyModel, ModuleModel
from .plans import (
    AffectedFileModel,
    DependencyModel,
    PlanModel,
    PlanSectionModel,
    TaskModel,
)
from .project import ProjectModel
from .revisions import AgentRunModel, AuditLogModel, RevisionModel
from .routines import RoutineModel, RoutineRunModel
from .stories import StoryAcceptanceModel, StoryLinkModel, UserStoryModel
from .tags import DocumentTagModel, StoryTagModel, TagModel, TaskTagModel
from .task_docs import TaskDocumentModel
from .traces import TraceCallModel

__all__ = [
    "ADRDiagramModel",
    "ADRModel",
    "ADRSupersedeModel",
    "ADRTaskModel",
    "ActivityEventModel",
    "AffectedFileModel",
    "AgentRunModel",
    "ApprovalDocRevisionLinkModel",
    "ApprovalModel",
    "ApprovalTaskLinkModel",
    "CommitLinkModel",
    "AuditLogModel",
    "Base",
    "DependencyModel",
    "DocCommentModel",
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
    "RoutineModel",
    "RoutineRunModel",
    "SectionModel",
    "StoryAcceptanceModel",
    "StoryLinkModel",
    "StoryTagModel",
    "TagModel",
    "TaskDocumentModel",
    "TaskMetricsModel",
    "TaskModel",
    "TaskTagModel",
    "TraceCallModel",
    "UserStoryModel",
    "_utcnow",
]
