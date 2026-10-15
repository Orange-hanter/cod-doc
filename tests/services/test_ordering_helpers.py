"""ORM-сторона ранга (CLO-004): ``conform`` и страховка ``before_flush``."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING

from cod_doc.infra import ordering
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import OpenQuestionModel, ProjectModel, QuestionOptionModel

if TYPE_CHECKING:
    from sqlalchemy import Engine


def _rows(*ranks: str | None) -> list[SimpleNamespace]:
    return [SimpleNamespace(rank=r) for r in ranks]


def test_conform_keeps_longest_increasing_chain() -> None:
    rows = _rows("b", "c", "a", "d", "e")  # «a» выпала из порядка
    assert ordering.conform(rows) == 1  # type: ignore[arg-type]
    ranks = [r.rank for r in rows]
    assert ranks == sorted(ranks)
    assert ranks[:2] == ["b", "c"] and ranks[3:] == ["d", "e"]


def test_conform_on_ordered_list_writes_nothing() -> None:
    rows = _rows("a", "b", "c")
    assert ordering.conform(rows) == 0  # type: ignore[arg-type]


def test_rank_between_tolerates_tie() -> None:
    """Равные ранги соседей — след слияния двух реплик; вставка встаёт в ничью."""
    assert ordering.rank_between("m", "m") == "m"


def test_new_row_without_rank_gets_one_by_position(engine_with_schema: Engine) -> None:
    """Путь, который ранг не ставит (фикстура, импорт), не оставляет NULL."""
    from datetime import UTC, datetime

    factory = make_session_factory(engine_with_schema)
    now = datetime.now(UTC)
    with transactional(factory) as session:
        proj = ProjectModel(slug="o", title="P", root_path="/tmp/o", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        question = OpenQuestionModel(
            project_id=proj.row_id, question_id="Q-001", title="q", question="?",
            status="open", priority="medium", author="human:t", created=now, last_updated=now,
        )  # fmt: skip
        # FK опций ещё не заполнен — родитель находится через отношение.
        for position in (2, 0, 1):
            question.options.append(
                QuestionOptionModel(position=position, title=str(position), chosen=False)
            )
        session.add(question)
        session.flush()
        rows = ordering.siblings(session, QuestionOptionModel, question_row_id=question.row_id)
        assert [r.position for r in rows] == [0, 1, 2]
        assert all(r.rank for r in rows)
