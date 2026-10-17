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
3. **Restricted и потомки документа.** Строка секции не несёт метки
   документа. Каждая колонка, ссылающаяся на документ или секцию, объявлена в
   ``DOC_REFS``; вызывающий кладёт метку документа под ключ
   ``sensitivity_key(колонка)`` (``"document_id.sensitivity"``), без неё ссылка
   не разрешена (решения 12, 16). RFC называет только секции; комментарий
   цитирует текст документа, а тег и комментарий без документа на другой
   реплике висели бы на несуществующем uid.
4. **``link_suggestion``.** Класс ``DERIVED``; ``authored_columns`` — ``state``
   и колонки ключа решения (``from_section_id``, ``to_doc_key``,
   ``to_section_id``): без них на другой реплике решение не к чему приложить.
   Строка с ``state = pending`` — ``row_is_local``: не решение (Q-017). Ключ
   решения — ``deterministic_uid``. Своя нерешённая подсказка на реплике —
   та же строка, что и пришедшее решение (один ключ): применение пишет
   решение поверх неё, гейт сверяется со строкой уже с решением
   (``decision_columns``, CLO-025).
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
9. **``rank_column``** у 11 мест порядка — колонки ``rank`` / ``node_rank`` из
   миграции 0050 (CLO-003); гейт сверяет их существование наравне с прочими
   объявленными колонками и запрещает ``UNIQUE`` на позиции этих таблиц.
10. **``deterministic_uid``** — колонки, из которых uuid5 собирается после
    перевода FK в uid; совпадает с ``UNIQUE`` ребра. У ``story_link`` такого
    ``UNIQUE`` в схеме нет — детерминированный uid сделает дубль невозможным.
    ``approval_task_link``/``approval_doc_revision_link`` в списке рёбер RFC
    нет — получают случайный uid.
11. **``text_columns``** — ровно список RFC §3.7.
12. **Fail-closed по строкам** (security review CLO-001). Документ уезжает
    только с меткой ``public``/``internal``/``confidential``; нет метки,
    ``NULL``, незнакомое значение — local. Журнал по RFC уезжает целиком, но
    ``revision`` документа/секции несёт diff его текста, а ``activity_event`` со
    ``scope_kind`` ``doc``/``document``/``section``/``comment`` — сведения о нём:
    они в ``DOC_REFS`` наравне с остальными ссылками. Незнакомый
    ``entity_kind``, пустой ``kind``, незнакомая или пустая ``scope_kind`` —
    local; ``finding`` без канонического ``source`` — derived; подсказка
    уезжает только с ``state`` из ``accepted``/``rejected``.
13. **Fail-closed по колонкам.** ``SHIPPED_COLUMNS`` перечисляет уезжающие
    колонки authored- и journal-таблиц явно; ``column_class`` отдаёт ``LOCAL``
    для колонки вне перечня, ``row_id`` и ``local_columns``, ``KeyError`` для
    незнакомой таблицы. Гейт требует, чтобы каждая колонка схемы была либо в
    перечне, либо в ``local_columns``: новая колонка получает класс явным
    решением, а до него не уезжает. Сверх списка RFC local стали:
    ``project.config_json`` (конфиг машины), ``approval.payload_json`` (тело
    предлагаемой правки документа — мог бы унести текст restricted-документа),
    ``finding.payload`` (сырые данные производителя), ``run_id`` у ``revision``
    и ``activity_event`` (ссылка на local ``agent_run``).
14. **Машинно-приватные данные** (security review CLO-001, второй раунд).
    Строка с абсолютным ``path`` (POSIX, ``~``, диск Windows, UNC) не уезжает:
    ``affected_file``, ``module_code``, ``document`` (путь обязателен — нет
    ключа, тоже local) и ``finding`` (путь необязателен — ``NULL`` уезжает).
    На живой БД cod-doc таких строк 4 в ``affected_file``. ``activity_event.payload``
    — local: свободный JSON с абсолютными путями (``master.hashes_updated``,
    ``doc.exported``) и текстами ошибок (``project.repaired``); событие уезжает
    с ``kind``/``scope``/``summary``. ``commit_link.message`` и ``.author`` —
    local: git-автор (имя/почта) и сообщение восстанавливаются из репозитория
    по ``sha``, везти их вторым каналом незачем. Свободный авторский текст
    (тела секций, описания задач, ``revision.diff``) не фильтруется — это
    содержимое проекта, а не метаданные машины; от него защищает только
    ``restricted``.
15. **Разбор значений как у репозитория** (security review CLO-001, третий
    раунд, parser differential). Метка разбирается ``parse_sensitivity`` — тем
    же ``Sensitivity(value)``, что и ``document_repo``, без ``strip``/``lower``:
    ``"Restricted"``, ``" internal"`` — не канон, строка local.
    ``sensitivity_scanner.clearance_meets`` для тех же значений делает
    ``lower()`` и считает неизвестное ``internal`` — гейт синхронизации с ним
    расходится намеренно, в сторону «не уезжает». ``entity_kind`` — через
    ``EntityKind(value)``; ``source`` находки — строчный токен; путь с
    пробелом по краям, NUL или схемой URI — машинный.
