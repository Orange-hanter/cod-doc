"""Сверка партиции находок: закрыть вылеченное, вернуть рецидив.

До этого в `finding` не было ни того, ни другого: наружу торчали только
`dismiss` и `promote`, а `ingest_findings` на конфликте поднимал `times_seen`
и не трогал статус. Значит вылеченная и снова появившаяся находка не вернулась
бы в очередь никогда — `curator_next` смотрит только `status="open"`.

Тесты живут отдельно от потребителя (`doc_node_health`): механика
переиспользуемая, и ломаться она должна своим именем.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, FindingModel, ProjectModel
from cod_doc.services import finding_service
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

_SOURCE = "routine"
_REF = "doc_node_health"
_OTHER_REF = "doc_node_health_ai"


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _ingest(session: Session, project_id: int, fps: list[str], *, ref: str = _REF) -> None:
    finding_service.ingest_findings(
        session,
        project_id=project_id,
        source_run_id=f"run-{'-'.join(fps) or 'empty'}",
        seeds=[
            FindingSeed(
                fingerprint=fp,
                source=_SOURCE,
                source_ref=ref,
                title=f"gap {fp}",
                severity="minor",
            )
            for fp in fps
        ],
    )
    session.flush()


def _reconcile(
    session: Session,
    project_id: int,
    fps: set[str],
    *,
    ref: str = _REF,
    close_after_misses: int = 1,
    unjudged: set[str] | None = None,
) -> dict:
    return finding_service.reconcile_partition(
        session,
        project_id=project_id,
        source=_SOURCE,
        source_ref=ref,
        seen_fingerprints=fps,
        author="routine:doc_node_health",
        close_after_misses=close_after_misses,
        unjudged_fingerprints=unjudged,
    )


def _streak(session: Session, fp: str) -> int:
    f = session.execute(select(FindingModel).where(FindingModel.fingerprint == fp)).scalar_one()
    return f.miss_streak


def _events(session: Session, kind: str) -> int:
    session.flush()
    return len(
        session.execute(select(ActivityEventModel).where(ActivityEventModel.kind == kind))
        .scalars()
        .all()
    )


def _status(session: Session, fp: str) -> str:
    f = session.execute(select(FindingModel).where(FindingModel.fingerprint == fp)).scalar_one()
    return f.status


def test_healed_finding_is_resolved(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, {"a"})

        assert result == {"resolved": 1, "reopened": 0, "missed": 0, "skipped": 0}
        assert _status(session, "a") == "open"
        assert _status(session, "b") == "resolved"


def test_recurrence_reopens_a_resolved_finding(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Раздел наполнили, потом он снова просел — находка обязана вернуться.

    Без переоткрытия она осталась бы `resolved` навсегда: `ingest_findings`
    поднимает только `times_seen`, а очередь куратора фильтрует по `open`.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, set())
        assert _status(session, "a") == "resolved"

        _ingest(session, pid, ["a"])
        result = _reconcile(session, pid, {"a"})

        assert result == {"resolved": 0, "reopened": 1, "missed": 0, "skipped": 0}
        assert _status(session, "a") == "open"


def test_dismissed_survives_both_directions(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Решение человека автоматика не отменяет — ни закрытием, ни возвратом."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        uid = session.execute(
            select(FindingModel.finding_uid).where(FindingModel.fingerprint == "a")
        ).scalar_one()
        finding_service.dismiss_finding(
            session, project_id=pid, finding_uid=uid, author="human:test"
        )

        assert _reconcile(session, pid, set()) == {
            "resolved": 0,
            "reopened": 0,
            "missed": 0,
            "skipped": 0,
        }
        assert _status(session, "a") == "dismissed"

        assert _reconcile(session, pid, {"a"}) == {
            "resolved": 0,
            "reopened": 0,
            "missed": 0,
            "skipped": 0,
        }
        assert _status(session, "a") == "dismissed"


def test_other_partition_is_untouched(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Упавший LLM-проход не вправе закрывать детерминированные находки."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"], ref=_REF)
        _ingest(session, pid, ["b"], ref=_OTHER_REF)

        _reconcile(session, pid, set(), ref=_OTHER_REF)

        assert _status(session, "a") == "open", "чужая партиция не тронута"
        assert _status(session, "b") == "resolved"


def test_reconcile_is_idempotent(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])

        first = _reconcile(session, pid, set())
        second = _reconcile(session, pid, set())

        assert first == {"resolved": 1, "reopened": 0, "missed": 0, "skipped": 0}
        assert second == {"resolved": 0, "reopened": 0, "missed": 0, "skipped": 0}, (
            "второй прогон ничего не меняет"
        )


def test_status_change_leaves_an_event(session_factory) -> None:  # type: ignore[no-untyped-def]
    """ADO-040: у находок нет ревизий, поэтому след — событие."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, set())
        _ingest(session, pid, ["a"])
        _reconcile(session, pid, {"a"})
        session.flush()

        kinds = {e.kind for e in session.query(ActivityEventModel).all()}
        assert {"finding.resolved", "finding.reopened"} <= kinds

        actors = {
            e.actor_kind
            for e in session.query(ActivityEventModel).all()
            if e.kind.startswith("finding.")
        }
        assert actors == {"routine"}, "actor_kind выводится из автора, а не проставляется руками"


