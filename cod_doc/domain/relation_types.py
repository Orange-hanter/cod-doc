"""Словарь отношений графа связей (RFC 39 §3.2). Чистый домен, без БД.

Тип отношения — запись, а не член enum. `Vocabulary.build` раскрывает «любой»
и множества видов в конечную таблицу `(токен, from_kind, to_kind) → (тип,
направление)` и ловит неоднозначность при загрузке, а не при вызове.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from cod_doc.domain.relations import ENTITY_KINDS, RefKind

_ALL_KINDS: frozenset[RefKind] = frozenset(RefKind)
_MAX_SUGGEST_DISTANCE = 2

#: Потребители, которые существуют сейчас: dotted-путь к реальному вызываемому.
#: `relation_service.relations` читает каждый тип (обратное имя, симметричность)
#: и отдаёт ленту MCP `relations` и CLI `rel ls`. Гейт
#: `test_relation_types_have_consumers` требует у каждого встроенного типа хотя бы
#: одного потребителя отсюда и проверяет, что путь разрешается.
KNOWN_CONSUMERS: frozenset[str] = frozenset({"relation_service.relations"})

#: Потребители, которых ещё нет: веб-панель и контекст (REL-012), `plan_ready` и
#: `adr_graph` на едином чтении (REL-016). Тип может их перечислять, но одних
#: планируемых мало — иначе гейт перестал бы отличать метку от типа. Перенеси имя
#: в `KNOWN_CONSUMERS`, когда читатель появится.
PLANNED_CONSUMERS: frozenset[str] = frozenset(
    {"context_service.L1", "web.relations_panel", "plan_ready", "adr_graph"}
)

_READER = "relation_service.relations"


@dataclass(frozen=True)
class RelationType:
    name: str  # читается от источника
    inverse: str  # читается от цели
    from_kinds: frozenset[RefKind]
    to_kinds: frozenset[RefKind]
    symmetric: bool = False  # хранится одна строка, A→B ≡ B→A
    acyclic: bool = False
    allow_forward: bool = False  # цель может ещё не существовать
    backend: str = "relation"  # relation | dependency | adr_relation | adr_supersedes
    aliases: frozenset[str] = frozenset()
    consumers: frozenset[str] = frozenset()  # кто читает тип; пусто — тип не встроенный
    description: str = ""


@dataclass(frozen=True)
class Resolution:
    type: RelationType
    reversed: bool  # токен — обратное имя: ребро хранится от цели к источнику


class VocabularyConflictError(ValueError):
    """Два типа одной специфичности претендуют на одну тройку `(токен, from, to)`."""


def _t(
    name: str,
    inverse: str,
    from_kinds: frozenset[RefKind] | set[RefKind],
    to_kinds: frozenset[RefKind] | set[RefKind],
    consumers: set[str],
    description: str,
    **flags: object,
) -> RelationType:
    return RelationType(
        name=name,
        inverse=inverse,
        from_kinds=frozenset(from_kinds),
        to_kinds=frozenset(to_kinds),
        consumers=frozenset(consumers) | {_READER},
        description=description,
        **flags,  # type: ignore[arg-type]
    )


_K = RefKind
_L1 = "context_service.L1"
_WEB = "web.relations_panel"
_READY = "plan_ready"
_ADR = "adr_graph"

BUILTIN_TYPES: tuple[RelationType, ...] = (
    _t(
        "relates_to",
        "relates_to",
        ENTITY_KINDS,
        ENTITY_KINDS,
        {_L1, _WEB},
        "общий запасной вариант",
        symmetric=True,
        aliases=frozenset({"see_also"}),
    ),
    _t(
        "depends_on",
        "required_by",
        {_K.STORY},
        {_K.STORY},
        {_L1, _WEB},
        "история зависит от истории",
        acyclic=True,
    ),
    *(
        _t("duplicates", "duplicated_by", {k}, {k}, {_WEB}, "дубль того же вида")
        for k in (_K.STORY, _K.TASK, _K.QUESTION)
    ),
    _t(
        "splits_into",
        "split_from",
        {_K.STORY},
        {_K.STORY},
        {_L1, _WEB},
        "история разбита на истории",
        acyclic=True,
    ),
    _t(
        "implemented_by",
        "implements",
        {_K.STORY},
        {_K.TASK},
        {_L1, _WEB},
        "история реализуется задачей",
    ),
    _t(
        "specified_in",
        "specifies",
        {_K.STORY, _K.SCENARIO},
        {_K.DOCUMENT, _K.SECTION},
        {_L1, _WEB},
        "где описано",
    ),
    _t("owned_by", "owns", {_K.STORY}, {_K.MODULE}, {_L1}, "модуль-владелец истории"),
    _t(
        "verifies",
        "verified_by",
        {_K.SCENARIO},
        {_K.TASK, _K.STORY, _K.CRITERION, _K.DOCUMENT},
        {_L1, _WEB},
        "сценарий проверяет",
    ),
    _t(
        "exercised_by",
        "exercises",
        {_K.SCENARIO},
        {_K.TASK, _K.CODE},
        {_L1},
        "что исполняет сценарий",
    ),
    _t(
        "about",
        "discussed_in",
        {_K.QUESTION},
        _ALL_KINDS,
        {_L1, _WEB},
        "вопрос о сущности",
        allow_forward=True,
    ),
    _t(
        "blocks",
        "blocked_by",
        {_K.QUESTION},
        _ALL_KINDS,
        {_L1, _WEB},
        "вопрос блокирует сущность",
        allow_forward=True,
    ),
    _t(
        "addressed_by",
        "addresses",
        {_K.QUESTION},
        {_K.TASK, _K.ADR, _K.DOCUMENT},
        {_L1, _WEB},
        "вопрос адресован",
    ),
    _t(
        "resolved_by",
        "resolves",
        {_K.QUESTION},
        {_K.TASK, _K.ADR, _K.DOCUMENT},
        {_L1, _WEB},
        "вопрос закрыт",
    ),
    _t(
        "implemented_by",
        "implements",
        {_K.ADR},
        {_K.TASK},
        {_ADR, _WEB},
        "решение реализует задача",
    ),
    _t(
        "invalidated_by",
        "invalidates",
        {_K.ADR},
        {_K.TASK},
        {_ADR, _WEB},
        "решение отменила задача",
    ),
    _t("discovered_by", "discovers", {_K.ADR}, {_K.TASK}, {_ADR, _WEB}, "решение найдено задачей"),
    _t("decided_in", "decides", {_K.ADR}, {_K.DOCUMENT}, {_ADR, _WEB}, "где принято решение"),
    _t(
        "blocked_by",
        "blocks",
        {_K.TASK},
        {_K.TASK},
        {_READY, _L1},
        "задача ждёт задачу",
        acyclic=True,
        backend="dependency",
    ),
    _t(
        "amends",
        "amended_by",
        {_K.ADR},
        {_K.ADR},
        {_ADR},
        "ADR уточняет ADR",
        acyclic=True,
        backend="adr_relation",
    ),
    _t(
        "depends_on",
        "required_by",
        {_K.ADR},
        {_K.ADR},
        {_ADR},
        "ADR зависит от ADR",
        acyclic=True,
        backend="adr_relation",
    ),
    _t(
        "supersedes",
        "superseded_by",
        {_K.ADR},
        {_K.ADR},
        {_ADR, _WEB},
        "ADR заменяет ADR",
        acyclic=True,
        backend="adr_supersedes",
    ),
)


def levenshtein(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


@dataclass(frozen=True)
class _Entry:
    type: RelationType
    reversed: bool
    specific: bool  # False — тройка получена раскрытием «любой»


def _is_wild(kinds: frozenset[RefKind]) -> bool:
    return kinds >= ENTITY_KINDS


@dataclass(frozen=True)
class Vocabulary:
    entries: dict[tuple[str, RefKind, RefKind], _Entry] = field(default_factory=dict)

    @classmethod
    def build(cls, types: tuple[RelationType, ...] | list[RelationType]) -> Vocabulary:
        entries: dict[tuple[str, RefKind, RefKind], _Entry] = {}
        for rt in types:
            specific = not (_is_wild(rt.from_kinds) or _is_wild(rt.to_kinds))
            for f in rt.from_kinds:
                for t in rt.to_kinds:
                    # У симметричного типа обратное имя совпадает с прямым и
                    # покрыто обеими перестановками пар.
                    tokens = [(n, f, t, False) for n in (rt.name, *rt.aliases)]
                    if not rt.symmetric:
                        tokens.append((rt.inverse, t, f, True))
                    for token, ef, et, rev in tokens:
                        cls._put(entries, (token, ef, et), _Entry(rt, rev, specific))
        return cls(entries)

    @staticmethod
    def _put(
        entries: dict[tuple[str, RefKind, RefKind], _Entry],
        key: tuple[str, RefKind, RefKind],
        new: _Entry,
    ) -> None:
        old = entries.get(key)
        if old is None or old.type is new.type:
            entries.setdefault(key, new)
        elif old.specific == new.specific:
            msg = (
                f"неоднозначный токен {key[0]!r} для {key[1].value} → {key[2].value}: "
                f"типы {old.type.name!r} (backend={old.type.backend}) и "
                f"{new.type.name!r} (backend={new.type.backend}) одной специфичности"
            )
            raise VocabularyConflictError(msg)
        elif new.specific:
            entries[key] = new

    def lookup(self, token: str, from_kind: RefKind, to_kind: RefKind) -> Resolution | None:
        entry = self.entries.get((token, from_kind, to_kind))
        return Resolution(entry.type, entry.reversed) if entry else None

    def allowed(self, from_kind: RefKind, to_kind: RefKind) -> list[RelationType]:
        found = {
            id(e.type): e.type
            for (_, f, t), e in self.entries.items()
            if f is from_kind and t is to_kind
        }
        return [t for t in BUILTIN_TYPES if id(t) in found] + [
            t for t in found.values() if all(t is not b for b in BUILTIN_TYPES)
        ]

    def suggest(self, token: str, from_kind: RefKind, to_kind: RefKind) -> list[str]:
        """Токены пары на расстоянии Левенштейна ≤ 2 от `token`, ближайшие первыми."""
        scored = sorted(
            (levenshtein(token, tok), tok)
            for (tok, f, t) in self.entries
            if f is from_kind and t is to_kind and tok != token
        )
        return [tok for dist, tok in scored if dist <= _MAX_SUGGEST_DISTANCE]


BUILTIN_VOCABULARY = Vocabulary.build(BUILTIN_TYPES)
