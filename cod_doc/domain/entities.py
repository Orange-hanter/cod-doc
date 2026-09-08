"""Domain entities — pure dataclasses, no SQLAlchemy or other infra imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import date, datetime


class DocumentType(StrEnum):
    """Catalogue of document types cod-doc can store.

    Two groups, one enum:

    - **Native (cod-doc's own)** — the shapes the generator, the plan exporter
      and the ADR tooling produce themselves.
    - **Corpus types (ADO-015 / RFC 22 §4)** — shapes that already exist in
      real repositories cod-doc imports: this repo's own ``capability`` and
      ``audit-report`` documents, plus the pilots' ``design`` / ``journal`` /
      ``plan`` / ``analysis`` / ``research`` / ``audit``. Before ADO-015 an
      import coerced every one of them into ``module-spec`` in silence.

    ``document.type`` is ``VARCHAR(32)`` without a CHECK constraint or a
    database ENUM, so adding a value here needs no schema migration — only the
    data migration ``0026_document_type_recoercion``, which repairs rows that
    were coerced before the value existed.
    """

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
    # ADO-015: types found in the corpus, previously coerced to module-spec.
    DESIGN = "design"
    AUDIT = "audit"
    AUDIT_REPORT = "audit-report"
    JOURNAL = "journal"
    PLAN = "plan"
    ANALYSIS = "analysis"
    RESEARCH = "research"
    CAPABILITY = "capability"


class DocumentStatus(StrEnum):
    DRAFT = "draft"
    REVIEW = "review"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class Sensitivity(StrEnum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


class LinkKind(StrEnum):
    CANONICAL = "canonical"
    WIKI = "wiki"
    MARKDOWN = "markdown"
    URL = "url"
    TASK = "task"
    STORY = "story"
    SECTION = "section"
    CODE = "code"
    ADR = "adr"


class TaskStatus(StrEnum):
    """7-state taxonomy from proposal 08.

    Legacy aliases (kept for backward compat with existing tasks/tests):
    - PENDING ≡ TODO  (semantically — "ready to work, not picked up")
    - IN_PROGRESS uses hyphen ("in-progress") for legacy; new code may
      use IN_PROGRESS_NEW ("in_progress") interchangeably via the
      state-machine normaliser.
    """

    # New canonical taxonomy (proposal 08).
    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS_NEW = "in_progress"
    IN_REVIEW = "in_review"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

    # Legacy values — still valid, mapped to new semantics.
    PENDING = "pending"
    IN_PROGRESS = "in-progress"
    DONE = "done"


class TaskType(StrEnum):
    FEATURE = "feature"
    TEST = "test"
    BUG = "bug"
    REFACTOR = "refactor"
    MIGRATION = "migration"
    DOCS = "docs"
    CHORE = "chore"


class Priority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DependencyKind(StrEnum):
    BLOCKS = "blocks"
    RELATES = "relates"
    DUPLICATES = "duplicates"


class AffectedFileKind(StrEnum):
    SOURCE = "source"
    TEST = "test"
    MIGRATION = "migration"
    CONFIG = "config"


class UserStoryStatus(StrEnum):
    DRAFT = "draft"
    ACCEPTED = "accepted"
    DELIVERED = "delivered"
    DEFERRED = "deferred"


class StoryLinkKind(StrEnum):
    TASK = "task"
    DOCUMENT = "document"
    MODULE = "module"


class StoryRelation(StrEnum):
    IMPLEMENTED_BY = "implemented_by"
    SPECIFIED_IN = "specified_in"
    OWNED_BY = "owned_by"


class ADRStatus(StrEnum):
    """ADR lifecycle (ADR-001 / cycle-5).

    Canonical: proposed → accepted → (optionally) superseded | deprecated.
    Rejected is for "considered but explicitly declined" decisions.
    """

    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"


class ADRTaskRelation(StrEnum):
    """How a task relates to an ADR (ADR-001 / cycle-5)."""

    IMPLEMENTS = "implements"
    INVALIDATES = "invalidates"
    DISCOVERS = "discovers"
    RELATES = "relates"


class ModuleStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class ModuleCodeKind(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    TESTS = "tests"
    MIGRATIONS = "migrations"
    ADMIN_PANEL = "admin_panel"


class EntityKind(StrEnum):
    DOCUMENT = "document"
    SECTION = "section"
    TASK = "task"
    TASK_DOC = "task_doc"
    PLAN = "plan"
    STORY = "story"
    # ADO-143: у секции своя нумерация row_id, поэтому свой kind. Писать её
    # ревизии под STORY нельзя: пара (kind, entity_id) — единственный адрес
    # ревизии, и секция row_id=1 села бы в историю истории row_id=1.
    STORY_SECTION = "story_section"
    LINK = "link"
    MODULE = "module"
    ADR = "adr"


class ActorKind(StrEnum):
    """ADR-012: канонический словарь ``activity_event.actor_kind``.

    Значения — ровно те, что пишет код. До ADR-012 словарь был объявлен
    как ``orchestrator | human | routine | system`` (миграция 0012),
    а писались ещё ``agent``, ``cli`` и ``api``.
    """

    HUMAN = "human"
    AGENT = "agent"
    ORCHESTRATOR = "orchestrator"
    ROUTINE = "routine"
    SYSTEM = "system"
    #: Поверхностные акторы: их проставляют явно (``cmd_ingest``, ``/api/v1``),
    #: из строки-автора они не выводятся — см. :func:`actor_kind_for_author`.
    CLI = "cli"
    API = "api"


def actor_kind_for_author(author: str | None) -> ActorKind:
    """Вывести :class:`ActorKind` из строки-автора мутации.

    **Единственная** точка вывода на все четыре поверхности (ADR-012).
    До неё эвристика ``author.startswith("agent")`` была продублирована
    в одиннадцати местах в трёх несовместимых вариантах, из-за чего один
    и тот же оркестраторный прогон попадал в журнал то как ``orchestrator``,
    то как ``human``.

    Канонический формат ``author`` — ``<kind>:<id>`` (``human:dakh``,
    ``agent:claude-opus-5``, ``routine:doc_drift_daily``). Оркестраторный
    прогон исторически пишется через дефис — ``orchestrator-run-<name>``;
    резолвер понимает оба написания.

    Правила по убыванию приоритета:

    - ``orchestrator:*`` / ``orchestrator-run-*``  → ``orchestrator``
    - ``agent:*`` / ``agent-*`` / ``agent``        → ``agent``
    - ``routine:*`` / ``routine-*``                → ``routine``
    - ``human:*``                                  → ``human``
    - ``system``, ``mcp``, ``mcp:*``               → ``system``
    - пусто или legacy-форма без префикса          → ``human``

    ``ActorKind.CLI`` / ``ActorKind.API`` из строки не выводятся: это
    поверхности, а не роли автора, и проставляются явно на своих
    call-site'ах. Legacy-хвост (``cli``, ``claude-opus-5``,
    ``roadmap-sync``, ``kimi-m4``) осознанно уезжает в ``human``: за
    такими строками стоит человек, запустивший инструмент руками.
    """
    if not author:
        return ActorKind.HUMAN

    head = author.split(":", 1)[0].strip().lower()
    # ``orchestrator-run-…`` / ``agent-steward`` — префикс через дефис.
    root = head.split("-", 1)[0]

    if root == "orchestrator":
        return ActorKind.ORCHESTRATOR
    if root == "agent":
        return ActorKind.AGENT
    if root == "routine":
        return ActorKind.ROUTINE
    if head in {"system", "mcp"}:
        return ActorKind.SYSTEM
    return ActorKind.HUMAN


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
    # ADO-010/ADO-036: verbatim YAML frontmatter исходного файла (без ---),
    # был ли в теле `# H1`, и sha256 принятого файла — фиделность проекций.
    # NULL для DB-authored документов.
    frontmatter_raw: str | None = None
    title_in_body: bool | None = None
    content_sha256_head: str | None = None
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
    to_adr_id: str | None = None
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
    blocked_reason: str | None = None


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
class StorySection:
    """Именованная группа историй проекта — продуктовый модуль.

    ``key`` — стабильный слаг, а не произвольная строка: он подставляется в путь
    роута анализа секции (`/stories/section/{key}/analyze`, один сегмент URL) и
    в ``id``/``hx-target`` htmx-фрагмента, который уходит в ``querySelector``.
    Поэтому допустимы только ``[a-z0-9-]`` — см. ``validate_section_key``.
    """

    project_id: int
    key: str
    title: str
    position: int
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
    section_id: int | None = None


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
    run_id: str | None = None


class AgentRunStatus(StrEnum):
    """PCA-030: lifecycle of a single Orchestrator.run_task invocation."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(slots=True)