# ── Гистерезис: закрывать не с первого промаха ──────────────────────────
#
# Вердикт модели субъективен и мигает: замер на живом корпусе дал три прогона
# подряд с наборами {a,b,c} / {a,b} / {a,b}. Без отсрочки каждый такой хвост
# давал бы пару событий resolved/reopened на прогон.


def test_one_miss_keeps_the_finding_open(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Первый промах при N=2 не закрывает и не шумит событием.

    Негативная проверка встроена: поставь N=1 (или убери гистерезис) — упадут
    оба ассерта сразу, и статус, и отсутствие события.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, {"a"}, close_after_misses=2)

        assert result == {"resolved": 0, "reopened": 0, "missed": 1, "skipped": 0}
        assert _status(session, "b") == "open", "находка обязана остаться видимой куратору"
        assert _streak(session, "b") == 1
        assert _events(session, "finding.resolved") == 0


def test_second_consecutive_miss_closes(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Гистерезис — отсрочка, а не запрет: второй промах подряд закрывает."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        _reconcile(session, pid, {"a"}, close_after_misses=2)
        result = _reconcile(session, pid, {"a"}, close_after_misses=2)

        assert result == {"resolved": 1, "reopened": 0, "missed": 0, "skipped": 0}
        assert _status(session, "b") == "resolved"
        assert _streak(session, "b") == 0, "у закрытой находки серия обнуляется"


def test_return_between_misses_resets_the_streak(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Тот самый флап с живого корпуса, в юнит-масштабе.

    Промах → возврат → промах: серия считается заново, поэтому находка не
    закрывается и за всю последовательность нет ни одного события.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        _reconcile(session, pid, {"a"}, close_after_misses=2)
        assert _streak(session, "b") == 1

        _ingest(session, pid, ["a", "b"])
        _reconcile(session, pid, {"a", "b"}, close_after_misses=2)
        assert _streak(session, "b") == 0, "возврат обнуляет серию, а не уменьшает её"

        _reconcile(session, pid, {"a"}, close_after_misses=2)

        assert _status(session, "b") == "open"
        assert _events(session, "finding.resolved") == 0
        assert _events(session, "finding.reopened") == 0


def test_dismissed_does_not_accumulate_misses(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Решение человека автоматика не трогает — в том числе счётчиком."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])
        uid = session.execute(
            select(FindingModel.finding_uid).where(FindingModel.fingerprint == "b")
        ).scalar_one()
        finding_service.dismiss_finding(
            session, project_id=pid, finding_uid=uid, author="human:test"
        )

        _reconcile(session, pid, {"a"}, close_after_misses=2)
        _reconcile(session, pid, {"a"}, close_after_misses=2)

        assert _status(session, "b") == "dismissed"
        assert _streak(session, "b") == 0


def test_close_after_misses_must_be_positive(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Ноль закрывал бы находку, которую производитель только что видел."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a"])

        with pytest.raises(ValueError, match="close_after_misses"):
            _reconcile(session, pid, {"a"}, close_after_misses=0)


# ── Частичная сверка: прогон рассмотрел не всю партицию ────────────────


def test_unjudged_finding_is_left_alone(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Находка, о которой прогон не смог судить, не закрывается и не копит промахи.

    Негативная проверка встроена: убери `unjudged_fingerprints` — и `b`
    закроется как вылеченная, хотя судить о ней было нечем.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, {"a"}, unjudged={"b"})

        assert result == {"resolved": 0, "reopened": 0, "missed": 0, "skipped": 1}
        assert _status(session, "b") == "open"
        assert _streak(session, "b") == 0, "нерассмотренное не производит улику"


def test_the_judged_part_of_the_run_still_works(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Частично полезный прогон не пропадает целиком.

    То, о чём судить удалось, сверяется как обычно — иначе неполный ответ
    обесценивал бы и ту часть, которой можно верить.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, set(), unjudged={"b"})

        assert result["resolved"] == 1, "рассмотренный и невиденный — вылечен"
        assert _status(session, "a") == "resolved"
        assert _status(session, "b") == "open", "нерассмотренный остался нетронутым"


def test_unknown_finding_is_still_closed(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Находка вне поля зрения прогона закрывается как обычно.

    Это и есть довод за запретный список вместо разрешительного: производитель
    знает, о чём промолчал, но не знает, какие ещё находки лежат в партиции.
    Разрешительный список подвесил бы `c` навсегда.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b", "c"])

        result = _reconcile(session, pid, {"a"}, unjudged={"b"})

        assert _status(session, "c") == "resolved", "предмет вердикта исчез — закрывать законно"
        assert _status(session, "b") == "open"
        assert result["skipped"] == 1


def test_none_means_the_whole_partition(session_factory) -> None:  # type: ignore[no-untyped-def]
    """Дефолт не изменился: производитель отвечает за всю партицию."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        _ingest(session, pid, ["a", "b"])

        result = _reconcile(session, pid, {"a"})

        assert result["skipped"] == 0
        assert _status(session, "b") == "resolved"
