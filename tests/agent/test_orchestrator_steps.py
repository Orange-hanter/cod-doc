"""ADO-115: шаги прогона переживают процесс.

Живая лента консоли шла по WebSocket и на диск не попадала вообще, поэтому
`/p/<slug>/run/<id>` находил ноль строк и рисовал «run упал до первого
write-tool». Замерено до правки: 20 прогонов в `agent_run`, ни одного шага
ни у одного.

Проверяется не оркестратор целиком (для этого нужен живой LLM-адаптер), а
писатель: `run_context.record_step` под открытым прогоном. Контракт у него
двойной, и обе половины здесь:

* `activity_event` — контракт, туда идёт ОБРЕЗАННОЕ тело;
* `<project>/.cod-doc/runs/<run_id>.jsonl` — дополнение, туда идёт ПОЛНОЕ.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import Project as ProjectEntity
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import activity_service, run_context
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from collections.abc import Iterator

SLUG = "stepped"


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Проект на диске с мигрированной БД и строкой в реестре."""
    root = tmp_path / "repo"
    (root / ".cod-doc").mkdir(parents=True)
    db_path = root / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = make_engine(f"sqlite:///{db_path}")
    try:
        with transactional(make_session_factory(engine)) as session:
            now = datetime.now(UTC)
            proj = ProjectRepository(session).add(
                ProjectEntity(slug=SLUG, title=SLUG, root_path=str(root), config={})
            )
            proj.created = now
            proj.updated = now
    finally:
        engine.dispose()

    from cod_doc.config import Config, ProjectEntry

    entry = ProjectEntry(name=SLUG, path=str(root))
    monkeypatch.setattr(Config, "list_projects", lambda self: [entry])
    yield root


def _events(root: Path, run_id: str) -> list[dict]:  # type: ignore[type-arg]
    engine = make_engine(f"sqlite:///{root / '.cod-doc' / 'state.db'}")
    try:
        with transactional(make_session_factory(engine)) as session:
            proj = ProjectRepository(session).get_by_slug(SLUG)
            assert proj is not None and proj.row_id is not None
            return activity_service.events_for_run(session, proj.row_id, run_id, limit=1000)
    finally:
        engine.dispose()


def _sidecar(root: Path, run_id: str) -> list[dict]:  # type: ignore[type-arg]
    path = root / ".cod-doc" / "runs" / f"{run_id}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _run(root: Path, run_id: str, steps: list[tuple[str, object]]) -> None:
    handle = run_context.start_orchestrator_run(project_path=str(root), run_id=run_id)
    try:
        for kind, data in steps:
            run_context.record_step(kind, data)
    finally:
        run_context.finalize_orchestrator_run(handle, project_path=str(root), run_id=run_id)


# ── acceptance: «emit шагов в run_scope → events_for_run непустой» ─────


def test_steps_land_in_activity_event_in_order(project: Path) -> None:
    _run(
        project,
        "01JTESTRUN0000000000000001",
        [
            ("agent.thinking", {"data": "думаю"}),
            ("agent.tool_call", {"data": "task_get"}),
            ("agent.tool_result", {"data": "ok"}),
        ],
    )

    events = _events(project, "01JTESTRUN0000000000000001")
    assert [e["kind"] for e in events] == [
        "agent.thinking",
        "agent.tool_call",
        "agent.tool_result",
    ]
    assert all(e["run_id"] == "01JTESTRUN0000000000000001" for e in events)
    # Роль выводится ТОЛЬКО через actor_kind_for_author (ADR-012).
    assert {e["actor_kind"] for e in events} == {"orchestrator"}


def test_steps_are_scoped_to_run_not_task(project: Path) -> None:
    """scope_kind='run', а не 'task'.

    `agent_service` тянет `list_events(scope_kind="task", …)` в каждый payload
    `agent_pick`/`agent_get`. Со скоупом на задачу контекст агента забился бы
    его же собственным thinking вместо человеческих мутаций.
    """
    _run(project, "01JTESTRUN0000000000000002", [("agent.thinking", {"data": "x"})])
    (event,) = _events(project, "01JTESTRUN0000000000000002")
    assert event["scope_kind"] == "run"
    assert event["scope_id"] == "01JTESTRUN0000000000000002"


# ── обрезка в БД + полный текст сбоку ─────────────────────────────────


def test_long_body_is_truncated_in_db_but_whole_in_sidecar(project: Path) -> None:
    body = "щ" * 100_000
    _run(project, "01JTESTRUN0000000000000003", [("agent.tool_result", {"data": body})])

    (event,) = _events(project, "01JTESTRUN0000000000000003")
    assert len(event["payload"]["data"]) == run_context.MAX_STEP_CHARS
    assert event["payload"]["data_truncated"] is True
    assert event["payload"]["data_orig_len"] == 100_000
    assert event["payload"]["full"] is True

    (row,) = _sidecar(project, "01JTESTRUN0000000000000003")
    assert row["data"]["data"] == body, "sidecar обязан хранить полное тело"
    assert row["i"] == event["payload"]["step"]