class AgentRun:
    """PCA-030: per-orchestrator-heartbeat run record (proposal 04)."""

    run_id: str
    project_id: int
    row_id: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    wake_reason: str | None = None
    triggering_task_id: str | None = None
    triggering_doc_ref: str | None = None
    llm_calls: int = 0
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    status: AgentRunStatus = AgentRunStatus.RUNNING
    summary: str | None = None


@dataclass(slots=True)
class Tag:
    project_id: int
    name: str
    row_id: int | None = None


@dataclass(slots=True)
class TraceCall:
    """One LLM round-trip — chat completion or embedding call (COD-063)."""

    model: str
    kind: str = "chat"
    task_id: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    tool_calls: list[dict[str, Any]] | None = None
    error: str | None = None
    ts: datetime | None = None
    row_id: int | None = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class Adr:
    """Architecture Decision Record (ADR-001)."""

    project_id: int
    adr_id: str  # 'ADR-NNN'
    title: str
    status: ADRStatus = ADRStatus.PROPOSED
    decided_at: date | None = None
    context: str | None = None
    decision: str | None = None
    alternatives: str | None = None
    consequences: str | None = None
    author: str = "human"
    created: datetime | None = None
    last_updated: datetime | None = None
    row_id: int | None = None


@dataclass(slots=True)
class AdrDiagram:
    """Mermaid diagram attached to an ADR."""

    adr_id: int  # row_id of parent ADR
    position: int = 0
    title: str | None = None
    mermaid: str = ""
    row_id: int | None = None


@dataclass(slots=True)
class AdrSupersede:
    """Edge in the ADR supersede DAG: ``superseding`` replaces ``superseded``."""

    superseding_id: int  # row_id
    superseded_id: int  # row_id
    reason: str | None = None
    at: datetime | None = None
    row_id: int | None = None


@dataclass(slots=True)
class AdrTaskLink:
    """Link between an ADR and a task — by task_id string (not row_id)."""

    adr_row_id: int
    task_id: str
    relation: ADRTaskRelation = ADRTaskRelation.IMPLEMENTS
    row_id: int | None = None
