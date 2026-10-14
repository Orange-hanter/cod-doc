"""Реестр классов синхронизации (RFC 29 §3.2, §3.3, контракт §10.2; ADR-017).

Каждая таблица моделей относится ровно к одному классу:

- ``authored`` — уезжает операциями над полями;
- ``journal`` — append-only (``revision``, ``activity_event``), объединение по
  глобальному ID;
- ``derived`` — не уезжает, пересобирается после pull;
- ``local`` — не уезжает никогда.

Гейт полноты — ``tests/infra/test_sync_registry_complete.py``: таблица моделей
без записи и целочисленная ``*_id``-колонка authored-таблицы без настоящего FK и
без записи в ``logical_fks`` роняют его. Модуль — stdlib + ``cod_doc.domain``:
захват (CLO-006) и движок слияния читают его из ``infra/`` и ``services/``.

Решения там, где контракт RFC неоднозначен или расходится со схемой (CLO-001):

1. **Таблиц 65, а не 64.** ``link_suggestion`` объявлена в
   ``infra/models/link_suggestions.py``, но ``infra/models/__init__.py`` её не
   импортирует: она попадает в ``Base.metadata`` только после ленивого импорта
   из ``link_service/semantic.py``. RFC считал 64 по ``__init__``. Гейт
   импортирует каждый модуль пакета ``models``, иначе таблица выпадала бы из
   сверки именно так, как выпала из подсчёта.
2. **``row_is_local`` значит «строка не уезжает»**, а не «класс local». У
   ``finding`` строки ``source = "routine"`` по RFC — derived; ``row_class``
   отдаёт для них ``DERIVED``, для остальных построчных исключений — ``LOCAL``.
3. **Restricted и потомки документа.** Строка секции не несёт ``sensitivity``.
   Для таблиц из ``RESTRICTED_OWNER_FK`` (``section``, ``doc_comment``,
   ``document_tag``) вызывающий кладёт в строку ``sensitivity`` документа-
   владельца. RFC называет только секции; комментарий цитирует текст документа,
   а тег и комментарий без документа на другой реплике висели бы на
   несуществующем uid — поэтому в списке все три таблицы, которые документ
   держит по ``ON DELETE CASCADE`` и которые authored.
4. **``link_suggestion``.** Класс ``DERIVED``; ``authored_columns`` — ``state``
   и колонки ключа решения (``from_section_id``, ``to_doc_key``,
   ``to_section_id``): без них на другой реплике решение не к чему приложить.
   Строка с ``state = pending`` — ``row_is_local``: не решение (Q-017). Ключ
   решения — ``deterministic_uid``.
5. **Полиморфные ссылки.** ``external_ref.entity_row_id`` и
   ``revision.entity_id`` ссылаются на ``row_id`` таблицы, выбранной колонкой
   ``entity_kind``; ``logical_fks: {колонка: таблица}`` такого не выражает. Они
   объявлены в ``POLYMORPHIC_FKS`` (колонка → колонка-дискриминатор),
   ``ENTITY_KIND_TABLES`` переводит значения ``EntityKind`` в таблицы.
   Аналогично ``to_ref`` у ``story_link``/``scenario_link``/``open_question_link``:
   в ``human_id_refs`` значение — имя колонки-дискриминатора (``to_kind``), а не
   таблица; ``human_ref_target`` различает эти случаи. ``criterion`` у
   ``scenario_link`` — ``<story_id>#<position>``: переписывать надо часть до ``#``.
6. **``approval_task_link.task_ref``** — строковая ссылка на ``task.task_id``,
   в списке RFC §3.3 её нет; добавлена в ``human_id_refs``.
7. **Натуральный ключ ``finding (project_id, source, fingerprint)``** — в
   таблице RFC §3.3 его нет, но это ``UNIQUE`` на authored-таблице: одна внешняя
   находка, принятая двумя репликами, иначе упала бы на вставке. Объявлен
   ``NAME``.
8. **Колонки local сверх списка RFC:** ``project.root_path`` (путь рабочего
   дерева на этой машине), ``finding.run_id`` и ``approval.run_id`` (ссылка на
   local ``agent_run``, ADR-012), ``finding.miss_streak`` (счётчик сверки
   ``reconcile_partition``, которая по RFC остаётся локальной).
9. **``rank_column``** объявлен заранее для 11 мест порядка; колонок ``rank`` /
   ``node_rank`` в схеме ещё нет (миграция CLO-003), поэтому гейт сверяет
   существование всех объявленных колонок, кроме ``rank_column``.
10. **``deterministic_uid``** — колонки, из которых uuid5 собирается после
    перевода FK в uid; совпадает с ``UNIQUE`` ребра. У ``story_link`` такого
    ``UNIQUE`` в схеме нет — детерминированный uid сделает дубль невозможным.
    ``approval_task_link``/``approval_doc_revision_link`` в списке рёбер RFC
    нет — получают случайный uid.
11. **``text_columns``** — ровно список RFC §3.7.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType

from cod_doc.domain.entities import EntityKind, Sensitivity


class SyncClass(StrEnum):
    AUTHORED = "authored"
    JOURNAL = "journal"
    DERIVED = "derived"
    LOCAL = "local"


class KeyKind(StrEnum):
    NAME = "name"  # склейка (Q-007)
    LABEL = "label"  # перебуквление (plan_section.letter)
    LEASED = "leased"  # арендованный номер


@dataclass(frozen=True, slots=True)
class NaturalKey:
    columns: tuple[str, ...]  # ('project_id', 'doc_key')
    kind: KeyKind


RowPredicate = Callable[[Mapping[str, object]], bool]


@dataclass(frozen=True, slots=True)
class TableSpec:
    table: str
    cls: SyncClass
    local_columns: frozenset[str] = frozenset()  # projection_hash, position, …
    # для DERIVED с authored-полем (link_suggestion.state)
    authored_columns: frozenset[str] = frozenset()
    row_is_local: RowPredicate | None = None  # restricted
    natural_keys: tuple[NaturalKey, ...] = ()
    logical_fks: Mapping[str, str] = field(default_factory=dict)  # {'topic_id': 'adr_topic'}
    human_id_refs: Mapping[str, str] = field(default_factory=dict)  # {'task_id': 'task'}
    rank_column: str | None = None  # 'rank' / 'node_rank'
    deterministic_uid: tuple[str, ...] | None = None  # концы ребра для uuid5
    text_columns: frozenset[str] = frozenset()  # 3-way merge вместо LWW


# --- построчные предикаты ---------------------------------------------------

_SENSITIVITY = "sensitivity"
_ROUTINE_SOURCE = "routine"
_PENDING_STATE = "pending"
_LOCAL_EVENT_PREFIX = "link."
_LOCAL_EVENT_KINDS = frozenset({"routine.fired"})


def _restricted(row: Mapping[str, object]) -> bool:
    """Документ ``restricted`` и его потомки (решение 3 в докстринге модуля)."""
    return row.get(_SENSITIVITY) == Sensitivity.RESTRICTED


def _routine_finding(row: Mapping[str, object]) -> bool:
    return row.get("source") == _ROUTINE_SOURCE


def _pending_suggestion(row: Mapping[str, object]) -> bool:
    return row.get("state") == _PENDING_STATE


def _local_event(row: Mapping[str, object]) -> bool:
    kind = row.get("kind")
    if not isinstance(kind, str):
        return False
    return kind.startswith(_LOCAL_EVENT_PREFIX) or kind in _LOCAL_EVENT_KINDS


# --- сокращения для таблицы --------------------------------------------------

_A = SyncClass.AUTHORED
_J = SyncClass.JOURNAL
_D = SyncClass.DERIVED
_L = SyncClass.LOCAL

_POSITION = frozenset({"position"})
_RANK = "rank"


def _name(*columns: str) -> NaturalKey:
    return NaturalKey(columns, KeyKind.NAME)


def _leased(column: str) -> NaturalKey:
    return NaturalKey(("project_id", column), KeyKind.LEASED)


_TO_REF = {"to_ref": "to_kind"}

_SPECS: tuple[TableSpec, ...] = (
    # --- authored: проект, документы, дерево ---------------------------------
    TableSpec("project", _A, local_columns=frozenset({"root_path"})),
    TableSpec(
        "document",
        _A,
        local_columns=frozenset({"projection_hash", "content_sha256_head", "node_position"}),
        row_is_local=_restricted,
        natural_keys=(_name("project_id", "doc_key"),),
        rank_column="node_rank",
    ),
    TableSpec(
        "section",
        _A,
        local_columns=_POSITION,
        row_is_local=_restricted,
        natural_keys=(_name("document_id", "anchor"),),
        rank_column=_RANK,
        text_columns=frozenset({"body"}),
    ),
    TableSpec("doc_comment", _A, row_is_local=_restricted),
    TableSpec(
        "doc_node",
        _A,
        local_columns=_POSITION,
        natural_keys=(_name("project_id", "node_key"),),
        rank_column=_RANK,
    ),
    # --- authored: планы и задачи -------------------------------------------
    TableSpec("plan", _A, natural_keys=(_name("project_id", "scope"),)),
    TableSpec(
        "plan_section",
        _A,
        local_columns=_POSITION,
        natural_keys=(NaturalKey(("plan_id", "letter"), KeyKind.LABEL),),
        rank_column=_RANK,
    ),
    TableSpec(
        "task",
        _A,
        natural_keys=(_leased("task_id"),),
        text_columns=frozenset({"description", "acceptance"}),
    ),
    TableSpec("dependency", _A, deterministic_uid=("from_task_id", "to_task_id", "kind")),
    TableSpec("affected_file", _A, deterministic_uid=("task_id", "path")),
    TableSpec(
        "task_document",
        _A,
        natural_keys=(_name("task_id", "key"),),
        text_columns=frozenset({"body"}),
    ),
    TableSpec(
        "commit_link",
        _A,
        human_id_refs={"task_id": "task"},
        deterministic_uid=("project_id", "task_id", "sha"),
    ),
    # --- authored: ADR ------------------------------------------------------
    TableSpec(
        "adr",
        _A,
        natural_keys=(_leased("adr_id"),),
        logical_fks={"topic_id": "adr_topic"},
        text_columns=frozenset({"decision"}),
    ),
    TableSpec(
        "adr_topic",
        _A,
        local_columns=_POSITION,
        natural_keys=(_name("project_id", "name"),),
        rank_column=_RANK,
    ),
    TableSpec("adr_diagram", _A, local_columns=_POSITION, rank_column=_RANK),
    TableSpec("adr_supersedes", _A, deterministic_uid=("superseding_id", "superseded_id")),
    TableSpec("adr_relation", _A, deterministic_uid=("from_id", "to_id", "kind")),
    TableSpec(
        "adr_task",
        _A,
        human_id_refs={"task_id": "task"},
        deterministic_uid=("adr_row_id", "task_id", "relation"),
    ),
    # --- authored: истории --------------------------------------------------
    TableSpec(
        "user_story",
        _A,
        natural_keys=(_leased("story_id"),),
        text_columns=frozenset({"narrative"}),
    ),
    TableSpec("story_acceptance", _A, local_columns=_POSITION, rank_column=_RANK),
    TableSpec(
        "story_link",
        _A,
        human_id_refs=_TO_REF,
        deterministic_uid=("story_id", "to_kind", "to_ref", "relation"),
    ),
    TableSpec(
        "story_section",
        _A,
        local_columns=_POSITION,
        natural_keys=(_name("project_id", "key"),),
        rank_column=_RANK,
    ),
    # --- authored: сценарии -------------------------------------------------
    TableSpec(
        "scenario",
        _A,
        local_columns=_POSITION,
        natural_keys=(_leased("scenario_id"),),
        rank_column=_RANK,
    ),
    TableSpec("scenario_step", _A, local_columns=_POSITION, rank_column=_RANK),
    TableSpec(
        "scenario_link",
        _A,
        human_id_refs=_TO_REF,
        deterministic_uid=("scenario_row_id", "to_kind", "to_ref", "relation"),
    ),
    # --- authored: модули ---------------------------------------------------
    TableSpec("module", _A, natural_keys=(_name("project_id", "module_id"),)),
    TableSpec("module_dependency", _A, deterministic_uid=("from_module", "to_module")),
    TableSpec("module_code", _A),
    # --- authored: теги (составной PK, uid вычисляется при захвате) ---------
    TableSpec("tag", _A, natural_keys=(_name("project_id", "name"),)),
    TableSpec(
        "document_tag",
        _A,
        row_is_local=_restricted,
        deterministic_uid=("document_id", "tag_id"),
    ),
    TableSpec("task_tag", _A, deterministic_uid=("task_id", "tag_id")),
    TableSpec("story_tag", _A, deterministic_uid=("story_id", "tag_id")),
    # --- authored: внешние ссылки, согласования, рутины, находки ------------
    TableSpec(
        "external_ref",
        _A,
        natural_keys=(_name("project_id", "system", "external_id"),),
    ),
    TableSpec("approval", _A, local_columns=frozenset({"run_id"})),
    TableSpec("approval_task_link", _A, human_id_refs={"task_ref": "task"}),
    TableSpec("approval_doc_revision_link", _A),
    TableSpec("routine", _A, natural_keys=(_name("project_id", "name"),)),
    TableSpec(
        "structure_waiver",
        _A,
        natural_keys=(_name("project_id", "scope", "finding_fingerprint"),),
    ),
    TableSpec(
        "finding",
        _A,
        local_columns=frozenset({"run_id", "miss_streak"}),
        row_is_local=_routine_finding,
        natural_keys=(_name("project_id", "source", "fingerprint"),),
        human_id_refs={"promoted_task_id": "task"},
    ),
    # --- authored: открытые вопросы -----------------------------------------
    TableSpec(
        "open_question",
        _A,
        natural_keys=(_leased("question_id"),),
        human_id_refs={"resolved_by_adr": "adr"},
    ),
    TableSpec("open_question_option", _A, local_columns=_POSITION, rank_column=_RANK),
    TableSpec(
        "open_question_link",
        _A,
        local_columns=frozenset({"resolved", "broken_reason", "last_checked"}),
        human_id_refs=_TO_REF,
        deterministic_uid=("question_row_id", "to_kind", "to_ref", "relation"),
    ),
    # --- journal ------------------------------------------------------------
    TableSpec("revision", _J),
    TableSpec("activity_event", _J, row_is_local=_local_event),
    # --- derived ------------------------------------------------------------
    TableSpec("link", _D),
    TableSpec(
        "link_suggestion",
        _D,
        authored_columns=frozenset({"state", "from_section_id", "to_doc_key", "to_section_id"}),
        row_is_local=_pending_suggestion,
        logical_fks={"from_section_id": "section", "to_section_id": "section"},
        deterministic_uid=("from_section_id", "to_doc_key", "to_section_id"),
    ),
    TableSpec("doc_node_suggestion", _D),
    TableSpec("code_structure_snapshot", _D),
    TableSpec("code_entity", _D),
    TableSpec("code_edge", _D),
    TableSpec("code_contract", _D),
    TableSpec("code_boundary", _D),
    TableSpec("structure_assessment", _D),
    TableSpec("structure_current", _D),
    TableSpec(
        "structure_finding",
        _D,
        logical_fks={
            "first_seen_snapshot_id": "code_structure_snapshot",
            "last_seen_snapshot_id": "code_structure_snapshot",
            "resolved_by_snapshot_id": "code_structure_snapshot",
        },
        human_id_refs={"promoted_task_id": "task"},
    ),
    TableSpec("structure_link_suggestion", _D),
    TableSpec("doc_code_claim", _D),
    TableSpec("repo_file", _D),
    TableSpec("repo_symbol", _D),
    TableSpec("repo_import", _D),
    # --- local --------------------------------------------------------------
    TableSpec("routine_run", _L),
    TableSpec("agent_run", _L),
    TableSpec("trace_call", _L),
    TableSpec("task_metrics", _L),
    TableSpec("finding_source_run", _L),
)


def _index(specs: tuple[TableSpec, ...]) -> Mapping[str, TableSpec]:
    by_table: dict[str, TableSpec] = {}
    for spec in specs:
        if spec.table in by_table:
            msg = f"sync_registry: таблица {spec.table!r} объявлена дважды"
            raise ValueError(msg)
        by_table[spec.table] = spec
    return MappingProxyType(by_table)


REGISTRY: Mapping[str, TableSpec] = _index(_SPECS)

#: Куда уходит строка, которую ``row_is_local`` не выпускает (решение 2).
_ROW_CLASS_WHEN_HELD: Mapping[str, SyncClass] = MappingProxyType({"finding": SyncClass.DERIVED})

#: Таблицы-потомки документа: по какой FK-колонке брать ``sensitivity``
#: владельца для ``row_is_local`` (решение 3).
RESTRICTED_OWNER_FK: Mapping[str, str] = MappingProxyType(
    {"section": "document_id", "doc_comment": "document_id", "document_tag": "document_id"}
)

#: Полиморфные ссылки на ``row_id``: колонка → колонка-дискриминатор со
#: значением ``EntityKind`` (решение 5).
POLYMORPHIC_FKS: Mapping[tuple[str, str], str] = MappingProxyType(
    {
        ("external_ref", "entity_row_id"): "entity_kind",
        ("revision", "entity_id"): "entity_kind",
    }
)

#: ``EntityKind`` (адрес ревизии и внешней ссылки) → таблица.
ENTITY_KIND_TABLES: Mapping[EntityKind, str] = MappingProxyType(
    {
        EntityKind.DOCUMENT: "document",
        EntityKind.SECTION: "section",
        EntityKind.TASK: "task",
        EntityKind.TASK_DOC: "task_document",
        EntityKind.PLAN: "plan",
        EntityKind.STORY: "user_story",
        EntityKind.STORY_SECTION: "story_section",
        EntityKind.DOC_NODE: "doc_node",
        EntityKind.PLAN_SECTION: "plan_section",
        EntityKind.LINK: "link",
        EntityKind.MODULE: "module",
        EntityKind.ADR: "adr",
        EntityKind.ADR_TOPIC: "adr_topic",
        EntityKind.SCENARIO: "scenario",
        EntityKind.QUESTION: "open_question",
    }
)

#: Значение ``to_kind`` → таблица с арендованным номером, на который указывает
#: ``to_ref``. Виды, которых здесь нет (document, section, code, url, …),
#: ссылаются не на арендованный номер, и закрепление их не переписывает.
HUMAN_ID_KINDS: Mapping[str, str] = MappingProxyType(
    {
        "task": "task",
        "story": "user_story",
        "criterion": "user_story",  # '<story_id>#<position>'
        "adr": "adr",
        "scenario": "scenario",
    }
)


def spec_for(table: str) -> TableSpec:
    """Запись реестра; ``KeyError`` — незаклассифицированная таблица, баг."""
    return REGISTRY[table]


def authored_tables() -> list[TableSpec]:
    """Authored-таблицы в порядке имён."""
    return sorted(
        (spec for spec in REGISTRY.values() if spec.cls is SyncClass.AUTHORED),
        key=lambda spec: spec.table,
    )


def row_class(table: str, row: Mapping[str, object]) -> SyncClass:
    """Класс конкретной строки с учётом построчных исключений (решение 2)."""
    spec = spec_for(table)
    if spec.row_is_local is not None and spec.row_is_local(row):
        return _ROW_CLASS_WHEN_HELD.get(table, SyncClass.LOCAL)
    return spec.cls


def human_ref_target(table: str, column: str, row: Mapping[str, object]) -> str | None:
    """Таблица арендованного номера, на который ссылается ``row[column]``.

    ``None`` — колонка не человеческая ссылка или (для полиморфных ``to_ref``)
    вид цели не арендуемый.
    """
    target = spec_for(table).human_id_refs.get(column)
    if target is None or target in REGISTRY:
        return target
    kind = row.get(target)
    return HUMAN_ID_KINDS.get(kind) if isinstance(kind, str) else None
