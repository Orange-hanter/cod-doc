"""Task detail page (`GET /p/{slug}/tasks/{task_id}`).

Verifies:
- Header renders title + status/priority/type badges + plan link.
- Description and acceptance render through markdown (bold, inline code, lists).
- Forward / reverse chains list dependencies (via PlanService).
- Revision history shows latest revisions newest-first.
- Quick-actions form lets the user change status / mark done.
- Cross-project guard: 404 when the task belongs to another project.
- Empty-DB / unknown-project / unknown-task edge cases.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from cod_doc.api.web.peek import full_page_url
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
from cod_doc.infra.models import DependencyModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service as tasks
from tests._task_scope import sole_project_id

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def task_detail_client(tmp_path: Path, migrate_db):
    repo = tmp_path / "td-demo"
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
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectRepository(session).add(
            ProjectEntity(slug="demo", title="Demo", root_path=str(repo), config={})
        )
        proj.created = now
        proj.updated = now
        session.flush()

        plan = PlanRepository(session).add(
            Plan(project_id=proj.row_id, scope="payments", principle="test-first")
        )
        plan.created = now
        plan.last_updated = now
        session.flush()

        section = PlanSectionRepository(session).add(
            PlanSection(
                plan_id=plan.row_id,
                letter="A",
                title="Schema",
                slug="schema",
                position=0,
            )
        )
        session.flush()

        # Three tasks: t1 blocks t2 blocks t3 (chain).
        t1 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Set up DB schema",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="DET",
            description="Implement the **payment_intent** table and idempotency-key index.\n\n- Migration `0008_payments.py`\n- Index on `idempotency_key`",
            acceptance="`alembic upgrade head` applies cleanly\n\n- Index visible in `\\d+ payment_intent`",
        )
        t2 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Wire intent endpoint",
            type=TaskType.FEATURE,
            priority=Priority.CRITICAL,
            author="human:dakh",
            id_prefix="DET",
        )
        t3 = tasks.create(
            session,
            project_id=proj.row_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            title="Stripe webhook handler",
            type=TaskType.FEATURE,
            priority=Priority.HIGH,
            author="human:dakh",
            id_prefix="DET",
        )

        # Insert blocks-deps directly: DET-001 blocks DET-002, DET-002 blocks DET-003.
        session.add(DependencyModel(from_task_id=t2.row_id, to_task_id=t1.row_id, kind="blocks"))
        session.add(DependencyModel(from_task_id=t3.row_id, to_task_id=t2.row_id, kind="blocks"))
        session.flush()
        # Add an extra revision: bump status of t1 (creates one more entry).
        tasks.update_status(
            session,
            task_id=t1.task_id,
            new_status=TaskStatus.IN_PROGRESS,
            author="human:dakh",
            via_checkout=True,
            project_id=sole_project_id(session),
        )
    engine.dispose()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, entry


def test_task_detail_renders_header_and_badges(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    assert r.status_code == 200
    body = r.text
    assert "DET-001" in body
    assert "Set up DB schema" in body
    # Hero: status badge + priority chip + type chip
    assert "badge-lg badge-in_progress" in body  # status badge prominent
    assert "prio-chip prio-high" in body  # priority chip
    assert "type: feature" in body
    # Plan link rendered as a meta-chip
    assert 'class="meta-chip meta-chip-link"' in body
    assert 'href="/p/demo/plans/' in body
    # Priority stripe class on hero (visual accent)
    assert "prio-stripe-high" in body


def test_task_detail_renders_description_markdown(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "Description" in body
    assert "<strong>payment_intent</strong>" in body
    assert "<code>idempotency_key</code>" in body
    assert "<li>Migration <code>0008_payments.py</code></li>" in body


def test_task_detail_renders_acceptance(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "Acceptance criteria" in body
    assert "<code>alembic upgrade head</code>" in body


def test_task_detail_lists_blocked_by_chain(task_detail_client) -> None:
    """DET-002 must complete after DET-001 — forward chain shows DET-001."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-002")
    body = r.text
    assert "Blocked by" in body
    assert 'href="/p/demo/tasks/DET-001"' in body


