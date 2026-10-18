"""CLO-015: CLI ``remote``, ``fetch/pull/push``, ``sync status/log/verify`` (RFC 29 §10.8).

Acceptance: «Команды и опции по RFC 29 §10.8; ``--json`` печатается через
``click.echo`` и парсится; ``remote add s3://`` пишет предупреждение».

Две «машины» — два ``COD_DOC_HOME`` со своим ``config.yaml`` и своей БД под
одним слагом. Реплика B получает тот же ``project.uid``, что у A, и пустой
журнал — так выглядела бы реплика после clone (CLO-029).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import yaml
from click.testing import CliRunner
from sqlalchemy import select, text

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import PlanModel, PlanSectionModel, ProjectModel, TaskModel
from cod_doc.services import task_service

if TYPE_CHECKING:
    from pathlib import Path

SLUG = "demo"
AUTHOR = "human:dakh"


def _run(*args: str) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args), catch_exceptions=False)
    return result.exit_code, result.stdout, result.stderr


def _json(*args: str) -> object:
    code, out, err = _run(*args, "--json")
    assert code == 0, (out, err)
    return json.loads(out)


def _machine(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Переключиться на «машину» с этим COD_DOC_HOME и завести на ней проект."""
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    Config.clear_cache()
    root = home / "repo"
    if not root.exists():
        root.mkdir()
        code, out, err = _run("project", "add", str(root), "--name", SLUG)
        assert code == 0, (out, err)
    return root


def _factory():  # type: ignore[no-untyped-def]
    entry = Config.load().get_project(SLUG)
    assert entry is not None
    return db_for_entry(entry)[0]


def _seed_task(task_id: str) -> None:
    sf = _factory()
    with transactional(sf) as s:
        pid = s.scalars(select(ProjectModel.row_id).where(ProjectModel.slug == SLUG)).one()
        plan = s.scalars(select(PlanModel).where(PlanModel.project_id == pid)).one_or_none()
        if plan is None:
            now = datetime.now(UTC)
            plan = PlanModel(project_id=pid, scope="demo-plan", created=now, last_updated=now)
            s.add(plan)
            s.flush()
            s.add(
                PlanSectionModel(plan_id=plan.row_id, letter="A", title="A", slug="A-A", position=0)
            )
            s.flush()
        sec = s.scalars(select(PlanSectionModel).where(PlanSectionModel.plan_id == plan.row_id))
        task_service.create(
            s,
            project_id=pid,
            plan_id=plan.row_id,
            section_id=sec.one().row_id,
            task_id=task_id,
            title=f"Task {task_id}",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=AUTHOR,
        )


def _project_uid() -> str:
    with transactional(_factory()) as s:
        return str(s.scalars(select(ProjectModel.uid).where(ProjectModel.slug == SLUG)).one())


def _as_clone_of(uid: str) -> None:
    """Реплика B: uid проекта как у A, журнал пуст (так её оставил бы clone).

    Рутины и дерево документации, которые ``project add`` заводит на каждой
    машине, тоже убираются: они приедут с A, а две независимо заведённые
    строки с одним натуральным ключом — склейка секции C (CLO-023), не
    fast-forward. Ревизии этих строк — тоже: журнал уезжает с CLO-024, и
    ревизии строк, которых на A нет, дали бы B лишние строки дайджеста.
    """
    sf = _factory()
    with transactional(sf) as s:
        s.execute(text("DELETE FROM sync_op"))
        s.execute(text("DELETE FROM revision"))
        s.execute(text("DELETE FROM routine"))
        s.execute(text("UPDATE doc_node SET parent_id = NULL"))
        s.execute(text("DELETE FROM doc_node"))
        s.execute(text("UPDATE project SET uid = :u WHERE slug = :s"), {"u": uid, "s": SLUG})


@pytest.fixture
def machines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    remote_dir = tmp_path / "remote"
    remote_dir.mkdir()
    return {
        "a": tmp_path / "home-a",
        "b": tmp_path / "home-b",
        "remote": remote_dir,
    }


# --- remote add / list / remove -----------------------------------------------------


