"""ADO-154/ADO-106: рантайм-инвариант — страница не отдаёт сырой datetime.

Это не HTML-снапшот (те отвергнуты в капабилити §8 как шумящие на каждой
косметической правке), а один инвариант: в отрендеренной странице не должно
быть ни ``str(datetime)`` с микросекундами (``2026-09-08 03:21:28.897000``),
ни ``repr`` datetime, ни ISO-``T`` в видимом тексте.

Отличие от ``tests/api/test_web_template_dates.py``: тот линтует исходники
шаблонов и знает только те поля, которые перечислены в его регексе. Этот —
слеп к разметке и ловит ту же ошибку на любом поле и на любой странице,
включая ещё не написанные: достаточно добавить её URL в ``PAGES``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from cod_doc.config import Config, ProjectEntry
from cod_doc.core.project import Project
from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.domain.entities import (
    Project as ProjectEntity,
)
from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service as tasks

if TYPE_CHECKING:
    from pathlib import Path

#: ``str(datetime)`` с микросекундами — ровно то, что печаталось до ADO-154.
_MICROSECONDS_RE = re.compile(r"\d{2}:\d{2}:\d{2}\.\d{6}")
#: ``repr`` объекта — признак того, что в шаблон уехал не тот тип.
_DATETIME_REPR_RE = re.compile(r"datetime\.(?:datetime|date)\(")
#: ISO-разделитель в ВИДИМОМ тексте (script/pre/code вырезаются отдельно).
_ISO_T_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")

_NON_PROSE_RE = re.compile(
    r"<(script|pre|code|textarea)\b.*?</\1>|<input\b[^>]*>|<[^>]+>",
    re.DOTALL | re.IGNORECASE,
)


def _visible_text(html: str) -> str:
    """Текст без разметки, скриптов, ``<pre>``/``<code>`` и значений форм.

    Внутри ``<input type="date">`` ISO — правильный формат (ADR «Decided at»),
    в ``<script>`` и ``<pre>`` живут JSON и mermaid. Инвариант касается только
    того, что человек читает глазами.
    """
    return _NON_PROSE_RE.sub(" ", html)


@pytest.fixture
def seeded_client(tmp_path: Path, migrate_db):  # type: ignore[no-untyped-def]
    """Проект с задачей, у которой есть история: create → in_progress → done."""
    repo = tmp_path / "dt-demo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    migrate_db(db_path)

    entry = ProjectEntry(name="demo", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)
    Project(entry).init()

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    task_id = ""
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="dt-plan", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Section A",
                slug="section-a",
                position=0,
            )
        )
        session.flush()

        t = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Задача с историей",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="DTG",
        )
        task_id = t.task_id
        tasks.update_status(
            session,
            task_id=task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
            reason="started",
            via_checkout=True,
        )
        tasks.complete(session, task_id=task_id, author="human:dakh")
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry.name, task_id


def _pages(slug: str, task_id: str) -> list[str]:
    return [
        "/",
        f"/p/{slug}",
        f"/p/{slug}/revisions",
        f"/p/{slug}/tasks",
        f"/p/{slug}/tasks/{task_id}",
        f"/p/{slug}/docs",
        f"/p/{slug}/plans",
        f"/p/{slug}/adr",
        f"/p/{slug}/commits",
        f"/p/{slug}/routines",
        f"/p/{slug}/stories",
        f"/p/{slug}/metrics",
        f"/p/{slug}/costs",
        f"/p/{slug}/code-refs",
        f"/p/{slug}/scenarios",
    ]


def test_no_page_renders_a_raw_datetime_repr(seeded_client) -> None:  # type: ignore[no-untyped-def]
    client, slug, task_id = seeded_client
    offenders: list[str] = []

    for url in _pages(slug, task_id):
        r = client.get(url)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        for label, pattern in (
            ("микросекунды", _MICROSECONDS_RE),
            ("repr datetime", _DATETIME_REPR_RE),
        ):
            found = pattern.search(r.text)
            if found:
                offenders.append(f"{url}: {label} — {found.group(0)!r}")

    assert not offenders, (
        "Страница печатает сырой datetime вместо фильтра из "
        "cod_doc/api/web/dates.py:\n  " + "\n  ".join(offenders)
    )


def test_no_page_shows_iso_t_in_visible_text(seeded_client) -> None:  # type: ignore[no-untyped-def]
    client, slug, task_id = seeded_client
    offenders: list[str] = []

    for url in _pages(slug, task_id):
        r = client.get(url)
        found = _ISO_T_RE.search(_visible_text(r.text))
        if found:
            offenders.append(f"{url}: {found.group(0)!r}")

    assert not offenders, (
        "ISO-разделитель `T` в видимом тексте — дата не прошла через фильтр:\n  "
        + "\n  ".join(offenders)
    )


def test_the_seed_actually_renders_dates(seeded_client) -> None:  # type: ignore[no-untyped-def]
    """Инвариант выше был бы вечнозелёным, если бы страницы печатали одни прочерки."""
    client, slug, _task_id = seeded_client
    text = client.get(f"/p/{slug}/revisions").text
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}<", text), (
        "на странице ревизий нет ни одной отформатированной даты — "
        "сид не наполняет историю, и охранный тест ничего не проверяет"
    )


def test_the_guard_would_catch_the_original_bug() -> None:
    """Страховка: инвариант обязан ловить именно тот вывод, ради которого написан."""
    broken = "<td>2026-09-08 03:21:28.897000</td>"
    assert _MICROSECONDS_RE.search(broken)
    assert _ISO_T_RE.search(_visible_text("<span>2026-09-08T03:21</span>"))
    assert not _ISO_T_RE.search(_visible_text('<input type="date" value="2026-09-08T03:21">'))