def test_task_detail_lists_unblocks_chain(task_detail_client) -> None:
    """DET-001 unblocks DET-002 (and transitively DET-003)."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "Unblocks" in body
    assert 'href="/p/demo/tasks/DET-002"' in body
    # Reverse chain follows transitively → DET-003 also visible
    assert 'href="/p/demo/tasks/DET-003"' in body


def test_task_detail_shows_revision_history(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    # 2 revisions: create + status update — count rendered in <span class="count-chip">
    assert "Revision history" in body
    assert '<span class="count-chip">2</span>' in body
    assert "human:dakh" in body


def test_task_detail_quick_actions_present(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    # Status quick-set form (HTMX-wired)
    assert 'hx-post="/p/demo/tasks/DET-001/status"' in body
    # Mark done button (hidden when status=done; here status=in-progress)
    assert "Mark done" in body


def _force_raw_status(entry: ProjectEntry, task_id: str, status: str) -> None:
    """Пишем колонку напрямую, минуя сервис.

    Легаси-написание в БД — это не мигрированный проект или восстановленный из
    `~/.cod-doc/backups` бэкап, а не результат вызова write-пути; и наоборот,
    канонический статус здесь не должен зависеть от того, что именно write-путь
    сейчас кладёт в колонку.
    """
    with sqlite3.connect(f"{entry.path}/.cod-doc/state.db") as conn:
        conn.execute("UPDATE task SET status = ? WHERE task_id = ?", (status, task_id))


def test_task_detail_status_select_offers_only_canonical(task_detail_client) -> None:
    """ADO-156: форма не предлагает легаси-написание — им человек вернул бы дрейф."""
    client, entry = task_detail_client
    _force_raw_status(entry, "DET-002", "todo")

    body = client.get(f"/p/{entry.name}/tasks/DET-002").text
    for canonical in ("backlog", "todo", "in_progress", "in_review", "blocked", "done"):
        assert f'<option value="{canonical}"' in body
    assert '<option value="pending"' not in body
    assert '<option value="in-progress"' not in body


def test_task_detail_status_select_keeps_legacy_current_value(task_detail_client) -> None:
    """Задача, уже лежащая в легаси-написании, не теряет выбранный пункт."""
    client, entry = task_detail_client
    _force_raw_status(entry, "DET-002", "pending")

    body = client.get(f"/p/{entry.name}/tasks/DET-002").text
    assert '<option value="pending" selected>pending (legacy)</option>' in body
    # Канон при этом никуда не делся — переключиться есть на что.
    assert '<option value="todo"' in body


def test_task_detail_complete_button_hidden_when_done(task_detail_client) -> None:
    """Marking complete should hide the 'Mark done' button.

    DET-001 is the only task without blockers in the seed; mark it done and
    inspect its detail page.
    """
    client, entry = task_detail_client
    client.post(
        f"/p/{entry.name}/tasks/DET-001/complete",
        headers={"HX-Request": "true"},
    )
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    assert r.status_code == 200
    body = r.text
    assert "Mark done" not in body, "complete button should be hidden when status=done"


def test_task_detail_404_unknown(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/UNKNOWN-999")
    assert r.status_code == 404


def test_task_detail_db_absent_404(tmp_path: Path) -> None:
    repo = tmp_path / "no-db"
    repo.mkdir()
    entry = ProjectEntry(name="bare", path=str(repo))
    cfg = Config(api_key="sk-test", model="test/model", base_url="https://x")
    cfg.add_project(entry)

    import cod_doc.api.deps as deps

    deps.set_config(cfg)

    Project(entry).init()

    from cod_doc.api.server import app

    with TestClient(app, raise_server_exceptions=True) as client:
        r = client.get(f"/p/{entry.name}/tasks/X-001")
    assert r.status_code == 404


def test_tasks_list_links_to_detail(task_detail_client) -> None:
    """Regression: each task_id in the list page must link to the detail page."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks")
    body = r.text
    assert 'href="/p/demo/tasks/DET-001"' in body
    assert 'href="/p/demo/tasks/DET-002"' in body