def test_remote_add_list_remove_round_trip(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _machine(machines["a"], monkeypatch)
    url = machines["remote"].as_uri()

    added = _json("remote", "add", "origin", url, "-p", SLUG)
    assert added == {
        "remote": {"name": "origin", "url": url, "kind": "file"},
        "replaced": False,
        "warnings": [],
    }
    cfg = yaml.safe_load((machines["a"] / "config.yaml").read_text())
    entry = next(p for p in cfg["projects"] if p["name"] == SLUG)
    assert entry["remotes"] == {"origin": {"url": url, "kind": "file"}}

    assert _json("remote", "list", "-p", SLUG) == [{"name": "origin", "url": url, "kind": "file"}]
    assert _json("remote", "remove", "origin", "-p", SLUG) == {"removed": "origin"}
    assert _json("remote", "list", "-p", SLUG) == []

    code, _out, err = _run("remote", "remove", "origin", "-p", SLUG)
    assert code == 1
    assert "SYNC-110" in err


def test_remote_add_s3_warns_and_unreachable_push_reports_sync_101(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CLO-028: ``s3://`` больше не SYNC-110 — push идёт в сеть; недоступный
    адрес — SYNC-101, и ни креды, ни адрес запроса в текст ошибки не попадают."""
    _machine(machines["a"], monkeypatch)
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIDEXAMPLE")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "wJalrXUtnFEMI-secret")
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")

    code, out, err = _run("remote", "add", "origin", "s3://bucket/cod-doc", "-p", SLUG)

    assert code == 0, (out, err)
    assert "без разграничения доступа" in err
    assert "warning" not in out
    assert _json("remote", "list", "-p", SLUG) == [
        {"name": "origin", "url": "s3://bucket/cod-doc", "kind": "s3"}
    ]
    assert "пока не поддержан" not in err
    code, _out, err = _run("push", "-p", SLUG)
    assert code == 1
    assert err.strip().splitlines()[-1].startswith("Error: SYNC-101")
    assert "wJalrXUtnFEMI-secret" not in err


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("ftp://host/x", "auto"),
        ("file:///tmp/x", "s3"),
        ("file://relative", "auto"),
        ("s3://Bad_Bucket/x", "auto"),
        ("s3://bucket/../x", "s3"),
        ("s3://AKID:secret@bucket/x", "auto"),
    ],
)
def test_remote_add_rejects_bad_url_or_kind(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch, url: str, kind: str
) -> None:
    _machine(machines["a"], monkeypatch)
    code, _out, err = _run("remote", "add", "origin", url, "-p", SLUG, "--kind", kind)
    assert code == 1
    assert "SYNC-110" in err
    assert _json("remote", "list", "-p", SLUG) == []


# --- push / fetch / pull / sync ------------------------------------------------------


def test_push_fetch_pull_status_log_verify_between_two_machines(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    url = machines["remote"].as_uri()
    _machine(machines["a"], monkeypatch)
    _run("remote", "add", "origin", url, "-p", SLUG)
    _seed_task("DEM-001")
    uid = _project_uid()

    before = _json("sync", "status", "-p", SLUG)
    assert isinstance(before, dict)
    assert before["remote"] == "origin"
    assert before["pending_ops"] > 0
    assert before["head_remote"] is None

    pushed = _json("push", "-p", SLUG)
    assert isinstance(pushed, dict)
    assert pushed["ops"] == before["pending_ops"]
    head = pushed["head"]
    log = _json("sync", "log", "-p", SLUG)
    assert isinstance(log, list)
    assert [(e["sha"], e["ops"]) for e in log] == [(head, pushed["ops"])]
    assert _json("sync", "verify", "-p", SLUG)["equal"] is True  # type: ignore[index]

    _machine(machines["b"], monkeypatch)
    _as_clone_of(uid)
    _run("remote", "add", "origin", url, "-p", SLUG)

    assert _json("fetch", "-p", SLUG) == {"remote": "origin", "new": [head]}
    st = _json("sync", "status", "-p", SLUG, "--verify")
    assert isinstance(st, dict)
    assert (st["behind"], st["head_local"], st["head_remote"]) == (1, None, head)
    assert st["verify"]["equal"] is False
    assert "doc drift" in st["drift_hint"]
    code, _out, _err = _run("sync", "verify", "-p", SLUG)
    assert code == 1

    dry = _json("pull", "-p", SLUG, "--dry-run")
    assert dry["applied"] == [head]  # type: ignore[index]
    pulled = _json("pull", "-p", SLUG)
    assert isinstance(pulled, dict)
    assert (pulled["applied"], pulled["head"], pulled["fast_forward"]) == ([head], head, True)
    with transactional(_factory()) as s:
        assert s.scalars(select(TaskModel.task_id)).all() == ["DEM-001"]
    st = _json("sync", "status", "-p", SLUG, "--verify")
    assert isinstance(st, dict)
    assert (st["behind"], st["ahead"], st["pending_ops"]) == (0, 0, 0)
    assert st["verify"]["equal"] is True
    code, out, _err = _run("sync", "verify", "-p", SLUG)
    assert code == 0
    assert "совпал" in out


def test_status_without_remote_is_local_only(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _machine(machines["a"], monkeypatch)
    st = _json("sync", "status", "-p", SLUG, "--verify")
    assert isinstance(st, dict)
    assert st["remote"] is None
    assert st["verify"] is None
    assert st["epoch_local"] == "0050_sync_rank"
    assert _json("sync", "log", "-p", SLUG) == []


def test_pull_without_remote_reports_sync_110(
    machines: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    _machine(machines["a"], monkeypatch)
    for cmd in ("fetch", "pull", "push"):
        code, _out, err = _run(cmd, "-p", SLUG, "--remote", "nope")
        assert code == 1, cmd
        assert "SYNC-110" in err, cmd