def test_short_body_is_not_marked_truncated(project: Path) -> None:
    _run(project, "01JTESTRUN0000000000000004", [("agent.thinking", {"data": "коротко"})])
    (event,) = _events(project, "01JTESTRUN0000000000000004")
    assert event["payload"]["data"] == "коротко"
    assert "data_truncated" not in event["payload"]


def test_step_cap_applies_to_db_only(project: Path) -> None:
    """В БД — потолок, в sidecar — всё: это и есть обещанная возможность восстановить."""
    over = run_context.MAX_STEPS_PER_RUN + 100
    _run(
        project,
        "01JTESTRUN0000000000000005",
        [("agent.thinking", {"data": str(i)}) for i in range(over)],
    )

    events = _events(project, "01JTESTRUN0000000000000005")
    assert len(events) == run_context.MAX_STEPS_PER_RUN
    assert len(_sidecar(project, "01JTESTRUN0000000000000005")) == over


def test_terminal_step_is_written_past_the_cap(project: Path) -> None:
    """Иначе у длинного прогона в реплее не будет конца."""
    over = run_context.MAX_STEPS_PER_RUN + 10
    steps: list[tuple[str, object]] = [("agent.thinking", {"data": str(i)}) for i in range(over)]
    steps.append(("agent.stopped", {"reason": "completed"}))
    _run(project, "01JTESTRUN0000000000000006", steps)

    events = _events(project, "01JTESTRUN0000000000000006")
    assert events[-1]["kind"] == "agent.stopped"
    assert events[-1]["payload"]["capped"] is True


# ── degraded-контракт ─────────────────────────────────────────────────


def test_record_step_outside_a_run_is_a_noop() -> None:
    """Вне прогона sink пуст — вызов не должен ни писать, ни падать."""
    run_context.record_step("agent.thinking", {"data": "ничей"})


def test_project_without_db_does_not_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ни строк, ни исключения: телеметрия не роняет прогон."""
    from cod_doc.config import Config

    monkeypatch.setattr(Config, "list_projects", lambda self: [])
    root = tmp_path / "bare"
    root.mkdir()
    _run(root, "01JTESTRUN0000000000000007", [("agent.thinking", {"data": "x"})])
    # Sidecar пишется и без БД — он не зависит от реестра.
    assert len(_sidecar(root, "01JTESTRUN0000000000000007")) == 1


def test_sidecar_failure_still_writes_the_event(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Событие в БД — контракт; `full: false` означает «полного тела нет»."""
    monkeypatch.setattr(run_context, "_append_sidecar", lambda *a, **kw: False)
    _run(project, "01JTESTRUN0000000000000008", [("agent.thinking", {"data": "x"})])
    (event,) = _events(project, "01JTESTRUN0000000000000008")
    assert event["payload"]["full"] is False


# ── run_id как имя файла ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "a/b", "..", ".", "", "x" * 200, "with space", ".hidden"],
)
def test_validate_run_id_rejects_path_traversal(bad: str) -> None:
    """`run_id` приходит в том числе из URL и становится ИМЕНЕМ ФАЙЛА."""
    with pytest.raises(ValueError, match="run_id"):
        run_context.validate_run_id(bad)


def test_validate_run_id_accepts_a_ulid() -> None:
    run_context.validate_run_id("01JTESTRUN0000000000000001")


def test_unsafe_run_id_disables_sidecar_but_not_the_event(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Небезопасный run_id не должен ни писать файл, ни ронять прогон."""
    handle = run_context.start_orchestrator_run(project_path=str(project), run_id="../evil")
    try:
        run_context.record_step("agent.thinking", {"data": "x"})
    finally:
        run_context.finalize_orchestrator_run(handle, project_path=str(project), run_id="../evil")
    assert not (project.parent / "evil.jsonl").exists()
    assert not (project / ".cod-doc" / "runs").exists()


# ── проводка в оркестраторе ───────────────────────────────────────────


def test_orchestrator_records_every_kind_it_publishes() -> None:
    """Каждый `event_bus.publish` в `run_task` обязан иметь парный `record_step`.

    Анти-дрейф по исходнику, а не по поведению: `run_task` — async-генератор
    поверх живого LLM-адаптера, и поднимать его ради проверки проводки
    дороже, чем сама проверка. Без этого теста вызовы `record_step` можно
    выкинуть, а все поведенческие тесты выше останутся зелёными — они зовут
    писателя напрямую.

    Ровно так ADO-115 и появился: шаги публиковались в шину и никуда больше,
    и ни один тест этого не заметил.
    """
    source = (
        Path(__file__).resolve().parents[2] / "cod_doc" / "agent" / "orchestrator.py"
    ).read_text(encoding="utf-8")

    published = source.count("event_bus.publish(")
    recorded = source.count("run_context.record_step(")
    assert recorded >= 4, f"ожидалось не меньше четырёх record_step, найдено {recorded}"
    assert recorded >= published - 1, (
        f"публикаций в шину {published}, записей на диск {recorded} — "
        "шаг публикуется, но не переживает процесс (это и есть ADO-115)"
    )

    for marker in ("agent.started", "agent.stopped"):
        assert f'record_step("{marker}"' in source, (
            f"{marker} не пишется на диск — у воспроизведённого прогона не будет начала или конца"
        )
    assert 'record_step(f"agent.{event.type}"' in source, "шаги цикла не пишутся на диск"