# ── Inline edit (description / acceptance) ──────────────────────────────


def test_field_edit_form_has_textarea_for_description(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(
        f"/p/{entry.name}/tasks/DET-001/fields/description/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert '<textarea name="body"' in body
    # Pre-fill with current description (markdown source)
    assert "**payment_intent**" in body
    assert "Save" in body
    assert "Cancel" in body


def test_field_view_renders_markdown_card(task_detail_client) -> None:
    """Cancel button hits .../view → returns the read-only card."""
    client, entry = task_detail_client
    r = client.get(
        f"/p/{entry.name}/tasks/DET-001/fields/description/view",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert 'data-field="description"' in body
    assert "<strong>payment_intent</strong>" in body  # rendered markdown
    assert "✎ Edit" in body  # edit affordance back


def test_field_patch_writes_description_and_swaps_view(task_detail_client) -> None:
    client, entry = task_detail_client
    new_md = "Updated description.\n\n- bullet **bold**\n- second"
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description",
        data={"body": new_md},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    # Returns the view fragment with rendered new content
    assert 'data-field="description"' in body
    assert "<strong>bold</strong>" in body
    assert "<li>second</li>" in body
    # Persistence: full page reload still shows the new content
    r2 = client.get(f"/p/{entry.name}/tasks/DET-001")
    assert "Updated description" in r2.text
    assert "<li>second</li>" in r2.text


def test_field_patch_works_for_acceptance(task_detail_client) -> None:
    client, entry = task_detail_client
    new_md = "Acceptance v2:\n\n- `pytest -q` zero failures"
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/acceptance",
        data={"body": new_md},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert 'data-field="acceptance"' in body
    assert "<code>pytest -q</code>" in body


def test_field_patch_form_post_redirects(task_detail_client) -> None:
    """Non-HTMX form post → 303 back to the task page."""
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description",
        data={"body": "plain new text"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"/p/{entry.name}/tasks/DET-001"


def test_field_edit_unknown_field_404(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(
        f"/p/{entry.name}/tasks/DET-001/fields/garbage/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_field_edit_unknown_task_404(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(
        f"/p/{entry.name}/tasks/UNKNOWN-999/fields/description/edit",
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_empty_description_shows_edit_hint_with_button(task_detail_client) -> None:
    """Tasks without description render an editable empty card, not a wall of dashes."""
    client, entry = task_detail_client
    # DET-002 was seeded without description
    r = client.get(f"/p/{entry.name}/tasks/DET-002")
    body = r.text
    assert "не задано" in body
    # Edit button is present even on empty cards
    assert "✎ Edit" in body
    # Hint mentions markdown
    assert "markdown" in body.lower()


# ── COD-067: AI improve-text flow ──────────────────────────────────────────


def test_field_edit_form_has_improve_button_and_intent_input(task_detail_client) -> None:
    """The edit fragment exposes an Improve via AI button and intent input."""
    client, entry = task_detail_client
    r = client.get(
        f"/p/{entry.name}/tasks/DET-001/fields/description/edit",
        headers={"HX-Request": "true"},
    )
    body = r.text
    assert "Improve via AI" in body
    assert 'name="intent"' in body
    assert "/fields/description/improve" in body


def test_field_improve_swaps_textarea_with_suggestion(task_detail_client, monkeypatch) -> None:
    """The improve endpoint returns the edit fragment with the AI suggestion in textarea."""
    client, entry = task_detail_client
    from cod_doc.services import ai_text

    monkeypatch.setattr(
        ai_text,
        "improve_text_traced",
        lambda text, intent, *, cfg: ai_text.ImproveResult(
            text=f"AI-improved ({intent or 'default'}):\n{text.strip()}",
            model=cfg.model,
            input_tokens=10,
            output_tokens=20,
            duration_ms=100,
        ),
    )
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description/improve",
        data={"body": "Quick draft.", "intent": "make it more formal"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    # Edit fragment with new content in the textarea
    assert 'data-field="description"' in body
    assert "AI-improved (make it more formal)" in body
    assert "AI suggestion ready" in body
    # Intent input keeps the user's prompt
    assert 'value="make it more formal"' in body
    # DB unchanged — full page still shows original text
    r2 = client.get(f"/p/{entry.name}/tasks/DET-001")
    assert "AI-improved" not in r2.text


def test_field_improve_surfaces_backend_error_inline(task_detail_client, monkeypatch) -> None:
    """When the LLM call fails, return the original draft + an inline error notice."""
    client, entry = task_detail_client
    from cod_doc.services import ai_text

    def boom(text: str, intent: str, *, cfg):
        raise ai_text.AIBackendError("network down")

    monkeypatch.setattr(ai_text, "improve_text_traced", boom)

    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description/improve",
        data={"body": "Original draft.", "intent": ""},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    assert "AI error: network down" in body
    # Original draft preserved verbatim
    assert "Original draft." in body


def test_field_improve_records_a_trace_row(task_detail_client, monkeypatch) -> None:
    """A successful improve auto-logs a trace row visible in the AI trace tab."""
    client, entry = task_detail_client
    from cod_doc.services import ai_text

    monkeypatch.setattr(
        ai_text,
        "improve_text_traced",
        lambda text, intent, *, cfg: ai_text.ImproveResult(
            text="suggestion",
            model="anthropic/claude-sonnet-4-6",
            input_tokens=33,
            output_tokens=44,
            duration_ms=250,
        ),
    )
    client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description/improve",
        data={"body": "draft", "intent": "x"},
        headers={"HX-Request": "true"},
    )
    # Reload the task page — the trace section now lists the call.
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "anthropic/claude-sonnet-4-6" in body
    assert "improve_text:description" in body
    assert "33" in body and "44" in body  # in/out tokens


def test_field_improve_unknown_field_404(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/garbage/improve",
        data={"body": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


def test_field_improve_unknown_task_404(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/NO-999/fields/description/improve",
        data={"body": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 404


# ── COD-063: AI trace tab ──────────────────────────────────────────────


def test_task_detail_trace_section_empty(task_detail_client) -> None:
    """Trace section renders with an empty hint when no LLM calls logged."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "AI trace" in body
    assert "Ещё не было вызовов ИИ" in body


def test_task_detail_trace_section_lists_calls(task_detail_client) -> None:
    """A logged trace row shows up with model, tokens, duration, and tool calls."""
    client, entry = task_detail_client
    db_path = entry.cod_doc_dir / "state.db"

    from cod_doc.infra.db import make_engine, make_session_factory, transactional
    from cod_doc.services import task_service as task_svc
    from cod_doc.services import trace_service as trace_svc
    from cod_doc.services.project_lookup import require_project_id

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        task = task_svc.get(session, "DET-001", project_id=require_project_id(session, entry.name))
        assert task is not None and task.row_id is not None
        trace_svc.record(
            session,
            model="anthropic/claude-sonnet-4-6",
            task_id=task.row_id,
            input_tokens=120,
            output_tokens=42,
            duration_ms=850,
            tool_calls=[{"name": "task.list", "args": {"project": "demo"}}],
        )
        trace_svc.record(
            session,
            model="anthropic/claude-haiku-4-5",
            task_id=task.row_id,
            input_tokens=50,
            output_tokens=10,
            duration_ms=120,
            error="rate limited",
        )
    engine.dispose()

    r = client.get(f"/p/{entry.name}/tasks/DET-001")
    body = r.text
    assert "anthropic/claude-sonnet-4-6" in body
    assert "anthropic/claude-haiku-4-5" in body
    # Aggregate footer (sum across the two calls)
    assert "170" in body  # 120 + 50 input tokens
    assert "52" in body  # 42 + 10 output tokens
    # Duration column entry
    assert "850 ms" in body
    # Tool call name surfaces
    assert "task.list" in body
    # Error row gets the error notice
    assert "rate limited" in body
    assert "trace-err" in body


def _patch_field_service(monkeypatch, exc: Exception) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.api.web.fragments import tasks_fields

    def boom(*_a: object, **_kw: object) -> None:
        raise exc

    attr, label, _svc = tasks_fields._TASK_FIELDS["description"]
    monkeypatch.setitem(tasks_fields._TASK_FIELDS, "description", (attr, label, boom))


def test_field_patch_integrity_error_text_stays_server_side(
    task_detail_client, monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """str() от IntegrityError несёт SQL и параметры — в ответ идёт общий текст."""
    from sqlalchemy.exc import IntegrityError

    _patch_field_service(
        monkeypatch,
        IntegrityError("UPDATE secret_table SET x=?", {"p": "secret-param"}, Exception("x")),
    )
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description",
        data={"body": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code >= 400
    assert "Не удалось сохранить изменения." in r.text
    assert "secret_table" not in r.text
    assert "secret-param" not in r.text


def test_field_patch_domain_validation_message_is_kept(task_detail_client, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from cod_doc.services.validation import ValidationError

    _patch_field_service(monkeypatch, ValidationError("too_long", "описание слишком длинное"))
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description",
        data={"body": "x"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code >= 400
    assert "описание слишком длинное" in r.text


# ── WUI-001: шторка (peek) и шапка задачи ───────────────────────────────


def test_peek_returns_fragment_not_page(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-001?peek=1", headers={"HX-Request": "true"})
    assert r.status_code == 200
    body = r.text
    assert body.lstrip().startswith("<article")
    assert 'class="peek-doc peek-task"' in body
    assert 'data-peek-kind="task"' in body
    assert 'data-peek-url="/p/demo/tasks/DET-001"' in body
    assert "DET-001 · Set up DB schema" in body
    assert "<strong>payment_intent</strong>" in body
    for page_only in ("<html", 'class="topbar"', 'class="crumbs"', "<script", 'id="peek"'):
        assert page_only not in body, page_only


def test_peek_dependency_links_open_in_sheet(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/DET-002?peek=1")
    assert 'href="/p/demo/tasks/DET-001" class="dep-link" data-peek' in r.text
    assert 'href="/p/demo/tasks/DET-003" class="dep-link" data-peek' in r.text


def test_peek_unknown_task_is_html_404(task_detail_client) -> None:
    """404 шторки — фрагмент со ссылкой на страницу, а не JSON FastAPI."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/UNKNOWN-999?peek=1")
    assert r.status_code == 404
    assert "peek-error" in r.text
    assert "UNKNOWN-999" in r.text
    assert 'href="/p/demo/tasks/UNKNOWN-999" data-no-peek' in r.text


def test_full_page_carries_sheet_shell_and_assets(task_detail_client) -> None:
    client, entry = task_detail_client
    body = client.get(f"/p/{entry.name}/tasks/DET-001").text
    assert '<dialog id="peek" class="peek"' in body
    assert "/static/cod_doc_peek.js?v=" in body
    assert body.count('class="task-head"') == 1


def test_head_controls_target_head_not_themselves(task_detail_client) -> None:
    """Регресс: select и «Mark done» без hx-target получали <tr> внутрь себя."""
    client, entry = task_detail_client
    body = client.get(f"/p/{entry.name}/tasks/DET-001").text
    assert body.count('hx-target="closest .task-head"') == 2
    assert body.count('hx-vals=\'{"view": "detail"}\'') == 2


def test_status_detail_view_returns_head_and_trigger(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/status",
        data={"status": "in_review", "view": "detail"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert 'class="task-head"' in r.text
    assert "badge-lg badge-in_review" in r.text
    assert "<tr" not in r.text
    assert json.loads(r.headers["HX-Trigger"]) == {"cod_doc:task-changed": {"task_id": "DET-001"}}


def test_status_without_view_still_returns_row(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/status",
        data={"status": "in_review"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert '<tr id="task-DET-001"' in r.text
    assert "task-head" not in r.text


def test_complete_detail_view_returns_head(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/complete",
        data={"view": "detail"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "badge-lg badge-done" in r.text
    assert "Mark done" not in r.text
    assert "cod_doc:task-changed" in r.headers["HX-Trigger"]


def test_complete_refusal_keeps_alert_and_sends_no_trigger(task_detail_client) -> None:
    """Отказ задачу не менял: шапка прежняя, алерт out-of-band, события нет."""
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-002/complete",
        data={"view": "detail"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert 'class="task-head"' in r.text
    assert 'hx-swap-oob="afterbegin:#alerts"' in r.text
    assert "HX-Trigger" not in r.headers


def test_oob_alert_is_wrapped_so_the_box_survives(task_detail_client) -> None:
    """`afterbegin:` вставляет детей OOB-элемента: .alert обязан быть внутри обёртки.

    Без неё в #alerts приезжали голые <span> и <button> — алерт без рамки.
    """
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-002/complete",
        data={"view": "detail"},
        headers={"HX-Request": "true"},
    )
    assert re.search(
        r'<div hx-swap-oob="afterbegin:#alerts">\s*<div class="alert alert-warning">', r.text
    )


def test_field_patch_announces_task_change(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.post(
        f"/p/{entry.name}/tasks/DET-001/fields/description",
        data={"body": "new"},
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "cod_doc:task-changed" in r.headers["HX-Trigger"]


def test_field_cards_have_no_ids_and_relative_targets(task_detail_client) -> None:
    """Шторка над страницей другой задачи: id-цель попала бы в карточку страницы."""
    client, entry = task_detail_client
    edit = client.get(
        f"/p/{entry.name}/tasks/DET-001/fields/description/edit",
        headers={"HX-Request": "true"},
    ).text
    page = client.get(f"/p/{entry.name}/tasks/DET-001").text
    for body in (edit, page):
        assert 'id="task-field-' not in body
        assert "#task-field-" not in body
    assert edit.count('hx-target="closest .task-field-card"') == 3


def test_task_lists_mark_refresh_regions_and_peek_links(task_detail_client) -> None:
    client, entry = task_detail_client
    board = client.get(f"/p/{entry.name}/tasks").text
    assert 'id="tasks-body" data-peek-refresh' in board
    assert 'id="tasks-stats" data-peek-refresh' in board
    cards = re.findall(r'<a id="card-[^"]+"[^>]*>', board, flags=re.S)
    assert len(cards) == 3
    assert all("data-peek" in c for c in cards)
    chains = client.get(f"/p/{entry.name}/tasks?view=chains").text
    hits = re.findall(r'<a class="chain-card-hit"[^>]*>', chains, flags=re.S)
    assert len(hits) == 3
    assert all("data-peek" in h for h in hits)


def test_completed_commit_reaches_page_and_sheet(task_detail_client) -> None:
    """Коммит закрытия — в шапке и страницы, и шторки (контекст собран один раз)."""
    client, entry = task_detail_client
    engine = make_engine(f"sqlite:///{entry.cod_doc_dir / 'state.db'}")
    try:
        with transactional(make_session_factory(engine)) as session:
            tasks.complete(
                session,
                project_id=sole_project_id(session),
                task_id="DET-001",
                author="human:dakh",
                commit_sha="abcdef1234567890",
            )
    finally:
        engine.dispose()
    for url in (f"/p/{entry.name}/tasks/DET-001", f"/p/{entry.name}/tasks/DET-001?peek=1"):
        assert "<code>abcdef12</code>" in client.get(url).text, url


def test_peek_404_escapes_id_and_keeps_query(task_detail_client) -> None:
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/%3Cb%3Ex?peek=1&tab=history")
    assert r.status_code == 404
    assert "<b>x" not in r.text
    assert "&lt;b&gt;x" in r.text
    # Ссылка на полную страницу сохраняет прочие параметры, но не `peek`.
    assert 'href="/p/demo/tasks/%3Cb%3Ex?tab=history"' in r.text
    assert "peek=1" not in r.text


def test_peek_404_full_page_link_reencodes_path(task_detail_client) -> None:
    """Starlette отдаёт путь раскодированным: `?` из id без кодирования стал бы query."""
    client, entry = task_detail_client
    r = client.get(f"/p/{entry.name}/tasks/A%3FB%23C?peek=1")
    assert r.status_code == 404
    assert 'href="/p/demo/tasks/A%3FB%23C" data-no-peek' in r.text


def _request(path: str, raw_path: bytes | None, query: bytes) -> Request:
    scope = {"type": "http", "path": path, "query_string": query, "headers": []}
    if raw_path is not None:
        scope["raw_path"] = raw_path
    return Request(scope)


def test_full_page_url_keeps_encoded_slash() -> None:
    """`%2F` в ключе документа: в scope["path"] он уже неотличим от `/`."""
    req = _request("/p/x/docs/a/b", b"/p/x/docs/a%2Fb", b"peek=1&raw=1")
    assert full_page_url(req) == "/p/x/docs/a%2Fb?raw=1"


def test_full_page_url_without_raw_path_reencodes() -> None:
    req = _request("/p/x/tasks/A?B", None, b"peek=1")
    assert full_page_url(req) == "/p/x/tasks/A%3FB"


# ── ADO-148: done-карточка — коммит, PR, итог, файлы ─────────────────────


def _set_repo_url(url: str | None) -> None:
    from cod_doc.config import Config

    Config.load().set_project_repo_url("demo", url)


def _close_via_web(client, **fields: str):  # type: ignore[no-untyped-def]
    return client.post(
        "/p/demo/tasks/DET-001/complete",
        data={"view": "detail", **fields},
        headers={"HX-Request": "true"},
    )


def test_done_card_links_commit_pr_and_shows_summary(task_detail_client) -> None:
    client, _entry = task_detail_client
    _set_repo_url("git@github.com:o/r.git")
    r = _close_via_web(client, commit_sha="4B770E4abc", pr="#261", summary="Сделано **всё**")
    assert r.status_code == 200
    # Шапка из ответа /complete уже со ссылками.
    assert 'href="https://github.com/o/r/commit/4b770e4abc"' in r.text
    assert 'href="https://github.com/o/r/pull/261"' in r.text
    page = client.get("/p/demo/tasks/DET-001").text
    assert 'href="https://github.com/o/r/commit/4b770e4abc"' in page
    assert ">⇄ PR #261</a>" in page
    assert 'class="task-card task-summary-card"' in page
    assert "Сделано <strong>всё</strong>" in page


def test_done_card_without_repo_url_has_no_dead_links(task_detail_client) -> None:
    client, _entry = task_detail_client
    _set_repo_url(None)
    _close_via_web(client, commit_sha="4b770e4", pr="7")
    page = client.get("/p/demo/tasks/DET-001").text
    assert "/commit/" not in page
    assert "/pull/" not in page
    # Карточка без оболочки шторки: её «Open full page» заполняет JS.
    card = page.split('<dialog id="peek"')[0]
    assert 'href="#"' not in card
    # Данные не теряются: sha и номер PR видны текстом.
    assert "<code>4b770e4</code>" in page
    assert "⇄ PR #7</span>" in page


def test_done_card_without_pr_shows_no_pr_chip(task_detail_client) -> None:
    client, _entry = task_detail_client
    _set_repo_url("https://github.com/o/r")
    _close_via_web(client, commit_sha="4b770e4")
    page = client.get("/p/demo/tasks/DET-001").text
    assert 'href="https://github.com/o/r/commit/4b770e4"' in page
    assert "PR #" not in page
    # Без итога — системный reason не выдаётся за «что сделано».
    assert "task-summary-card" not in page


def test_mark_done_rejects_garbage_sha(task_detail_client) -> None:
    client, _entry = task_detail_client
    r = _close_via_web(client, commit_sha="not-a-sha")
    assert r.status_code == 400
    assert "sha" in r.text
    assert "badge-lg badge-done" not in client.get("/p/demo/tasks/DET-001").text


def test_mark_done_form_has_optional_fields(task_detail_client) -> None:
    client, _entry = task_detail_client
    page = client.get("/p/demo/tasks/DET-001").text
    for name in ('name="commit_sha"', 'name="pr"', 'name="summary"'):
        assert name in page, name


def test_done_card_lists_affected_files_at_closing_sha(task_detail_client) -> None:
    from sqlalchemy import select

    from cod_doc.infra.models import AffectedFileModel, TaskModel

    client, entry = task_detail_client
    _set_repo_url("https://github.com/o/r")
    engine = make_engine(f"sqlite:///{entry.cod_doc_dir / 'state.db'}")
    try:
        with transactional(make_session_factory(engine)) as session:
            row_id = session.execute(
                select(TaskModel.row_id).where(TaskModel.task_id == "DET-001")
            ).scalar_one()
            session.add(AffectedFileModel(task_id=row_id, path="docs/a.md"))
            session.add(AffectedFileModel(task_id=row_id, path="cod_doc/b.py"))
    finally:
        engine.dispose()
    _close_via_web(client, commit_sha="4b770e4")
    page = client.get("/p/demo/tasks/DET-001").text
    assert 'class="task-card task-files-card"' in page
    assert 'href="https://github.com/o/r/blob/4b770e4/docs/a.md"' in page
    # Порядок — по пути.
    assert page.index(">cod_doc/b.py<") < page.index(">docs/a.md<")


def test_pr_number_with_space_after_hash_is_accepted(task_detail_client) -> None:
    client, _entry = task_detail_client
    _set_repo_url("https://github.com/o/r")
    r = _close_via_web(client, pr="# 12")
    assert r.status_code == 200
    assert 'href="https://github.com/o/r/pull/12"' in r.text


# ── ADO-148: ссылки на хостинг — только безопасные и рабочие ──────────────


def test_file_url_drops_dot_segments_and_encodes() -> None:
    from cod_doc.api.web.task_detail import file_url

    base = "https://github.com/o/r"
    assert file_url(base, "4b770e4", "docs/a.md") == f"{base}/blob/4b770e4/docs/a.md"
    # `..` увёл бы ссылку за /blob/ после нормализации в браузере.
    assert file_url(base, "4b770e4", "../../settings/x") == f"{base}/blob/4b770e4/settings/x"
    # `#` и `?` не обрезают путь, пробел кодируется.
    assert file_url(base, None, "a b/c#d?e.md") == f"{base}/blob/HEAD/a%20b/c%23d%3Fe.md"
    assert file_url(base, "4b770e4", "../..") is None


def test_commit_and_pr_urls_reject_garbage() -> None:
    from cod_doc.api.web.task_detail import commit_url, pr_url

    base = "https://github.com/o/r"
    assert commit_url(base, "ABC1234") == f"{base}/commit/abc1234"
    # sha из MCP/CLI — любая строка: не hex — ссылки нет.
    assert commit_url(base, "abc/../../x") is None
    assert commit_url(base, "a b") is None
    assert pr_url(base, -5) is None
    assert pr_url(base, 0) is None


def test_links_survive_hand_edited_repo_url() -> None:
    """Реестр правят руками: адрес без схемы — нет ссылок, а не 500."""
    from cod_doc.api.web.task_detail import commit_url, file_url, pr_url

    for bad in ("github.com/o/r", "o/r", "https://gitlab.com"):
        assert commit_url(bad, "4b770e4") is None, bad
        assert pr_url(bad, 3) is None, bad
        assert file_url(bad, "4b770e4", "a.py") is None, bad


def test_gitlab_links() -> None:
    from cod_doc.api.web.task_detail import commit_url, file_url, pr_url

    base = "https://gitlab.example.com/g/sub/r"
    assert commit_url(base, "4b770e4") == f"{base}/-/commit/4b770e4"
    assert pr_url(base, 9) == f"{base}/-/merge_requests/9"
    assert file_url(base, "4b770e4", "a.py") == f"{base}/-/blob/4b770e4/a.py"
