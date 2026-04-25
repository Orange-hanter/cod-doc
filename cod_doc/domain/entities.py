"""Domain entities — pure dataclasses, no SQLAlchemy or other infra imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class DocumentType(str, Enum):
    MODULE_SPEC = "module-spec"
    MODULE_SUBDOC = "module-subdoc"
    EXECUTION_PLAN = "execution-plan"
    TASK_SECTION = "task-section"
    EXECUTION_LOG = "execution-log"
    STANDARD = "standard"
    ARCHITECTURE = "architecture"
    VISION = "vision"
    GUIDE = "guide"
    USER_STORY = "user-story"
    DECISION = "decision"
    OPEN_QUESTION = "open-question"
    REDIRECT = "redirect"


class DocumentStatus(str, Enum):
    DRAFT = "draft"
    REVIEW = "review"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class LinkKind(str, Enum):
    CANONICAL = "canonical"
    WIKI = "wiki"
    MARKDOWN = "markdown"
    URL = "url"
    TASK = "task"
    STORY = "story"
    SECTION = "section"


class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    DONE = "done"


class TaskType(str, Enum):
    FEATURE = "feature"
    TEST = "test"
    BUG = "bug"
    REFACTOR = "refactor"
    MIGRATION = "migration"
    DOCS = "docs"
    CHORE = "chore"


class Priority(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DependencyKind(str, Enum):
    BLOCKS = "blocks"
    RELATES = "relates"
    DUPLICATES = "duplicates"


class AffectedFileKind(str, Enum):
    SOURCE = "source"
    TEST = "test"
    MIGRATION = "migration"
    CONFIG = "config"


class UserStoryStatus(str, Enum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    DEFERRED = "deferred"


class StoryLinkKind(str, Enum):
    TASK = "task"
    DOCUMENT = "document"
    MODULE = "module"


class StoryRelation(str, Enum):
    IMPLEMENTED_BY = "implemented_by"
    SPECIFIED_IN = "specified_in"
    OWNED_BY = "owned_by"


class ModuleStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ModuleCodeKind(str, Enum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    TESTS = "tests"
    MIGRATIONS = "migrations"
    ADMIN_PANEL = "admin_panel"


class EntityKind(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    TASK = "task"
    PLAN = "plan"
    STORY = "story"
    LINK = "link"
    MODULE = "module"


class AuditSurface(str, Enum):
    CLI = "cli"
    MCP = "mcp"
    REST = "rest"
    TUI = "tui"
    AGENT = "agent"


@dataclass(slots=True)
class Project:
    slug: str
    title: str
    root_path: str
    row_id: int | None = None
    created: datetime | None = None
    updated: datetime | None = None
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Document:
    project_id: int
    doc_key: str
    path: str
    type: DocumentType
    status: DocumentStatus
    title: str
    row_id: int | None = None
    source_of_truth: bool = True
    sensitivity: Sensitivity = Sensitivity.INTERNAL
    owner: str | None = None
    preamble: str = ""
    frontmatter: dict[str, Any] = field(default_factory=dict)
    projection_hash: str | None = None
    created: datetime | None = None
    last_updated: datetime | None = None
    last_reviewed: datetime | None = None


@dataclass(slots=True)
class Section:
    document_id: int
    anchor: str
    heading: str
    level: int
    position: int
    body: str
    content_hash: str
    row_id: int | None = None


@dataclass(slots=True)
class Link:
    project_id: int
    from_section_id: int
    raw: str
    kind: LinkKind
    row_id: int | None = None
    to_doc_key: str | None = None
    to_task_id: str | None = None
    to_story_id: str | None = None
    resolved: bool = False
    last_checked: datetime | None = None
    broken_reason: str | None = None


@dataclass(slots=True)
class Plan:
    project_id: int
    scope: str
    row_id: int | None = None
    principle: str | None = None
    module_id: str | None = None
    parent_doc_id: int | None = None
    completed_log_id: int | None = None
    created: datetime | None = None
    last_updated: datetime | None = None


@dataclass(slots=True)
class PlanSection:
    plan_id: int
    letter: str
    title: str
    slug: str
    position: int
    row_id: int | None = None
    doc_id: int | None = None


@dataclass(slots=True)
class Task:
    project_id: int
    task_id: str
    plan_id: int
    section_id: int
    title: str
    status: TaskStatus
    type: TaskType
    priority: Priority
    row_id: int | None = None
    description: str | None = None
    acceptance: str | None = None
    created: datetime | None = None
    last_updated: datetime | None = None
    completed_at: datetime | None = None
    completed_commit: str | None = None


@dataclass(slots=True)
class Dependency:
    from_task_id: int
    to_task_id: int
    kind: DependencyKind = DependencyKind.BLOCKS
    note: str | None = None
    row_id: int | None = None


@dataclass(slots=True)
class AffectedFile:
    task_id: int
    path: str
    kind: AffectedFileKind = AffectedFileKind.SOURCE
    row_id: int | None = None


@dataclass(slots=True)
class UserStory:
    project_id: int
    story_id: str
    persona: str
    narrative: str
    status: UserStoryStatus
    priority: Priority
    row_id: int | None = None
    created: datetime | None = None
    last_updated: datetime | None = None


@dataclass(slots=True)
class StoryAcceptance:
    story_id: int
    position: int
    criterion: str
    met: bool = False
    row_id: int | None = None


@dataclass(slots=True)
class StoryLink:
    story_id: int
    to_kind: StoryLinkKind
    to_ref: str
    relation: StoryRelation
    row_id: int | None = None


@dataclass(slots=True)
class Module:
    project_id: int
    module_id: str
    name: str
    status: ModuleStatus
    row_id: int | None = None
    spec_doc_id: int | None = None
    plan_id: int | None = None


@dataclass(slots=True)
class ModuleDependency:
    from_module: int
    to_module: int
    reason: str | None = None
    row_id: int | None = None


@dataclass(slots=True)
class ModuleCode:
    module_id: int
    kind: ModuleCodeKind
    path: str
    row_id: int | None = None


@dataclass(slots=True)
class Revision:
    revision_id: str
    project_id: int
    entity_kind: EntityKind
    entity_id: int
    author: str
    diff: str
    row_id: int | None = None
    parent_revision_id: str | None = None
    at: datetime | None = None
    reason: str | None = None
    commit_sha: str | None = None


@dataclass(slots=True)
class AuditLog:
    project_id: int
    actor: str
    surface: AuditSurface
    action: str
    payload: dict[str, Any]
    result: str
    row_id: int | None = None
    at: datetime | None = None


@dataclass(slots=True)
class Tag:
    project_id: int
    name: str
    row_id: int | None = None
