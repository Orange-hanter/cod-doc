"""ACU-008: approval `doc_patch` — payload, отпечаток, память отказа."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ProjectModel
from cod_doc.services import approval_service

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session

_ARGS: dict[str, Any] = {"doc_key": "guide", "anchor": "setup", "old": "a.md", "new": "b.md"}


def _project(session: Session) -> int:
    now = datetime.now(UTC)
    project = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    project.created = now
    project.updated = now
    session.add(project)
    session.flush()
    return project.row_id


def _propose(
    session: Session, pid: int, *, args: dict[str, Any] = _ARGS, now: datetime | None = None
) -> approval_service.DocPatchRequest:
    return approval_service.request_doc_patch(
        session,
        pid,
        op="link_retarget",
        args=args,
        diff="-[x](a.md)\n+[x](b.md)\n",
        rationale="a.md переехал в b.md",
        base_revision_id="rev-1",
        now=now,
    )


def test_a_new_proposal_carries_the_full_payload(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        result = _propose(session, pid)

    assert result.outcome == "created"
    assert result.approval is not None
    assert result.approval.approval_type == approval_service.DOC_PATCH
    assert result.approval.requested_by == "agent:curator"
    payload = result.approval.payload
    assert payload["op"] == "link_retarget"
    assert payload["args"] == _ARGS
    assert payload["base_revision_id"] == "rev-1"
    assert payload["fingerprint"] == result.fingerprint
    assert payload["diff"].startswith("-[x]")


def test_a_pending_duplicate_is_not_created_again(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        first = _propose(session, pid)
        # Тот же смысл, другой порядок ключей и другая формулировка — тот же отпечаток.
        again = approval_service.request_doc_patch(
            session,
            pid,
            op="link_retarget",
            args=dict(reversed(list(_ARGS.items()))),
            diff="другой diff\n",
            rationale="другие слова",
        )
        pending = approval_service.list_approvals(session, pid, status="pending")

    assert again.outcome == "duplicate"
    assert again.approval is not None and first.approval is not None
    assert again.approval.approval_id == first.approval.approval_id
    assert pending["total"] == 1


def test_a_denied_proposal_stays_quiet_for_30_days_then_returns(
    engine_with_schema: Engine,
) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        first = _propose(session, pid)
        assert first.approval is not None
        approval_service.resolve(
            session, pid, first.approval.approval_id, decision="deny", resolved_by="human:dakh"
        )

        soon = _propose(session, pid, now=datetime.now(UTC) + timedelta(days=29))
        later = _propose(
            session,
            pid,
            now=datetime.now(UTC) + timedelta(days=approval_service.DENIAL_MEMORY_DAYS + 1),
        )

    assert soon.outcome == "suppressed"
    assert soon.approval is None
    assert later.outcome == "created"


def test_different_args_are_a_different_proposal(engine_with_schema: Engine) -> None:
    with transactional(make_session_factory(engine_with_schema)) as session:
        pid = _project(session)
        _propose(session, pid)
        other = _propose(session, pid, args={**_ARGS, "new": "c.md"})

    assert other.outcome == "created"


def test_an_empty_diff_is_refused(engine_with_schema: Engine) -> None:
    with (
        transactional(make_session_factory(engine_with_schema)) as session,
        pytest.raises(ValueError, match="diff"),
    ):
        approval_service.request_doc_patch(
            session, _project(session), op="link_retarget", args=_ARGS, diff=" ", rationale="r"
        )
