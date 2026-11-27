"""Словарь проекта: чтение, заведение и устаревание типов (RFC 39 §3.2)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from cod_doc.domain.relation_types import (
    BUILTIN_TYPES,
    BUILTIN_VOCABULARY,
    RelationType,
    Vocabulary,
    VocabularyConflictError,
)
from cod_doc.domain.relations import ENTITY_KINDS, RefKind
from cod_doc.infra.models import RelationModel, RelationTypeModel
from cod_doc.services import activity_service

from ._types import RelationError, RelationTypeInfo, RelationTypeSpec
from ._vocab import load

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_NAME = re.compile(r"[a-z][a-z0-9_]{1,47}")
_AUTHOR_MAX = 128


def relation_types(
    session: Session,
    *,
    project_id: int,
    from_kind: str | None = None,
    to_kind: str | None = None,
) -> list[RelationTypeInfo]:
    """Встроенные и проектные типы (с числом рёбер); фильтр по виду концов."""
    f, t = _kind(from_kind, "from_kind"), _kind(to_kind, "to_kind")
    loaded = load(session, project_id)
    counts = session.execute(
        select(RelationModel.type, RelationModel.from_kind, RelationModel.to_kind, func.count())
        .where(RelationModel.project_id == project_id)
        .group_by(RelationModel.type, RelationModel.from_kind, RelationModel.to_kind)
    ).all()
    out = []
    for rt in loaded.types:
        if (f is not None and f not in rt.from_kinds) or (t is not None and t not in rt.to_kinds):
            continue
        # Имя не уникально (depends_on у истории и у ADR): ребро — тому типу, чьи виды совпали.
        edges = sum(
            n
            for name, fk, tk, n in counts
            if name == rt.name and RefKind(fk) in rt.from_kinds and RefKind(tk) in rt.to_kinds
        )
        out.append(
            RelationTypeInfo(
                type=rt,
                builtin=rt not in loaded.project_types,
                deprecated=rt in loaded.deprecated,
                edges=edges,
            )
        )
    return out


def _kind(value: str | None, label: str) -> RefKind | None:
    if value is None:
        return None
    try:
        return RefKind(value)
    except ValueError:
        allowed = ", ".join(k.value for k in RefKind)
        raise RelationError([f"{label}: {value!r}, допустимо: {allowed}"]) from None


def _kinds(values: list[str], label: str, problems: list[str]) -> frozenset[RefKind]:
    out: set[RefKind] = set()
    for v in values:
        try:
            out.add(RefKind(v))
        except ValueError:
            problems.append(f"{label}: неизвестный вид {v!r}")
    if not values:
        problems.append(f"{label}: нужен хотя бы один вид")
    return frozenset(out)


def _validate(spec: RelationTypeSpec, author: str) -> tuple[RelationType | None, list[str]]:
    problems: list[str] = []
    inverse = spec.inverse or (spec.name if spec.symmetric else "")
    for label, value in (("name", spec.name), ("inverse", inverse)):
        if not _NAME.fullmatch(value):
            problems.append(f"{label}: {value!r} — строчные латинские буквы, цифры и _ (2–48)")
    if not author.strip() or len(author) > _AUTHOR_MAX:
        problems.append(f"author: непустая строка до {_AUTHOR_MAX} символов")
    from_kinds = _kinds(spec.from_kinds, "from_kinds", problems)
    to_kinds = _kinds(spec.to_kinds, "to_kinds", problems)
    if from_kinds - ENTITY_KINDS:
        problems.append("from_kinds: источником может быть только сущность (не code/url)")
    if spec.symmetric:
        if from_kinds != to_kinds or to_kinds - ENTITY_KINDS:
            problems.append("symmetric: from_kinds и to_kinds должны совпадать и быть сущностями")
        if inverse != spec.name:
            problems.append("symmetric: inverse совпадает с name")
        if spec.allow_forward:
            problems.append("symmetric: ссылка вперёд невозможна (концы нормализуются по uid)")
    if problems:
        return None, problems
    return (
        RelationType(
            name=spec.name,
            inverse=inverse,
            from_kinds=from_kinds,
            to_kinds=to_kinds,
            symmetric=spec.symmetric,
            acyclic=spec.acyclic,
            allow_forward=spec.allow_forward,
            description=spec.description,
        ),
        [],
    )


def _builtin_collisions(new: RelationType) -> list[str]:
    """Встроенное нельзя переопределить: любое пересечение токена на любой паре — отказ.

    `Vocabulary.build` отдал бы узкой паре победу над «любой» встроенного типа,
    а проектный тип не вправе тихо менять смысл встроенного.
    """
    probe = Vocabulary.build([new]).entries
    return sorted(
        {
            f"{tok!r} уже занят встроенным типом {BUILTIN_VOCABULARY.entries[key].type.name!r} "
            f"для {key[1].value} → {key[2].value}"
            for key in probe
            if key in BUILTIN_VOCABULARY.entries
            for tok in [key[0]]
        }
    )


def define_type(
    session: Session, *, project_id: int, spec: RelationTypeSpec, author: str
) -> RelationType:
    """Завести проектный тип. Повтор того же определения — no-op.

    TODO(R39-Q1): определение типа агентом должно идти через согласование
    (approval), пока достаточно writer-уровня, который проверяет `emit`.
    """
    new, problems = _validate(spec, author)
    if new is None:
        raise RelationError(problems)
    loaded = load(session, project_id)
    same = next((t for t in loaded.project_types if t.name == new.name), None)
    if same is not None:
        if same == new:
            return same
        raise RelationError([f"тип {new.name!r} уже определён в проекте иначе"])
    if problems := _builtin_collisions(new):
        raise RelationError(
            problems, "Встроенные типы переопределить нельзя — выберите другое имя."
        )
    try:
        Vocabulary.build([*loaded.types, new])
    except VocabularyConflictError as exc:
        raise RelationError([str(exc)], "Имя или обратное имя уже занято другим типом.") from None
    session.add(
        RelationTypeModel(
            project_id=project_id,
            name=new.name,
            inverse=new.inverse,
            from_kinds=sorted(k.value for k in new.from_kinds),
            to_kinds=sorted(k.value for k in new.to_kinds),
            symmetric=new.symmetric,
            acyclic=new.acyclic,
            allow_forward=new.allow_forward,
            description=new.description or None,
        )
    )
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "relation_type.defined",
        author,
        scope_kind="relation_type",
        scope_id=new.name,
        payload={"name": new.name, "inverse": new.inverse},
        summary=f"relation type {new.name} defined",
    )
    return new


def deprecate_type(session: Session, *, project_id: int, name: str, author: str) -> bool:
    """Запретить новые рёбра типа; старые остаются. `False` — уже устарел."""
    if name in {t.name for t in BUILTIN_TYPES}:
        raise RelationError([f"{name!r} — встроенный тип, его нельзя объявить устаревшим"])
    row = session.scalars(
        select(RelationTypeModel).where(
            RelationTypeModel.project_id == project_id, RelationTypeModel.name == name
        )
    ).first()
    if row is None:
        raise RelationError([f"тип {name!r} не определён в проекте"])
    if row.deprecated:
        return False
    row.deprecated = True
    session.flush()
    activity_service.emit_for_write(
        session,
        project_id,
        "relation_type.deprecated",
        author,
        scope_kind="relation_type",
        scope_id=name,
        payload={"name": name},
        summary=f"relation type {name} deprecated",
    )
    return True
