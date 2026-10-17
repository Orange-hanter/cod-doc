"""CLO-026: конфликты в curator_next, CLI и MCP (RFC 29 §3.7, §10.7–10.8; SCN-089).

Acceptance: «Открытые конфликты — один пункт очереди curator_next ниже битых
ссылок и выше Инбокса с командой sync resolve; CLI sync conflicts/resolve и
MCP sync_conflicts/sync_resolve по RFC 29 §10.7–10.8; паритет-спек обновлён;
тест на SCN-089.»

Конфликт даёт настоящее текстовое слияние (``text_merge``, CLO-019): обе
реплики переписали одну и ту же строку секции — ханки пересекаются.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select

from cod_doc.cli import main
from cod_doc.config import Config, ProjectEntry
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    RevisionModel,
    SectionModel,
    SyncConflictModel,
)
from cod_doc.mcp.server import mcp
from cod_doc.services import curator_service, doc_service
from cod_doc.services.sync_service import pull, push
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.file import FileRemote

DOC = "spec"
BOB = "human:bob"
_TICK = 0.003


@pytest.fixture
def pair(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    r.make_doc(a, DOC, {"intro": "общая строка\n"})
    _push(a, store)
    _pull(b, store)
    return a, b, store


def _push(rep: r.Replica, store: FileRemote) -> None:
    push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _patch(rep: r.Replica, body: str) -> None:
    time.sleep(_TICK)
    with transactional(rep.sf) as s:
        doc_service.patch_section(
            s,
            project_id=rep.project_id,
            document_id=r.doc_id(rep, DOC),
            anchor="intro",
            new_body=body,
            author=r.AUTHOR,
            reindex=False,
        )


def _body(rep: r.Replica) -> str:
    with transactional(rep.sf, commit=False) as s:
        return s.scalars(
            select(SectionModel.body).where(SectionModel.document_id == r.doc_id(rep, DOC))
        ).one()


def _register(rep: r.Replica, tmp_path: Path) -> None:
    """Проект реплики в config.yaml (COD_DOC_HOME — tmp из autouse-фикстуры)."""
    root = tmp_path / f"root-{rep.name}"
    root.mkdir(exist_ok=True)
    Config.clear_cache()
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name=r.SLUG, path=str(root), db_url=f"sqlite:///{rep.db_path}"))
    Config.clear_cache()


def _cli(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.stdout + result.stderr


def _curator(rep: r.Replica, tmp_path: Path) -> dict[str, Any]:
    with transactional(rep.sf, commit=False) as s:
        return curator_service.next(
            s,
            project_id=rep.project_id,
            root_path=tmp_path,
            master_path=tmp_path / "MASTER.md",
            project_slug=r.SLUG,
            limit=50,
        )


def test_scn_089_section_conflict_in_curator_resolved_by_cli_and_delivered(
    pair, tmp_path: Path
) -> None:
    a, b, store = pair
    _patch(a, "правка A\n")
    _patch(b, "правка B\n")
    _push(a, store)

    # 1. pull на B: ровно один конфликт, в БД B — «наше».
    pulled = _pull(b, store)
    assert len(pulled.conflicts) == 1
    uid = pulled.conflicts[0]
    assert _body(b) == "правка B\n"

    # 2. curator_next на B: пункт очереди с командой sync resolve.
    card = _curator(b, tmp_path)
    items = [p for p in card["priority"] if p["kind"] == "sync_conflict"]
    assert len(items) == 1
    assert f"cod-doc sync resolve {uid} -p {r.SLUG}" in items[0]["suggested_action"]
    assert card["meta"]["counts"]["sync_conflicts"] == 1

    # 3. sync conflicts / sync resolve --take theirs через CLI на B.
    _register(b, tmp_path)
    code, out = _cli("sync", "conflicts", "-p", r.SLUG, "--json")
    assert code == 0, out
    listed = json.loads(out)
    assert [c["uid"] for c in listed] == [uid]
    assert (listed[0]["kind"], listed[0]["field"], listed[0]["theirs"]) == (
        "text",
        "body",
        "правка A\n",
    )
    code, out = _cli(
        "sync", "resolve", uid, "-p", r.SLUG, "--take", "theirs", "--author", BOB, "--json"
    )
    assert code == 0, out
    resolved = json.loads(out)
    assert resolved["status"] == "resolved"
    assert resolved["revision_id"]
    assert _body(b) == "правка A\n"

    # 4. push на B, pull на A: секции равны, ревизия разрешения приехала.
    _push(b, store)
    _pull(a, store)
    assert _body(a) == _body(b) == "правка A\n"
    with transactional(b.sf, commit=False) as s:
        row = s.get_one(SyncConflictModel, uid)
        assert (row.status, row.resolved_by) == ("resolved", BOB)
        event = s.scalars(
            select(ActivityEventModel).where(ActivityEventModel.kind == "sync.conflict_resolved")
        ).one()
        assert event.actor_id == BOB
    with transactional(a.sf, commit=False) as s:
        delivered = s.scalars(
            select(RevisionModel.author).where(RevisionModel.revision_id == resolved["revision_id"])
        ).one()
    assert delivered == BOB
    assert [p for p in _curator(b, tmp_path)["priority"] if p["kind"] == "sync_conflict"] == []


def test_mcp_sync_conflicts_and_resolve(pair, tmp_path: Path) -> None:
    a, b, store = pair
    _patch(a, "правка A\n")
    _patch(b, "правка B\n")
    _push(a, store)
    [uid] = _pull(b, store).conflicts
    _register(b, tmp_path)

    tools = mcp._tool_manager._tools
    listed = tools["sync_conflicts"].fn(project=r.SLUG)
    json.dumps(listed)
    assert [c["uid"] for c in listed] == [uid]
    assert listed[0]["created"]
    with pytest.raises(ValueError, match=r"^SYNC-302"):
        tools["sync_resolve"].fn(project=r.SLUG, conflict_uid="nope", take="ours")

    out = tools["sync_resolve"].fn(project=r.SLUG, conflict_uid=uid, take="ours", author=BOB)
    json.dumps(out)
    assert (out["status"], out["resolved_by"]) == ("resolved", BOB)
    assert out["revision_id"]
    assert tools["sync_conflicts"].fn(project=r.SLUG) == []
    assert [c["uid"] for c in tools["sync_conflicts"].fn(project=r.SLUG, status="all")] == [uid]


def test_conflicts_rank_below_broken_links_and_above_inbox() -> None:
    card: dict[str, Any] = {
        "drift": {"issues": []},
        "links": [{"doc_key": "d", "anchor": "a", "title": "LINK-BROKEN"}],
        "master": [{"status": "BROKEN", "path": "x.md", "expected": "-"}],
        "findings": [{"finding_uid": "f", "severity": "minor", "title": "t"}],
        "questions": {"broken_links": [], "answered": [], "stale": []},
        "unplaced": {"count": 3, "tree_seeded": True},
    }
    conflicts = [
        {"uid": "c1", "entity_kind": "section", "entity_uid": "s", "field": "body", "kind": "text"},
        {"uid": "c2", "entity_kind": "task", "entity_uid": "t", "field": "title", "kind": "field"},
    ]
    kinds = [
        p["kind"]
        for p in curator_service._build_priority(
            card, slug="demo", master_rel="MASTER.md", sync_conflicts=conflicts
        )
    ]
    assert kinds == ["link", "sync_conflict", "master", "unplaced", "finding"]
