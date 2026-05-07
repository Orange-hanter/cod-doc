"""TraceCall repository (COD-063)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import TraceCall
from cod_doc.infra.models import TraceCallModel

from .base import BaseRepository

if TYPE_CHECKING:
    from collections.abc import Sequence


class TraceCallRepository(BaseRepository[TraceCall, TraceCallModel]):
    model_cls = TraceCallModel

    def _to_domain(self, model: TraceCallModel) -> TraceCall:
        tool_calls: list[dict[str, Any]] | None = None
        if model.tool_calls:
            try:
                tool_calls = json.loads(model.tool_calls)
            except (json.JSONDecodeError, TypeError):
                tool_calls = None
        return TraceCall(
            row_id=model.row_id,
            task_id=model.task_id,
            model=model.model,
            kind=model.kind,
            input_tokens=model.input_tokens,
            output_tokens=model.output_tokens,
            duration_ms=model.duration_ms,
            tool_calls=tool_calls,
            error=model.error,
            ts=model.ts,
        )

    def _to_model(self, entity: TraceCall) -> TraceCallModel:
        return TraceCallModel(
            task_id=entity.task_id,
            model=entity.model,
            kind=entity.kind,
            input_tokens=entity.input_tokens,
            output_tokens=entity.output_tokens,
            duration_ms=entity.duration_ms,
            tool_calls=json.dumps(entity.tool_calls) if entity.tool_calls else None,
            error=entity.error,
        )

    def list_for_task(self, task_id: int) -> Sequence[TraceCall]:
        """Return traces newest-first for a single task."""
        rows = self.session.execute(
            select(TraceCallModel)
            .where(TraceCallModel.task_id == task_id)
            .order_by(TraceCallModel.ts.desc(), TraceCallModel.row_id.desc())
        ).scalars()
        return [self._to_domain(m) for m in rows]
