"""Словарь проекта: встроенные типы плюс строки `relation_type`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.domain.relation_types import (
    BUILTIN_TYPES,
    RelationType,
    Vocabulary,
    VocabularyConflictError,
)
from cod_doc.domain.relations import RefKind
from cod_doc.infra.models import RelationTypeModel

from ._types import RelationError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Тип с бэкендом не в `relation` пишется своим сервисом; диспетчер — REL-017.
BACKEND_TOOLS = {
    "dependency": "task_add_dependency",
    "adr_relation": "adr_relate",
    "adr_supersedes": "adr_supersede",
}


@dataclass(frozen=True)
class Loaded:
    types: list[RelationType]
    vocab: Vocabulary
    project_types: frozenset[RelationType]
    deprecated: frozenset[RelationType]


def to_domain(model: RelationTypeModel) -> RelationType:
    return RelationType(
        name=model.name,
        inverse=model.inverse,
        from_kinds=frozenset(RefKind(k) for k in model.from_kinds),
        to_kinds=frozenset(RefKind(k) for k in model.to_kinds),
        symmetric=model.symmetric,
        acyclic=model.acyclic,
        allow_forward=model.allow_forward,
        description=model.description or "",
    )


def load(session: Session, project_id: int) -> Loaded:
    rows = session.scalars(
        select(RelationTypeModel)
        .where(RelationTypeModel.project_id == project_id)
        .order_by(RelationTypeModel.row_id)
    ).all()
    project = [(to_domain(m), m.deprecated) for m in rows]
    types = [*BUILTIN_TYPES, *(t for t, _ in project)]
    return Loaded(
        types=types,
        vocab=_build(types),
        project_types=frozenset(t for t, _ in project),
        deprecated=frozenset(t for t, dep in project if dep),
    )


def _build(types: list[RelationType]) -> Vocabulary:
    try:
        return Vocabulary.build(types)
    except VocabularyConflictError as exc:  # проектные типы не должны ломать чтение
        raise RelationError([str(exc)]) from exc


def check_backend(rt: RelationType, token: str) -> None:
    tool = BACKEND_TOOLS.get(rt.backend)
    if tool is not None:
        msg = f"тип {rt.name!r} хранится в backend {rt.backend!r}, а не в relation"
        raise RelationError(
            [msg], f"Пока используйте {tool} (единый вход — REL-017); токен {token!r}"
        )


def unknown_type_error(
    loaded: Loaded, token: str, from_ref: str, to_ref: str, f: RefKind, t: RefKind
) -> RelationError:
    """Ошибка «тип не определён для пары»: допустимое, похожее и готовый пример."""
    usable = [rt for rt in loaded.vocab.allowed(f, t) if rt not in loaded.deprecated]
    names: list[str] = []
    inverses: list[str] = []
    for rt in usable:
        alias = f" (алиас {', '.join(sorted(rt.aliases))})" if rt.aliases else ""
        # Прямое чтение: пара допустима как from → to; иначе тип годится только обратным именем.
        if f in rt.from_kinds and t in rt.to_kinds:
            names.append(rt.name + alias)
        if rt.name != rt.inverse and f in rt.to_kinds and t in rt.from_kinds:
            inverses.append(rt.inverse)
    msg = f"relation {token!r} не определена для {f.value} → {t.value}"
    similar = loaded.vocab.suggest(token, f, t)
    if similar:
        msg += f". Похоже: {similar[0]}"
    only_fallback = {rt.name for rt in usable} <= {"relates_to"}
    if only_fallback:
        hint = (
            f"Для {f.value} → {t.value} допустим только relates_to; "
            "для своего отношения заведите тип через relation_type_define."
        )
    else:
        hint = (
            f"Допустимо для {f.value} → {t.value}: {', '.join(names)}"
            + (f"; обратные: {', '.join(inverses)}" if inverses else "")
            + "."
        )
    example = similar[0] if similar else (names[0].split(" ")[0] if names else "relates_to")
    hint += f'\nПример: relate(from_ref="{from_ref}", type="{example}", to_ref="{to_ref}")'
    return RelationError([msg], hint)