16. **Все пути к restricted-документу под одним гейтом** (третий раунд,
    sibling-path parity). ``DOC_REFS`` перечисляет каждую ссылку на документ или
    секцию из уезжающих таблиц — FK, логическую, строковую ``doc_key``,
    полиморфную (``to_ref``/``to_kind``, ``entity_id``/``entity_kind``,
    ``scope_id``/``scope_kind``). Строка-связь (секция, комментарий, тег,
    ссылка вопроса/истории/сценария, внешняя ссылка, ревизия, событие,
    решение по подсказке, связь согласования с ревизией) не уезжает целиком;
    сущность со ссылкой (план, секция плана, модуль, сценарий, вопрос) уезжает
    без ссылки и связанных с ней колонок (``withheld_columns``). Гейт сверяет
    полноту: каждая FK/логическая ссылка на ``document``/``section`` и каждая
    колонка ``*doc_key`` уезжающей таблицы обязаны быть в ``DOC_REFS``.
    Derived (``link``, ``doc_node_suggestion``, FTS) не уезжает вовсе.
    ``approval_doc_revision_link.revision_id`` — метка документа, к которому
    относится ревизия.
17. **Непроверяемая ссылка — ссылка на restricted** (четвёртый раунд,
    регрессия решения 16). Первая версия ``DOC_REFS`` считала пустую или
    отсутствующую ссылку «ссылки нет», и секция без ``document_id``, ревизия
    документа без ``entity_id``, событие ``doc`` без ``scope_id`` уезжали, хотя
    до этого были local. Теперь «ссылки нет» — только ``None`` в ``nullable``
    колонке без дискриминатора (``nullable`` сверяется со схемой), а у
    полиморфной ссылки — только заведомо не-документный вид. Метки —
    снова перечень ``_CLEARED_SENSITIVITY``, а не ``!= restricted``. Захват
    (CLO-006) поэтому обязан передавать строку целиком, а не только
    изменённые поля: без колонки-ссылки строка-связь не уедет.
18. **Колонка ``uid``** (CLO-002, миграция ``0049_sync_identity``). Есть у
    каждой authored-таблицы, кроме тег-таблиц с составным PK
    (``COMPOSITE_PK_TABLES``): RFC §3.3/§10.1 исключает их — uid тега
    вычисляется при захвате из той же формулы, а ``deterministic_uid`` в их
    записях и есть её вход. ``uid`` уезжает (``SHIPPED_COLUMNS``): это
    идентичность строки, без неё операция не адресуема. Рёбра получают
    ``edge_uid`` — uuid5 от JSON ``[таблица, *ключ]``, где FK ключа заменены на
    uid концов; остальные строки — случайный uuid7. Поэтому uid ребра
    детерминирован относительно uid концов, но не между двумя независимо
    мигрированными БД: концы у них получили разные uuid7. Шесть служебных
    таблиц синхронизации (``replica``, ``sync_op``, ``changeset``,
    ``sync_ref``, ``sync_lease``, ``entity_alias``) — ``local``: это состояние
    реплики, а не проекта.
19. **Журнал в changeset'е** (CLO-024, Q-010). ``revision`` уезжает
    операцией ``upsert`` на вставку строки; адрес операции —
    ``revision_id`` (``JOURNAL_IDS``), колонки ``uid`` у журнала нет.
    Применение — объединение по этому id: ``parent_revision_id`` переписывает
    реплика (локальная голова сущности), поэтому в дайджест он не входит.
    Ревизия сущности без колонки ``uid`` (``EntityKind.LINK`` — derived
    ``link``) — local: адресовать её на другой реплике не через что.
    **Ревизия уезжает, только если её сущность уезжает целиком**
    (security review CLO-024): ``diff`` повторяет значения сущности, и
    ревизия сценария, привязанного к restricted-документу, унесла бы
    ``doc_key``/``doc_content_hash``, которые реестр у самого сценария
    придерживает. Захват кладёт вердикт под ``ENTITY_SHIPS_KEY``: строка
    сущности есть, ``row_is_local`` её выпускает и ``withheld_columns`` пуст.
    Нет ключа (сущность удалена, не найдена) — ревизия local (fail-closed).
    Ключи local-колонок сущности захват вычищает из JSON-``diff``.
    ``activity_event`` в ``JOURNAL_IDS`` нет: события ``sync.*`` пишет сам
    обмен (``sync.pushed`` после CAS дал бы новую операцию после каждого
    push), а лента событий — телеметрия реплики, а не данные проекта. Класс
    таблицы остаётся ``journal``: перечень уезжающих колонок готов на случай,
    если события поедут.
