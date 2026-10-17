"""Множества add-wins: рёбра и тег-связи (RFC 29 §3.3, §3.7; CLO-021).

Рёбра — таблицы с ``deterministic_uid`` в реестре (``dependency``,
``*_tag``, ``story_link``, ``adr_task``, ``affected_file``…). Их uid —
``uuid5(EDGE_UID_NAMESPACE, таблица ‖ uid концов ‖ relation)``
(``sync_registry.edge_uid``), поэтому одно ребро, добавленное на двух
репликах, — одна сущность: вторая вставка приходит на существующую строку и
сводится ядром поле за полем (равные поглощаются, остальное — LWW). У
тег-таблиц составной PK без колонки uid; uid вычисляется при захвате той же
формулой, строку применение находит по концам ребра.

Диспетчер ``merge`` зовёт ``resolve_entity`` в случае ``edge``: удаление
ребра с одной стороны против его же записи с другой. Правило §3.7 —
**add-wins**: ребро остаётся.

**Механика одна в обе стороны.** Реплика, которая видит столкновение,
утверждает ребро заново: убирает из хвоста свои операции над ним и пишет
одну вставку всех уезжающих полей со свежим штампом HLC. Вставка (пустой
``base``) нужна потому, что на другой реплике ребра уже нет, а правку
несуществующей строки применение отвергает (CLO-029); свежий штамп больше
любого увиденного, поэтому значения выровняются на всех репликах.

- Чужое удаление против своей записи ребра: удаление не применяется, ребро
  уезжает вставкой текущей строки.
- Чужая запись против своего удаления: строка восстанавливается из
  ``base`` своего удаления (строка целиком на момент удаления) поверх
  чужих полей и уезжает вставкой.

Удаление с обеих сторон сходится само (удаление идемпотентно). Если своё
удаление задело конец ребра (задачу, документ), это уже удаление против
ссылки — стратегия ``delete_edit`` (CLO-022), туда случай и уходит.
"""

from __future__ import annotations

import dataclasses
import json
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import delete

from cod_doc.infra import sync_capture as cap
from cod_doc.infra.models import ProjectModel, SyncOpModel
from cod_doc.services.sync_service import _local, apply, merge

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sqlalchemy.orm import Session

    from cod_doc.services.sync_service.changeset import FieldValue, Op

__all__ = ["resolve_entity"]

_DELETE: Final = "delete"


def _project(session: Session, project_id: int) -> _local.ProjectRef:
    proj = session.get_one(ProjectModel, project_id)
    return _local.ProjectRef(row_id=proj.row_id, uid=str(proj.uid), slug=proj.slug)


def _drop_pending(session: Session, ctx: merge.Context, op: Op) -> None:
    """Убрать из хвоста все свои операции над ребром."""
    session.execute(
        delete(SyncOpModel).where(
            SyncOpModel.project_id == ctx.project_id,
            SyncOpModel.entity_kind == op.entity_kind,
            SyncOpModel.entity_uid == op.entity_uid,
            SyncOpModel.changeset.is_(None),
        )
    )
    session.flush()


def _current(
    session: Session, project: _local.ProjectRef, op: Op, key: Mapping[str, object]
) -> dict[str, FieldValue] | None:
    """Уезжающие поля строки ребра на этой реплике (форма журнала); нет строки — ``None``."""
    mapper, info = apply._info(op.entity_kind)
    obj = apply._find(session, project, mapper, info, op.entity_uid, key)
    if obj is None:
        return None
    fields = cap.shipped_fields(session, op.entity_kind, apply._row_of(info, obj))
    if fields is None:
        return None
    # Форма журнала, как у хука захвата: время — строка ISO.
    return cast(
        "dict[str, FieldValue]",
        json.loads(json.dumps(fields, default=cap._json_default, ensure_ascii=False)),
    )


def _reassert(session: Session, ctx: merge.Context, op: Op, fields: dict[str, FieldValue]) -> None:
    _drop_pending(session, ctx, op)
    merge._append(session, ctx, op, fields, {})


def resolve_entity(session: Session, ctx: merge.Context, clash: merge.EntityClash) -> bool:
    """Стратегия ``edge`` для диспетчера ``merge``: ребро остаётся (add-wins)."""
    theirs = clash.theirs
    same = [o for o in clash.ours if o.entity_uid == clash.entity_uid]
    if len(same) != len(clash.ours):
        # Своё удаление задело конец ребра: удаление против ссылки (CLO-022).
        plugin = cast("merge._EntityPlugin", merge._strategy_module(merge.DELETE_EDIT))
        return plugin.resolve_entity(
            session, ctx, dataclasses.replace(clash, kind=merge.DELETE_EDIT)
        )
    project = _project(session, ctx.project_id)
    if theirs.kind == _DELETE:
        fields = _current(session, project, theirs, theirs.base)
        if fields is None:
            return True  # своей строки уже нет: удалять нечего
        _reassert(session, ctx, theirs, fields)
        return False
    deleted = max((o for o in same if o.kind == _DELETE), key=lambda o: (o.hlc, o.op_id))
    fields = {**cast("dict[str, FieldValue]", dict(deleted.base)), **theirs.fields}
    _drop_pending(session, ctx, theirs)
    # Хвоста над ребром больше нет, и вставка проходит обычным путём применения.
    insert = dataclasses.replace(theirs, fields=fields, base={})
    apply._apply_op(session, project, insert, apply.Touched(), ctx)
    restored = _current(session, project, theirs, fields)
    if restored is not None:
        merge._append(session, ctx, theirs, restored, {})
    return False
