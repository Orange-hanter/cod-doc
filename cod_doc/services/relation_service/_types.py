"""Типы и ошибки `relation_service` (RFC 39 §3.4, §3.6)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from cod_doc.domain.relation_types import RelationType
    from cod_doc.domain.relations import EntityRef


class RelationError(ValueError):
    """Ошибка связи — подсказка: все найденные проблемы разом и пример вызова.

    ``problems`` — по одной строке на неверный параметр; ``hint`` — что
    допустимо и как выглядит верный вызов (может быть пустым).
    """

    def __init__(self, problems: list[str], hint: str = "") -> None:
        self.problems = problems
        self.hint = hint
        super().__init__("\n".join([*problems, hint] if hint else problems))


@dataclass(frozen=True)
class RelationEdge:
    """Ребро в том виде, в каком оно записано: канонический тип и направление."""

    type: str
    from_ref: EntityRef
    to_ref: EntityRef
    from_uid: str
    to_uid: str | None
    note: str | None
    resolved: bool
    #: Вызов был по обратному имени: ребро записано от цели к источнику.
    reversed: bool
    #: ``False`` — такое ребро уже было, событие не писалось.
    created: bool


@dataclass(frozen=True)
class RelationView:
    label: str  # name со стороны источника, inverse со стороны цели
    type: str
    other: EntityRef
    other_title: str | None
    direction: Literal["out", "in"]
    source: str
    resolved: bool
    note: str | None


@dataclass(frozen=True)
class RelationTypeSpec:
    name: str
    inverse: str
    from_kinds: list[str]
    to_kinds: list[str]
    symmetric: bool = False
    acyclic: bool = False
    allow_forward: bool = False
    description: str = ""


@dataclass(frozen=True)
class RelationTypeInfo:
    type: RelationType
    builtin: bool
    deprecated: bool
    #: Рёбер этого типа в таблице `relation` проекта (RFC §8).
    edges: int


@dataclass
class VerifyReport:
    checked: int = 0
    resolved: int = 0
    broken: list[str] = field(default_factory=list)
    filled: int = 0