"""

from __future__ import annotations

import json
import re
import uuid
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


def _empty_mapping() -> Mapping[str, str]:
    return MappingProxyType({})


@dataclass(frozen=True, slots=True)
class TableSpec:
    table: str
    cls: SyncClass
    local_columns: frozenset[str] = frozenset()  # projection_hash, position, …
    # для DERIVED с authored-полем (link_suggestion.state)
    authored_columns: frozenset[str] = frozenset()
    row_is_local: RowPredicate | None = None  # restricted
    natural_keys: tuple[NaturalKey, ...] = ()
    logical_fks: Mapping[str, str] = field(default_factory=_empty_mapping)  # {'topic_id': …}
    human_id_refs: Mapping[str, str] = field(default_factory=_empty_mapping)  # {'task_id': …}
    rank_column: str | None = None  # 'rank' / 'node_rank'
    deterministic_uid: tuple[str, ...] | None = None  # концы ребра для uuid5
    text_columns: frozenset[str] = frozenset()  # 3-way merge вместо LWW

    def __post_init__(self) -> None:
        # frozen защищает поля, но не содержимое dict: переданные словари
        # копируются в неизменяемое представление.
        for name in ("logical_fks", "human_id_refs"):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


# --- построчные предикаты ---------------------------------------------------
#
# Fail-closed (решение 12): предикат выпускает строку, только если видит
# значение, разрешающее её выпустить. Нет ключа, NULL, незнакомое значение —
# строка не уезжает. Значения разбираются тем же конструктором enum, что и
# путь чтения (``document_repo``: ``Sensitivity(model.sensitivity)``), без
# ``strip``/``lower``: что репозиторий не прочитал бы, то и не уезжает
# (решение 15).

_SENSITIVITY = "sensitivity"
_ROUTINE_SOURCE = "routine"
#: Канонический источник находки: строчный токен. ``" routine"``, ``"Routine"``
#: и пустое — не канон, строка не уезжает.
_SOURCE_TOKEN = re.compile(r"[a-z][a-z0-9_-]*")
#: Решение по подсказке (Q-017); ``pending`` и незнакомое — не решение.
_DECIDED_STATES = frozenset({"accepted", "rejected"})
_LOCAL_EVENT_PREFIX = "link."
_LOCAL_EVENT_KINDS = frozenset({"routine.fired"})
#: Области событий о документе: уезжают только при разрешённой метке.
_DOC_EVENT_SCOPES = frozenset({"doc", "document", "section", "comment"})
#: Области событий без документа. Прочие (``link``, ``run``, ``source_run``,
#: незнакомые, NULL) — local: о производных и локальных сущностях журнал не
#: везёт, а незнакомую область нечем проверить.
_PLAIN_EVENT_SCOPES = frozenset(
    {
        "adr",
        "adr_relation",
        "adr_topic",
        "approval",
        "doc_code_claim",
        "doc_node",
        "finding",
        "plan",
        "plan_section",
        "project",
        "question",
        "routine",
        "scenario",
        "scenario_group",
        "story",
        "story_section",
        "structure_finding",
        "structure_snapshot",
        "structure_waiver",
        "task",
        "task_doc",
    }
)


def parse_sensitivity(value: object) -> Sensitivity | None:
    """Метка документа, как её прочитал бы ``document_repo``; иначе ``None``."""
    if isinstance(value, Sensitivity):
        return value
    if type(value) is not str:
        return None
    try:
        return Sensitivity(value)
    except ValueError:
        return None


#: Метки, с которыми документ уезжает (Q-015). Перечень, а не
#: ``!= restricted``: новая метка ``Sensitivity`` не уезжает, пока её не впишут
#: сюда (решение 17).
_CLEARED_SENSITIVITY = frozenset(
    {Sensitivity.PUBLIC, Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL}
)


def _cleared(value: object) -> bool:
    return parse_sensitivity(value) in _CLEARED_SENSITIVITY


def _parse_entity_kind(value: object) -> EntityKind | None:
    if isinstance(value, EntityKind):
        return value
    if type(value) is not str:
        return None
    try:
        return EntityKind(value)
    except ValueError:
        return None


@dataclass(frozen=True, slots=True)
class DocRef:
    """Ссылка строки на документ или секцию (решение 16).

    Строка, ссылающаяся на документ, несёт метку этого документа под ключом
    ``sensitivity_key(column)`` — её кладёт вызывающий (захват, CLO-006).
    Ссылка без разрешённой метки: ``whole_row`` — строка local целиком (она и
    есть связь с документом); иначе не уезжают ``column`` и ``withheld``, а
    строка уезжает без них. ``kind_column``/``kinds`` — для полиморфных
    ссылок: ссылка на документ, только когда дискриминатор в ``kinds``;
    незнакомый дискриминатор считается ссылкой на документ.

    Ссылку, которую нельзя проверить, гейт считает ссылкой на документ
    (решение 17): колонки нет в строке, NOT NULL-колонка пуста, дискриминатор
    указывает на документ при пустом id. ``None`` значит «ссылки нет» только у
    ``nullable`` колонки без дискриминатора; ``nullable`` сверяется со схемой.
    """

    column: str
    whole_row: bool = False
    withheld: tuple[str, ...] = ()
    kind_column: str | None = None
    kinds: frozenset[str] = frozenset()
    nullable: bool = False


def sensitivity_key(column: str) -> str:
    """Ключ строки, под которым лежит метка документа, на который смотрит ``column``."""
    return f"{column}.{_SENSITIVITY}"


def _points_to_document(ref: DocRef, row: Mapping[str, object]) -> bool:
    """Ведёт ли ссылка на документ; непроверяемое — ведёт (решение 17)."""
    if ref.kind_column is not None:
        kind = row.get(ref.kind_column)
        non_doc = _NON_DOC_KINDS.get(ref.kind_column, frozenset()) - ref.kinds
        # Только заведомо не-документный вид снимает ссылку; пустой id при
        # документном или незнакомом виде — не «ссылки нет».
        return not (isinstance(kind, str) and kind in non_doc)
    if ref.column not in row:
        return True
    return row[ref.column] is not None or not ref.nullable


def ref_cleared(ref: DocRef, row: Mapping[str, object]) -> bool:
    """Ссылка не ведёт на документ или ведёт на документ с разрешённой меткой."""
    if not _points_to_document(ref, row):
        return True
    return _cleared(row.get(sensitivity_key(ref.column)))


_PATH = "path"
_DRIVE = re.compile(r"^[A-Za-z]:")
_URI = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SEPARATORS = re.compile(r"[\\/]")
_PARENT_SEGMENT = ".."


def _is_machine_path(value: str) -> bool:
    """Путь, который описывает машину, а не проект (решения 14, 15).

    Абсолютный POSIX, ``~``, диск Windows, UNC, URI со схемой; пробел по краям
    и NUL — не канон: ``" /Users/…"`` иначе прошёл бы как относительный.
    Сегмент ``..`` (через ``/`` или ``\\``) выводит за корень проекта.
    Пустая строка — тоже машинная: пустой путь не адресует файл проекта, а
    отличить его от недописанного значения нечем (fail-closed).
    """
    return (
        not value
        or value != value.strip()
        or "\x00" in value
        or value.startswith(("/", "~", "\\"))
        or _DRIVE.match(value) is not None
        or _URI.match(value) is not None
        or _PARENT_SEGMENT in _SEPARATORS.split(value)
    )


def _machine_path(row: Mapping[str, object]) -> bool:
    """Обязательный ``path``: нет ключа, не строка или машинный путь — local."""
    value = row.get(_PATH)
    return type(value) is not str or _is_machine_path(value)


def _optional_machine_path(row: Mapping[str, object]) -> bool:
    """Необязательный ``path``: ``NULL`` допустим, машинный путь — нет."""
    value = row.get(_PATH)
    if value is None:
        return False
    return type(value) is not str or _is_machine_path(value)


def _local_document(row: Mapping[str, object]) -> bool:
    return not _cleared(row.get(_SENSITIVITY)) or _machine_path(row)


def _routine_finding(row: Mapping[str, object]) -> bool:
    source = row.get("source")
    if type(source) is not str or _SOURCE_TOKEN.fullmatch(source) is None:
        return True
    return source == _ROUTINE_SOURCE


def _held_finding(row: Mapping[str, object]) -> bool:
    return _routine_finding(row) or _optional_machine_path(row)


def _undecided_suggestion(row: Mapping[str, object]) -> bool:
    state = row.get("state")
    return type(state) is not str or state not in _DECIDED_STATES


#: Ключ гейт-строки ревизии: сущность ревизии уезжает целиком (решение 19).
ENTITY_SHIPS_KEY = "entity_id.ships"


def _local_revision(row: Mapping[str, object]) -> bool:
    kind = _parse_entity_kind(row.get("entity_kind"))
    # Ревизия сущности без uid (derived ``link``) не адресуема на другой реплике.
    if kind is None or ENTITY_KIND_TABLES.get(kind) not in UID_TABLES:
        return True
    return row.get(ENTITY_SHIPS_KEY) is not True


def _local_event(row: Mapping[str, object]) -> bool:
    kind = row.get("kind")
    if type(kind) is not str or not kind:
        return True
    if kind.startswith(_LOCAL_EVENT_PREFIX) or kind in _LOCAL_EVENT_KINDS:
        return True
    scope = row.get("scope_kind")
    return type(scope) is not str or scope not in _DOC_EVENT_SCOPES | _PLAIN_EVENT_SCOPES


_DOC_ENTITY_KINDS = frozenset({EntityKind.DOCUMENT.value, EntityKind.SECTION.value})
_DOC_LINK_KINDS = frozenset({"document", "section"})
#: Значения дискриминатора, о которых известно, что это НЕ документ. Всё вне
#: ``kinds`` и вне этого перечня считается ссылкой на документ (fail-closed).
_NON_DOC_KINDS: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "entity_kind": frozenset(k.value for k in EntityKind) - _DOC_ENTITY_KINDS,
        "scope_kind": _PLAIN_EVENT_SCOPES,
        "to_kind": frozenset(
            {"task", "story", "module", "criterion", "adr", "scenario", "finding", "code", "url"}
        ),
    }
)


def _whole(column: str) -> DocRef:
    return DocRef(column, whole_row=True)


#: Каждая колонка shipped-таблицы, которая ссылается на документ или секцию
#: (FK, логическая, строковая ``doc_key`` или полиморфная). Полноту по FK и
#: по ``doc_key`` сверяет гейт (решение 16).
DOC_REFS: Mapping[str, tuple[DocRef, ...]] = MappingProxyType(
    {
        "section": (_whole("document_id"),),
        "doc_comment": (_whole("document_id"), DocRef("section_id", whole_row=True, nullable=True)),
        "document_tag": (_whole("document_id"),),
        "plan": (DocRef("parent_doc_id", nullable=True), DocRef("completed_log_id", nullable=True)),
        "plan_section": (DocRef("doc_id", nullable=True),),
        "module": (DocRef("spec_doc_id", nullable=True),),
        "scenario": (
            DocRef("document_id", nullable=True, withheld=("section_anchor", "doc_content_hash")),
            DocRef("doc_key", nullable=True, withheld=("section_anchor", "doc_content_hash")),
        ),
        "open_question": (DocRef("source_doc_key", nullable=True),),
        "open_question_link": (
            DocRef("to_ref", whole_row=True, kind_column="to_kind", kinds=_DOC_LINK_KINDS),
        ),
        "story_link": (
            DocRef("to_ref", whole_row=True, kind_column="to_kind", kinds=_DOC_LINK_KINDS),
        ),
        "scenario_link": (
            DocRef("to_ref", whole_row=True, kind_column="to_kind", kinds=_DOC_LINK_KINDS),
        ),
        "external_ref": (
            DocRef(
                "entity_row_id",
                whole_row=True,
                kind_column="entity_kind",
                kinds=_DOC_ENTITY_KINDS,
            ),
        ),
        "approval_doc_revision_link": (_whole("revision_id"),),
        "link_suggestion": (
            _whole("from_section_id"),
            DocRef("to_section_id", whole_row=True, nullable=True),
            _whole("to_doc_key"),
        ),
        "revision": (
            DocRef(
                "entity_id",
                whole_row=True,
                kind_column="entity_kind",
                kinds=_DOC_ENTITY_KINDS,
            ),
        ),
        "activity_event": (
            DocRef(
                "scope_id",
                whole_row=True,
                kind_column="scope_kind",
                kinds=_DOC_EVENT_SCOPES,
                nullable=True,
            ),
        ),
    }
)


def _doc_refs_block_row(table: str, row: Mapping[str, object]) -> bool:
    return any(ref.whole_row and not ref_cleared(ref, row) for ref in DOC_REFS.get(table, ()))


def _gated(table: str, extra: RowPredicate | None = None) -> RowPredicate:
    """``row_is_local`` таблицы: ссылки на документ (``DOC_REFS``) плюс ``extra``."""

    def predicate(row: Mapping[str, object]) -> bool:
        return _doc_refs_block_row(table, row) or (extra is not None and extra(row))

    return predicate


def withheld_columns(table: str, row: Mapping[str, object]) -> frozenset[str]:
    """Колонки строки, которые не уезжают из-за ссылки на restricted-документ."""
    out: set[str] = set()
    for ref in DOC_REFS.get(table, ()):
        if not ref.whole_row and not ref_cleared(ref, row):
            out.add(ref.column)
            out.update(ref.withheld)
    return frozenset(out)


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


_TO_REF: Mapping[str, str] = MappingProxyType({"to_ref": "to_kind"})

_SPECS: tuple[TableSpec, ...] = (
    # --- authored: проект, документы, дерево ---------------------------------
    TableSpec("project", _A, local_columns=frozenset({"root_path", "config_json"})),
    TableSpec(
        "document",
        _A,
        local_columns=frozenset({"projection_hash", "content_sha256_head", "node_position"}),
        row_is_local=_local_document,
        natural_keys=(_name("project_id", "doc_key"),),
        rank_column="node_rank",
    ),
    TableSpec(
        "section",
        _A,
        local_columns=_POSITION,
        row_is_local=_gated("section"),
        natural_keys=(_name("document_id", "anchor"),),
        rank_column=_RANK,
        text_columns=frozenset({"body"}),
    ),
    TableSpec("doc_comment", _A, row_is_local=_gated("doc_comment")),
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
    TableSpec(
        "affected_file", _A, row_is_local=_machine_path, deterministic_uid=("task_id", "path")
    ),
    TableSpec(
        "task_document",
        _A,
        natural_keys=(_name("task_id", "key"),),
        text_columns=frozenset({"body"}),
    ),
    TableSpec(
        "commit_link",
        _A,
        local_columns=frozenset({"message", "author"}),
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
        row_is_local=_gated("story_link"),
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
        row_is_local=_gated("scenario_link"),
        human_id_refs=_TO_REF,
        deterministic_uid=("scenario_row_id", "to_kind", "to_ref", "relation"),
    ),
    # --- authored: модули ---------------------------------------------------
    TableSpec("module", _A, natural_keys=(_name("project_id", "module_id"),)),
    TableSpec("module_dependency", _A, deterministic_uid=("from_module", "to_module")),
    TableSpec("module_code", _A, row_is_local=_machine_path),
    # --- authored: теги (составной PK, uid вычисляется при захвате) ---------
    TableSpec("tag", _A, natural_keys=(_name("project_id", "name"),)),
    TableSpec(
        "document_tag",
        _A,
        row_is_local=_gated("document_tag"),
        deterministic_uid=("document_id", "tag_id"),
    ),
    TableSpec("task_tag", _A, deterministic_uid=("task_id", "tag_id")),
    TableSpec("story_tag", _A, deterministic_uid=("story_id", "tag_id")),
    # --- authored: внешние ссылки, согласования, рутины, находки ------------
    TableSpec(
        "external_ref",
        _A,
        row_is_local=_gated("external_ref"),
        natural_keys=(_name("project_id", "system", "external_id"),),
    ),
    TableSpec("approval", _A, local_columns=frozenset({"run_id", "payload_json"})),
    TableSpec("approval_task_link", _A, human_id_refs={"task_ref": "task"}),
    TableSpec("approval_doc_revision_link", _A, row_is_local=_gated("approval_doc_revision_link")),
    TableSpec("routine", _A, natural_keys=(_name("project_id", "name"),)),
    TableSpec(
        "structure_waiver",
        _A,
        natural_keys=(_name("project_id", "scope", "finding_fingerprint"),),
    ),
    TableSpec(
        "finding",
        _A,
        local_columns=frozenset({"run_id", "miss_streak", "payload"}),
        row_is_local=_held_finding,
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
        row_is_local=_gated("open_question_link"),
        local_columns=frozenset({"resolved", "broken_reason", "last_checked"}),
        human_id_refs=_TO_REF,
        deterministic_uid=("question_row_id", "to_kind", "to_ref", "relation"),
    ),
    # --- journal ------------------------------------------------------------
    TableSpec(
        "revision",
        _J,
        local_columns=frozenset({"run_id"}),
        row_is_local=_gated("revision", _local_revision),
    ),
    TableSpec(
        "activity_event",
        _J,
        local_columns=frozenset({"run_id", "payload"}),
        row_is_local=_gated("activity_event", _local_event),
    ),
    # --- derived ------------------------------------------------------------
    TableSpec("link", _D),
    TableSpec(
        "link_suggestion",
        _D,
        authored_columns=frozenset({"state", "from_section_id", "to_doc_key", "to_section_id"}),
        row_is_local=_gated("link_suggestion", _undecided_suggestion),
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
    # --- local: служебные таблицы синхронизации (0049, RFC 29 §10.1) ---------
    TableSpec("replica", _L),
    TableSpec("sync_op", _L),
    TableSpec("changeset", _L),
    TableSpec("sync_ref", _L),
    TableSpec("sync_lease", _L),
    TableSpec("entity_alias", _L),
    # --- local: конфликты слияния (0051, RFC 29 §3.7) — решение реплики, не данные
    TableSpec("sync_conflict", _L),
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

#: Journal-таблицы, которые уезжают в changeset'ах, → колонка глобального id:
#: адрес операции (``entity_uid``) и ключ объединения при применении (решение 19).
JOURNAL_IDS: Mapping[str, str] = MappingProxyType({"revision": "revision_id"})

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


#: Колонки authored- и journal-таблиц, которые уезжают (решение 13).
#: Явный перечень, а не «все, кроме local_columns»: новая колонка не уезжает,
#: пока её не впишут сюда, а гейт не даёт оставить её без решения.
_SHIPPED_BEFORE_UID: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "activity_event": frozenset(
            {
                "id",
                "project_id",
                "ts",
                "actor_kind",
                "actor_id",
                "kind",
                "scope_kind",
                "scope_id",
                "summary",
            }
        ),
        "adr": frozenset(
            {
                "project_id",
                "adr_id",
                "title",
                "status",
                "decided_at",
                "context",
                "decision",
                "alternatives",
                "consequences",
                "author",
                "created",
                "last_updated",
                "topic_id",
            }
        ),
        "adr_diagram": frozenset({"adr_id", "title", "mermaid"}),
        "adr_relation": frozenset({"from_id", "to_id", "kind", "reason", "at"}),
        "adr_supersedes": frozenset({"superseding_id", "superseded_id", "reason", "at"}),
        "adr_task": frozenset({"adr_row_id", "task_id", "relation"}),
        "adr_topic": frozenset(
            {"project_id", "name", "includes", "excludes", "created", "last_updated"}
        ),
        "affected_file": frozenset({"task_id", "path", "kind"}),
        "approval": frozenset(
            {
                "approval_id",
                "project_id",
                "approval_type",
                "status",
                "requested_by",
                "requested_at",
                "resolved_by",
                "resolved_at",
                "expires_at",
                "decision_comment",
            }
        ),
        "approval_doc_revision_link": frozenset({"approval_id", "revision_id"}),
        "approval_task_link": frozenset({"approval_id", "task_ref"}),
        "commit_link": frozenset({"project_id", "task_id", "sha", "short_sha", "ts"}),
        "dependency": frozenset({"from_task_id", "to_task_id", "kind", "note"}),
        "doc_comment": frozenset(
            {
                "document_id",
                "section_id",
                "anchor",
                "quote",
                "body",
                "author",
                "status",
                "created",
                "last_updated",
            }
        ),
        "doc_node": frozenset(
            {
                "project_id",
                "node_key",
                "parent_id",
                "title",
                "intent",
                "expected_types",
                "min_docs",
                "is_inbox",
            }
        ),
        "document": frozenset(
            {
                "project_id",
                "doc_key",
                "path",
                "type",
                "status",
                "source_of_truth",
                "sensitivity",
                "owner",
                "title",
                "preamble",
                "frontmatter_json",
                "frontmatter_raw",
                "title_in_body",
                "node_id",
                "created",
                "last_updated",
                "last_reviewed",
            }
        ),
        "document_tag": frozenset({"document_id", "tag_id"}),
        "external_ref": frozenset(
            {"project_id", "entity_kind", "entity_row_id", "system", "external_id", "url"}
        ),
        "finding": frozenset(
            {
                "project_id",
                "finding_uid",
                "source",
                "source_ref",
                "fingerprint",
                "severity",
                "kind",
                "title",
                "body",
                "path",
                "line",
                "status",
                "confidence",
                "first_seen_at",
                "last_seen_at",
                "times_seen",
                "promoted_task_id",
            }
        ),
        "module": frozenset(
            {"project_id", "module_id", "name", "status", "spec_doc_id", "plan_id"}
        ),
        "module_code": frozenset({"module_id", "kind", "path"}),
        "module_dependency": frozenset({"from_module", "to_module", "reason"}),
        "open_question": frozenset(
            {
                "project_id",
                "question_id",
                "title",
                "question",
                "context",
                "status",
                "priority",
                "owner",
                "resolution",
                "resolved_by_adr",
                "resolved_at",
                "source_doc_key",
                "author",
                "created",
                "last_updated",
            }
        ),
        "open_question_link": frozenset(
            {"question_row_id", "to_kind", "to_ref", "relation", "note"}
        ),
        "open_question_option": frozenset({"question_row_id", "title", "body", "chosen"}),
        "plan": frozenset(
            {
                "project_id",
                "scope",
                "principle",
                "id_prefix",
                "module_id",
                "parent_doc_id",
                "completed_log_id",
                "created",
                "last_updated",
            }
        ),
        "plan_section": frozenset({"plan_id", "letter", "title", "slug", "doc_id"}),
        "project": frozenset({"slug", "title", "created", "updated"}),
        "revision": frozenset(
            {
                "revision_id",
                "project_id",
                "entity_kind",
                "entity_id",
                "parent_revision_id",
                "author",
                "at",
                "diff",
                "reason",
                "commit_sha",
            }
        ),
        "routine": frozenset(
            {
                "project_id",
                "name",
                "enabled",
                "trigger",
                "cron",
                "check_name",
                "check_args",
                "on_finding",
                "concurrency",
                "catch_up",
                "created",
                "last_updated",
            }
        ),
        "scenario": frozenset(
            {
                "project_id",
                "scenario_id",
                "title",
                "kind",
                "group_key",
                "document_id",
                "doc_key",
                "section_anchor",
                "doc_content_hash",
                "module_id",
                "subject_ref",
                "status",
                "provenance",
                "preconditions",
                "expected",
                "notes",
                "author",
                "created",
                "last_updated",
            }
        ),
        "scenario_link": frozenset({"scenario_row_id", "to_kind", "to_ref", "relation"}),
        "scenario_step": frozenset({"scenario_row_id", "text"}),
        "section": frozenset({"document_id", "anchor", "heading", "level", "body", "content_hash"}),
        "story_acceptance": frozenset({"story_id", "criterion", "met"}),
        "story_link": frozenset({"story_id", "to_kind", "to_ref", "relation"}),
        "story_section": frozenset({"project_id", "key", "title"}),
        "story_tag": frozenset({"story_id", "tag_id"}),
        "structure_waiver": frozenset(
            {
                "project_id",
                "finding_fingerprint",
                "owner",
                "reason",
                "scope",
                "expires_at",
                "created",
            }
        ),
        "tag": frozenset({"project_id", "name"}),
        "task": frozenset(
            {
                "project_id",
                "task_id",
                "plan_id",
                "section_id",
                "title",
                "status",
                "type",
                "priority",
                "description",
                "acceptance",
                "created",
                "last_updated",
                "completed_at",
                "completed_commit",
                "blocked_reason",
                "normalized_title",
                "checked_out_by",
                "checked_out_at",
                "expected_status_at_checkout",
            }
        ),
        "task_document": frozenset(
            {
                "task_id",
                "key",
                "title",
                "body",
                "format",
                "current_revision_id",
                "created",
                "last_updated",
            }
        ),
        "task_tag": frozenset({"task_id", "tag_id"}),
        "user_story": frozenset(
            {
                "project_id",
                "story_id",
                "persona",
                "narrative",
                "status",
                "priority",
                "section_id",
                "created",
                "last_updated",
            }
        ),
    }
)

#: Тег-таблицы с составным PK: колонки ``uid`` у них нет, uid ребра
#: вычисляется при захвате той же ``edge_uid`` (RFC 29 §3.3, §10.1; решение 18).
COMPOSITE_PK_TABLES: frozenset[str] = frozenset({"document_tag", "task_tag", "story_tag"})

#: Таблицы с колонкой ``uid`` (миграция 0049): authored без составного PK.
UID_TABLES: frozenset[str] = (
    frozenset(t for t, s in REGISTRY.items() if s.cls is SyncClass.AUTHORED) - COMPOSITE_PK_TABLES
)


def _with_identity_and_rank(table: str, cols: frozenset[str]) -> frozenset[str]:
    if table in UID_TABLES:
        cols |= {"uid"}
    rank = REGISTRY[table].rank_column
    return cols | {rank} if rank else cols


#: ``uid`` — сама идентичность строки: уезжает у каждой таблицы, где он есть;
#: ``rank_column`` — синхронизируемый порядок (0050, Q-009), в отличие от
#: local-позиции рядом с ним.
SHIPPED_COLUMNS: Mapping[str, frozenset[str]] = MappingProxyType(
    {table: _with_identity_and_rank(table, cols) for table, cols in _SHIPPED_BEFORE_UID.items()}
)

#: Пространство имён uuid5 рёбер. Константа навсегда: смена значения меняет
#: uid всех рёбер на всех репликах, а миграция 0049 держит свою копию.
EDGE_UID_NAMESPACE = uuid.UUID("03a4b384-e781-5d7b-90ee-d862b599d935")


def edge_canonical(table: str, row: Mapping[str, object]) -> str:
    """Каноническая строка ребра: JSON ``[таблица, *значения ключа]``.

    Значения — в порядке ``deterministic_uid``; FK-колонки ключа уже
    переведены в uid концов (RFC 29 §3.3: «uid концов»), остальное — как в
    строке (``kind``, ``relation``, ``to_ref``, ``path``, ``sha``). JSON, а не
    склейка разделителем: значение с разделителем внутри не склеится с соседним.
    ``KeyError`` — у таблицы нет детерминированного uid или в строке нет колонки.
    """
    columns = spec_for(table).deterministic_uid
    if columns is None:
        msg = f"sync_registry: у {table!r} нет deterministic_uid"
        raise KeyError(msg)
    values = [row[column] for column in columns]
    return json.dumps([table, *values], ensure_ascii=False, separators=(",", ":"))


def edge_uid(table: str, row: Mapping[str, object]) -> str:
    """Детерминированный uid ребра: ``uuid5(EDGE_UID_NAMESPACE, edge_canonical)``.

    Одно ребро, добавленное на двух репликах, получает один uid, и add-wins
    сводится к идемпотентной вставке (RFC 29 §3.3). Бэкфилл миграции 0049
    считает так же — сверяет ``tests/infra/test_migration_0049_preserves_data.py``.
    """
    return str(uuid.uuid5(EDGE_UID_NAMESPACE, edge_canonical(table, row)))


def spec_for(table: str) -> TableSpec:
    """Запись реестра; ``KeyError`` — незаклассифицированная таблица, баг."""
    return REGISTRY[table]


def authored_tables() -> list[TableSpec]:
    """Authored-таблицы в порядке имён."""
    return sorted(
        (spec for spec in REGISTRY.values() if spec.cls is SyncClass.AUTHORED),
        key=lambda spec: spec.table,
    )


_SHIPPED_CLASSES = frozenset({SyncClass.AUTHORED, SyncClass.JOURNAL})


def column_class(table: str, column: str) -> SyncClass:
    """Класс колонки; неизвестная колонка — ``LOCAL`` (fail-closed, решение 13).

    ``row_id`` — локальный PK, на другой реплике бессмыслен: всегда ``LOCAL``.
    Незнакомая таблица — ``KeyError`` (как у ``spec_for``).
    """
    spec = spec_for(table)
    if column in spec.local_columns:
        return SyncClass.LOCAL
    if column in spec.authored_columns:
        return SyncClass.AUTHORED
    if spec.cls not in _SHIPPED_CLASSES:
        # derived/local: колонка не уезжает, класс — класс таблицы
        return spec.cls
    # authored/journal без перечня — забытая запись: не уезжает ничего
    shipped = SHIPPED_COLUMNS.get(table, frozenset())
    return spec.cls if column in shipped else SyncClass.LOCAL


def row_class(table: str, row: Mapping[str, object]) -> SyncClass:
    """Класс конкретной строки с учётом построчных исключений (решение 2)."""
    spec = spec_for(table)
    if spec.row_is_local is not None and spec.row_is_local(row):
        return _ROW_CLASS_WHEN_HELD.get(table, SyncClass.LOCAL)
    return spec.cls


def decision_columns(table: str) -> frozenset[str]:
    """Authored-колонки derived-таблицы вне ключа строки: само решение (Q-017).

    У ``link_suggestion`` — ``state``. Своя derived-строка до решения local
    (``row_is_local``), и remote пишет решение поверх неё: применение
    сверяет гейт со строкой, в которую уже подставлены эти колонки
    (CLO-025). У authored-таблиц решений нет — пусто.
    """
    spec = spec_for(table)
    if spec.cls is not SyncClass.DERIVED:
        return frozenset()
    return spec.authored_columns - frozenset(spec.deterministic_uid or ())


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
