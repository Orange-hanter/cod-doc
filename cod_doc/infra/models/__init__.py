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

from sqlalchemy import Index as _Index
from sqlalchemy import Table as _Table

from .access import ActorModel, ApiTokenModel, ProjectMemberModel
from .activity import ActivityEventModel
from .adrs import (
    ADRDiagramModel,
    ADRModel,
    ADRRelationModel,
    ADRSupersedeModel,
    ADRTaskModel,
    ADRTopicModel,
)
from .approvals import ApprovalDocRevisionLinkModel, ApprovalModel, ApprovalTaskLinkModel
from .base import Base, SyncUidMixin, _utcnow
from .comments import DocCommentModel
from .commits import CommitLinkModel
from .documents import (
    DocNodeModel,
    DocNodeSuggestionModel,
    DocumentModel,
    LinkModel,
    SectionModel,
)
from .findings import ExternalRefModel, FindingModel, FindingSourceRunModel
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
from .questions import OpenQuestionModel, QuestionLinkModel, QuestionOptionModel
from .repo_index import RepoFileModel, RepoImportModel, RepoSymbolModel
from .revisions import AgentRunModel, RevisionModel
from .routines import RoutineModel, RoutineRunModel
from .scenarios import ScenarioLinkModel, ScenarioModel, ScenarioStepModel
from .stories import (
    StoryAcceptanceModel,
    StoryLinkModel,
    StorySectionModel,
    UserStoryModel,
)
from .structure import (
    CodeBoundaryModel,
    CodeContractModel,
    CodeEdgeModel,
    CodeEntityModel,
    CodeStructureSnapshotModel,
    DocCodeClaimModel,
    StructureAssessmentModel,
    StructureCurrentModel,
    StructureFindingModel,
    StructureLinkSuggestionModel,
    StructureWaiverModel,
)
from .sync import (
    ChangesetModel,
    EntityAliasModel,
    ReplicaModel,
    SyncConflictModel,
    SyncLeaseModel,
    SyncOpModel,
    SyncRefModel,
)
from .tags import DocumentTagModel, StoryTagModel, TagModel, TaskTagModel
from .task_docs import TaskDocumentModel
from .traces import TraceCallModel

# ``uq_<t>_uid`` (миграция 0049) — у каждой модели с ``SyncUidMixin``. Цепляется
# здесь, а не в ``__table_args__`` миксина: собственные ``__table_args__``
# моделей перекрыли бы его.
for _mapper in Base.registry.mappers:
    _table = _mapper.local_table
    if issubclass(_mapper.class_, SyncUidMixin) and isinstance(_table, _Table):
        _Index(f"uq_{_table.name}_uid", _table.c.uid, unique=True)

# Страховка ранга (CLO-004): ``before_flush`` ставит rank новой строке без него.
# Регистрируется импортом — модели загружены везде, где есть сессия.
from cod_doc.infra import ordering as _ordering  # noqa: E402, F401

__all__ = [
    "ADRDiagramModel",
    "ADRModel",
    "ADRRelationModel",
    "ADRSupersedeModel",
    "ADRTaskModel",
    "ADRTopicModel",
    "ActivityEventModel",
    "ActorModel",
    "AffectedFileModel",
    "AgentRunModel",
    "ApiTokenModel",
    "ApprovalDocRevisionLinkModel",
    "ApprovalModel",
    "ApprovalTaskLinkModel",
    "Base",
    "ChangesetModel",
    "CodeBoundaryModel",
    "CodeContractModel",
    "CodeEdgeModel",
    "CodeEntityModel",
    "CodeStructureSnapshotModel",
    "CommitLinkModel",
    "DependencyModel",
    "DocCodeClaimModel",
    "DocCommentModel",
    "DocNodeModel",
    "DocNodeSuggestionModel",
    "DocumentModel",
    "DocumentTagModel",
    "EntityAliasModel",
    "ExternalRefModel",
    "FindingModel",
    "FindingSourceRunModel",
    "LinkModel",
    "ModuleCodeModel",
    "ModuleDependencyModel",
    "ModuleModel",
    "OpenQuestionModel",
    "PlanModel",
    "PlanSectionModel",
    "ProjectMemberModel",
    "ProjectModel",
    "QuestionLinkModel",
    "QuestionOptionModel",
    "ReplicaModel",
    "RepoFileModel",
    "RepoImportModel",
    "RepoSymbolModel",
    "RevisionModel",
    "RoutineModel",
    "RoutineRunModel",
    "ScenarioLinkModel",
    "ScenarioModel",
    "ScenarioStepModel",
    "SectionModel",
    "StoryAcceptanceModel",
    "StoryLinkModel",
    "StorySectionModel",
    "StoryTagModel",
    "StructureAssessmentModel",
    "StructureCurrentModel",
    "StructureFindingModel",
    "StructureLinkSuggestionModel",
    "StructureWaiverModel",
    "SyncConflictModel",
    "SyncLeaseModel",
    "SyncOpModel",
    "SyncRefModel",
    "SyncUidMixin",
    "TagModel",
    "TaskDocumentModel",
    "TaskMetricsModel",
    "TaskModel",
    "TaskTagModel",
    "TraceCallModel",
    "UserStoryModel",
    "_utcnow",
]
