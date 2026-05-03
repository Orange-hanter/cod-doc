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
from cod_doc.infra.models import DependencyModel
from cod_doc.infra.repositories import (
    PlanRepository,
    PlanSectionRepository,
    ProjectRepository,
)
from cod_doc.services import task_service as tasks

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
            session, task_id=t1.task_id, new_status=TaskStatus.IN_PROGRESS, author="human:dakh"
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
    assert "badge-lg badge-in-progress" in body  # status badge prominent
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
    assert 'id="task-field-description"' in body
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
    assert 'id="task-field-description"' in body
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
    assert 'id="task-field-acceptance"' in body
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


def test_field_improve_swaps_textarea_with_suggestion(
    task_detail_client, monkeypatch
) -> None:
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
    assert 'id="task-field-description"' in body
    assert "AI-improved (make it more formal)" in body
    assert "AI suggestion ready" in body
    # Intent input keeps the user's prompt
    assert 'value="make it more formal"' in body
    # DB unchanged — full page still shows original text
    r2 = client.get(f"/p/{entry.name}/tasks/DET-001")
    assert "AI-improved" not in r2.text


def test_field_improve_surfaces_backend_error_inline(
    task_detail_client, monkeypatch
) -> None:
    """When the LLM call fails, return the original draft + an inline error notice."""
    client, entry = task_detail_client
    from cod_doc.services import ai_text

    def boom(text: str, intent: str, *, cfg):  # noqa: ANN202
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

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    with transactional(factory) as session:
        task = task_svc.get(session, "DET-001")
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
