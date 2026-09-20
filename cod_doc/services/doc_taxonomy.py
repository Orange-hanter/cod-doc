"""Дефолтное дерево документации и детерминированная раскладка по нему.

ADO-116. Модуль чистый: ни ``Session``, ни конфига, ни обращений к диску —
только описание разделов и правила «какой документ куда». Всё, что пишет в
БД, живёт в :mod:`cod_doc.services.doc_tree_service`.

Правило одно и названо прямо: **побеждает первое совпадение**, а условия
внутри правила соединяются конъюнкцией. Это ровно то, чего не делает
прежний навигатор (`nav_service._JOURNEY`, удалён): там ``type_ok or pat_ok``
по подстроке, поэтому один
документ попадает сразу в несколько шагов, а шаг «Data Model» собирает все
``module-spec`` корпуса — 78 документов из 170 на cod-doc.

Несовпавший документ даёт ``None`` и уходит в Инбокс. Инбокс — не свалка, а
рабочий сигнал: пока в нём что-то лежит, дерево не разложено, и это видно на
экране числом. Отсутствие такого состояния и есть причина, по которой
хаотичный импорт растворялся в корпусе бесследно.

Правила заведомо неполны для чужого репозитория, и так и задумано: скелет
правится руками (``doc_node_create``), остаток разбирает агент-куратор с
подтверждением человека. На корпусе cod-doc этот набор размещает 156 из 170.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cod_doc.domain.entities import DocumentType

# Ключи разделов. Строковые константы, потому что это данные проекта, а не
# словарь домена: проект волен переименовать раздел и завести свои.
INBOX_KEY = "inbox"


@dataclass(frozen=True, slots=True)
class NodeSpec:
    """Раздел дефолтного дерева.

    ``min_docs`` — порог «раздел пуст» для куратора. Ноль означает «раздел
    может быть пустым законно»: у аудита в новом проекте отчётов ещё нет, и
    требовать их — шум, а не находка.
    """

    node_key: str
    title: str
    intent: str
    expected_types: tuple[str, ...] = ()
    min_docs: int = 0
    is_inbox: bool = False


@dataclass(frozen=True, slots=True)
class PlacementRule:
    """Правило раскладки. Пустое поле — условие не проверяется.

    Все заданные условия обязаны выполниться одновременно; проверяются
    правила по порядку, срабатывает первое подошедшее.
    """

    node_key: str
    types: tuple[DocumentType, ...] = ()
    key_prefixes: tuple[str, ...] = ()
    key_contains: tuple[str, ...] = ()
    exact_keys: tuple[str, ...] = ()
    #: Последний сегмент ключа: файл с таким именем — точка входа своего
    #: каталога, в какой бы глубине он ни лежал.
    basenames: tuple[str, ...] = ()
    #: Почему правило существует — попадает в evidence предложения куратора.
    reason: str = ""

    def matches(self, doc_key: str, doc_type: DocumentType) -> bool:
        key = doc_key.lower()
        if self.exact_keys and key in self.exact_keys:
            return True
        if self.basenames and key.rsplit("/", 1)[-1] in self.basenames:
            return True
        # exact_keys и basenames — самостоятельные условия («вот эти документы
        # сюда»), а не ещё один конъюнкт: иначе правило с ними не сработает ни
        # на чём, кроме перечисленного, что делает остальные поля мёртвыми.
        if (self.exact_keys or self.basenames) and not (
            self.types or self.key_prefixes or self.key_contains
        ):
            return False
        if self.types and doc_type not in self.types:
            return False
        if self.key_prefixes and not any(key.startswith(p) for p in self.key_prefixes):
            return False
        return not (self.key_contains and not any(c in key for c in self.key_contains))


DEFAULT_TREE: tuple[NodeSpec, ...] = (
    NodeSpec(
        node_key="entry",
        title="Точки входа",
        intent=(
            "Откуда начинают читать: навигатор проекта, README, гид "
            "контрибьютора. Документы, на которые ссылаются извне и с которых "
            "начинается онбординг."
        ),
        expected_types=("guide", "module-spec"),
        min_docs=1,
    ),
    NodeSpec(
        node_key="vision",
        title="Видение",
        intent="Зачем проект существует, для кого, какими принципами ограничен.",
        expected_types=("vision",),
        min_docs=1,
    ),
    NodeSpec(
        node_key="architecture",
        title="Архитектура",
        intent="Структура системы: слои, границы, компоненты и их взаимодействие.",
        expected_types=("architecture",),
        min_docs=1,
    ),
    NodeSpec(
        node_key="data-model",
        title="Модель данных",
        intent="Сущности домена, схема БД, инварианты и связи между таблицами.",
        expected_types=("module-spec",),
        min_docs=1,
    ),
    NodeSpec(
        node_key="capabilities",
        title="Возможности",
        intent="Что система умеет — по способностям, а не по модулям кода.",
        expected_types=("capability",),
    ),
    NodeSpec(
        node_key="scenarios",
        title="Сценарии",
        intent="Наборы сценариев поведения: happy path, отказы, граничные случаи.",
        expected_types=("scenario-set",),
    ),
    NodeSpec(
        node_key="standards",
        title="Стандарты",
        intent="Соглашения и правила: код, документация, процесс, данные.",
        expected_types=("standard",),
    ),
    NodeSpec(
        node_key="skills",
        title="Скиллы",
        intent="Инструкции для агента — по одному документу на скилл.",
        expected_types=("module-spec",),
    ),
    NodeSpec(
        node_key="proposals",
        title="RFC и предложения",
        intent="Предложения об изменениях: контекст, предложение, миграция, риски.",
        expected_types=("analysis", "design", "module-spec"),
    ),
    NodeSpec(
        node_key="roadmap",
        title="Планы и роадмап",
        intent="Приоритеты, милстоуны, планы исполнения и их секции.",
        expected_types=("plan", "execution-plan"),
    ),
    NodeSpec(
        node_key="audit",
        title="Аудит и отчёты",
        intent=(
            "Отчёты о закрытии фаз, сверки и журналы. Растёт со временем и "
            "почти не перечитывается — держится отдельно, чтобы не забивать "
            "остальное дерево."
        ),
        expected_types=("audit-report", "audit", "journal", "execution-log"),
    ),
    NodeSpec(
        node_key=INBOX_KEY,
        title="Инбокс",
        intent=(
            "Документы, которые правилам не подошли. Не свалка, а очередь "
            "разбора: пока раздел не пуст, дерево не разложено."
        ),
        is_inbox=True,
    ),
)

#: Порядок здесь — часть смысла: первое совпадение побеждает. Узкие правила
#: (точные ключи, конкретные каталоги) обязаны стоять выше широких по типу,
#: иначе широкое правило перехватит документ раньше узкого.
DEFAULT_RULES: tuple[PlacementRule, ...] = (
    PlacementRule(
        node_key="entry",
        exact_keys=("agents", "claude", "docs/handbook"),
        reason="гид контрибьютора",
    ),
    PlacementRule(
        node_key="vision",
        types=(DocumentType.VISION,),
        reason="тип vision",
    ),
    PlacementRule(
        node_key="vision",
        key_contains=("vision",),
        reason="ключ содержит vision",
    ),
    PlacementRule(
        node_key="architecture",
        types=(DocumentType.ARCHITECTURE,),
        reason="тип architecture",
    ),
    PlacementRule(
        node_key="architecture",
        key_contains=("architecture",),
        reason="ключ содержит architecture",
    ),
    PlacementRule(
        node_key="data-model",
        key_contains=("data_model", "data-model"),
        reason="ключ содержит data_model",
    ),
    PlacementRule(
        node_key="data-model",
        key_prefixes=("models/",),
        reason="каталог models/",
    ),
    PlacementRule(
        node_key="capabilities",
        types=(DocumentType.CAPABILITY,),
        reason="тип capability",
    ),
    PlacementRule(
        node_key="capabilities",
        key_contains=("/capabilities/",),
        reason="каталог capabilities/",
    ),
    PlacementRule(
        node_key="scenarios",
        types=(DocumentType.SCENARIO_SET,),
        reason="тип scenario-set",
    ),
    PlacementRule(
        node_key="scenarios",
        key_contains=("/scenarios/",),
        reason="каталог scenarios/",
    ),
    PlacementRule(
        node_key="standards",
        types=(DocumentType.STANDARD,),
        reason="тип standard",
    ),
    PlacementRule(
        node_key="standards",
        key_contains=("/standards/",),
        reason="каталог standards/",
    ),
    # Скиллы стоят выше proposals и roadmap: их ключ содержит `/skills/` и ни
    # одно широкое правило по типу не должно перехватить их раньше.
    PlacementRule(
        node_key="skills",
        key_contains=("/skills/",),
        reason="каталог skills/",
    ),
    PlacementRule(
        node_key="proposals",
        key_prefixes=("proposals/",),
        reason="каталог proposals/",
    ),
    PlacementRule(
        node_key="roadmap",
        types=(DocumentType.PLAN, DocumentType.EXECUTION_PLAN),
        reason="тип plan/execution-plan",
    ),
    PlacementRule(
        node_key="roadmap",
        key_contains=("/roadmap/",),
        reason="каталог roadmap/",
    ),
    PlacementRule(
        node_key="audit",
        types=(
            DocumentType.AUDIT_REPORT,
            DocumentType.AUDIT,
            DocumentType.JOURNAL,
            DocumentType.EXECUTION_LOG,
        ),
        reason="тип audit/journal/execution-log",
    ),
    PlacementRule(
        node_key="audit",
        key_contains=("/audit/",),
        reason="каталог audit/",
    ),
    PlacementRule(
        node_key="entry",
        types=(DocumentType.GUIDE,),
        reason="тип guide",
    ),
    # Намеренно последнее правило. MASTER/README — точка входа своего каталога
    # на любой глубине, но только если каталог не разобран правилом выше:
    # `proposals/README` — индекс RFC и остаётся в RFC, а `docs/system/MASTER`
    # каталогом не покрыт и становится точкой входа.
    PlacementRule(
        node_key="entry",
        basenames=("master", "readme", "index"),
        reason="точка входа каталога, не покрытого другим правилом",
    ),
)


@dataclass(frozen=True, slots=True)
class Placement:
    """Результат раскладки одного документа."""

    doc_key: str
    node_key: str | None
    reason: str = ""

    @property
    def placed(self) -> bool:
        return self.node_key is not None


@dataclass(slots=True)
class ClassifyReport:
    """Итог прогона по корпусу — то, что печатают CLI и MCP."""

    placements: list[Placement] = field(default_factory=list)

    @property
    def placed(self) -> list[Placement]:
        return [p for p in self.placements if p.placed]

    @property
    def unplaced(self) -> list[Placement]:
        return [p for p in self.placements if not p.placed]

    @property
    def by_node(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for p in self.placed:
            assert p.node_key is not None
            out[p.node_key] = out.get(p.node_key, 0) + 1
        return out


def classify(
    doc_key: str,
    doc_type: DocumentType,
    rules: tuple[PlacementRule, ...] = DEFAULT_RULES,
) -> Placement:
    """Куда положить документ. ``node_key=None`` — в Инбокс.

    Первое подошедшее правило побеждает; порядок ``rules`` — часть контракта.
    """
    for rule in rules:
        if rule.matches(doc_key, doc_type):
            return Placement(doc_key=doc_key, node_key=rule.node_key, reason=rule.reason)
    return Placement(doc_key=doc_key, node_key=None, reason="ни одно правило не подошло")


def node_spec(node_key: str) -> NodeSpec | None:
    """Описание раздела дефолтного дерева по ключу."""
    return next((n for n in DEFAULT_TREE if n.node_key == node_key), None)
