"""CLO-030: аренда номеров и закрепление провизорных ID (RFC 29 §3.3, §10.1, §10.6).

Acceptance: «Пять генераторов ID из RFC 29 §2 при настроенном remote берут номер
из sync_lease (CAS на leases/<PREFIX>), без аренды — провизорный PREFIX-~xxxx с
предупреждением SYNC-107; push закрепляет номер, переписывает human_id_refs,
пишет entity_alias reason=pinned; task_get по провизорному ID резолвит алиас;
без remote генераторы работают как сегодня; тест на SCN-086.»

«Настроенный remote» — запись ``remotes`` проекта в ``config.yaml``: фикстура
``lab`` регистрирует проект ``demo`` с ``db_url`` реплики A и file-remote в tmp.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import pytest
from sqlalchemy import select, update

from cod_doc.config import Config, ProjectEntry
from cod_doc.domain.entities import (
    Priority,
    QuestionLinkKind,
    QuestionRelation,
    ScenarioKind,
    TaskType,
)
from cod_doc.infra.db import transactional
from cod_doc.infra.models import (
    ADRTaskModel,
    EntityAliasModel,
    QuestionLinkModel,
    SyncLeaseModel,
    TaskModel,
)
from cod_doc.services import adr_service, question_service, task_service
from cod_doc.services.scenario_service import crud as scenario_crud
from cod_doc.services.story_service import crud as story_crud
from cod_doc.services.sync_service import digest, leases, pull, push
from cod_doc.services.sync_service.changeset import decode
from cod_doc.services.sync_service.errors import ProvisionalIdWarning
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path

    from cod_doc.infra.remote.base import JsonValue, RemoteStore
    from cod_doc.infra.remote.file import FileRemote

PROVISIONAL = re.compile(r"^[A-Z]{1,5}-~[a-z2-7]{4}$")
PREFIX = "DEM"  # id_prefix_from_scope("demo-plan")


# --- обвязка -----------------------------------------------------------------------


def _register(tmp_path: Path, a: r.Replica, store: FileRemote) -> None:
    cfg = Config.load()
    cfg.add_project(ProjectEntry(name=r.SLUG, path=str(tmp_path), db_url=f"sqlite:///{a.db_path}"))
    Config.load().set_project_remote(r.SLUG, "origin", {"url": store.root.as_uri(), "kind": "file"})


@pytest.fixture
def lab(tmp_path: Path, isolated_cod_doc_home: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    del isolated_cod_doc_home
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    _register(tmp_path, a, store)
    return a, b, store


@pytest.fixture
def bare(tmp_path: Path) -> tuple[r.Replica, r.Replica, FileRemote]:
    """Те же реплики без записи remote в конфиге — генераторы как до CLO-030."""
    a = r.replica_a(tmp_path)
    return a, r.replica_b(tmp_path, a), r.remote(tmp_path)


def _push(rep: r.Replica, store: FileRemote) -> push.PushResult:
    return push.push(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _pull(rep: r.Replica, store: FileRemote) -> pull.PullResult:
    return pull.pull(rep.sf, r.SLUG, author=r.AUTHOR, store=store)


def _auto_task(rep: r.Replica, title: str, description: str | None = None) -> str:
    plan_id, section_id = r.plan_ids(rep)
    with transactional(rep.sf) as s:
        task = task_service.create(
            s,
            project_id=rep.project_id,
            plan_id=plan_id,
            section_id=section_id,
            title=title,
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author=r.AUTHOR,
            description=description,
        )
        return task.task_id


def _lease(rep: r.Replica, prefix: str) -> tuple[int, int] | None:
    with transactional(rep.sf) as s:
        row = s.get(SyncLeaseModel, (rep.project_id, prefix))
        return None if row is None else (row.next_number, row.end_number)


def _exhaust(rep: r.Replica, prefix: str) -> None:
    with transactional(rep.sf) as s:
        s.execute(
            update(SyncLeaseModel)
            .where(SyncLeaseModel.project_id == rep.project_id, SyncLeaseModel.prefix == prefix)
            .values(next_number=SyncLeaseModel.end_number + 1)
        )


def _task_ids(rep: r.Replica) -> set[str]:
    with transactional(rep.sf) as s:
        return set(
            s.scalars(select(TaskModel.task_id).where(TaskModel.project_id == rep.project_id))
        )


def _remote_values(store: FileRemote, project_uid: str) -> list[object]:
    """Значения полей сущностей во всех changeset'ах remote (объекты — с диска)."""
    out: list[object] = []
    for path in sorted((store.root / project_uid / "objects").rglob("*.zst")):
        sha = path.parent.name + path.name.removesuffix(".zst")
        cs = decode(store.get_object(project_uid, sha), sha)
        for op in cs.ops:
            if op.entity_kind == "revision":
                continue  # diff ревизии — текст, его резолвит алиас (§3.3 п. 4)
            out.extend(op.fields.values())
            out.extend(op.base.values())
    return out


def _project_uid(rep: r.Replica) -> str:
    from cod_doc.infra.models import ProjectModel

    with transactional(rep.sf) as s:
        return str(s.get_one(ProjectModel, rep.project_id).uid)


# --- без remote — как сегодня -----------------------------------------------------


def test_without_remote_generators_number_max_plus_one(bare: tuple) -> None:
    a, _b, _store = bare
    first = _auto_task(a, "one")
    second = _auto_task(a, "two")
    assert (first, second) == (f"{PREFIX}-001", f"{PREFIX}-002")
    with transactional(a.sf) as s:
        assert list(s.scalars(select(SyncLeaseModel))) == []
        assert leases.enabled(s, a.project_id) is False


# --- генераторы при настроенном remote --------------------------------------------


def test_generator_takes_the_number_from_the_lease(lab: tuple) -> None:
    a, _b, store = lab
    _push(a, store)  # push берёт аренду впрок (префиксы планов и фиксированные)
    lease = _lease(a, PREFIX)
    assert lease is not None
    start, end = lease
    assert end - start + 1 == leases.LEASE_BLOCK
    assert _auto_task(a, "leased") == f"{PREFIX}-{start:03d}"
    assert _lease(a, PREFIX) == (start + 1, end)
    body, _etag = store.get_json(_project_uid(a), f"leases/{PREFIX}") or ({}, "")
    assert body["next"] == end + 1


def test_without_lease_every_generator_gives_a_provisional_id_with_sync_107(lab: tuple) -> None:
    a, _b, _store = lab
    with pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        task_id = _auto_task(a, "offline")
    assert PROVISIONAL.fullmatch(task_id), task_id
    assert task_id.startswith(f"{PREFIX}-~")
    with transactional(a.sf) as s, pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        adr = adr_service.create(s, project_id=a.project_id, title="Решение", author=r.AUTHOR)
        assert PROVISIONAL.fullmatch(adr.adr_id) and adr.adr_id.startswith("ADR-~")
    with transactional(a.sf) as s, pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        q = question_service.create(
            s, project_id=a.project_id, title="Вопрос", question="Зачем?", author=r.AUTHOR
        )
        assert PROVISIONAL.fullmatch(q.question_id) and q.question_id.startswith("Q-~")
    with transactional(a.sf) as s, pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        scn = scenario_crud.create(
            s,
            project_id=a.project_id,
            title="Сценарий",
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            group_key="g",
            kind=ScenarioKind.HAPPY_PATH,
            steps=["шаг"],
        )
        assert PROVISIONAL.fullmatch(scn.scenario_id) and scn.scenario_id.startswith("SCN-~")
    with transactional(a.sf) as s, pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        story_id = story_crud.next_story_id(s, a.project_id)
        assert PROVISIONAL.fullmatch(story_id) and story_id.startswith("US-~")


def test_hints_do_not_consume_the_lease(lab: tuple) -> None:
    a, _b, store = lab
    _push(a, store)
    before = _lease(a, "ADR")
    with transactional(a.sf) as s:
        hint = adr_service.next_adr_id(s, a.project_id)
        assert hint == adr_service.next_adr_id(s, a.project_id)
    assert _lease(a, "ADR") == before
    assert before is not None
    assert hint == f"ADR-{before[0]:03d}"


# --- аренда на remote: блоки не пересекаются --------------------------------------


class _Racing:
    """Транспорт, перед каждым ``cas_json`` которого другая реплика берёт блок."""

    def __init__(self, inner: RemoteStore, rival: r.Replica, rounds: int) -> None:
        self.inner = inner
        self.rival = rival
        self.rounds = rounds

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: dict[str, JsonValue],
    ) -> str | None:
        if self.rounds > 0:
            self.rounds -= 1
            leases.acquire(self.rival.sf, r.SLUG, PREFIX, store=self.inner)
        return self.inner.cas_json(project_uid, key, expected_etag, body)


def test_two_replicas_lease_disjoint_blocks_under_cas_contention(
    tmp_path: Path, isolated_cod_doc_home: Path
) -> None:
    """Реплика B берёт блок между чтением и CAS реплики A: у A — следующий блок.

    Тот же протокол на каждой реализации ``RemoteStore`` (file, s3) — контракт
    ``tests/infra/remote/test_remote_contract.py``.
    """
    del isolated_cod_doc_home
    a = r.replica_a(tmp_path)
    b = r.replica_b(tmp_path, a)
    store = r.remote(tmp_path)
    racing = _Racing(store, b, rounds=2)
    got_a = leases.acquire(a.sf, r.SLUG, PREFIX, store=racing)  # type: ignore[arg-type]
    got_b = _lease(b, PREFIX)
    assert got_b is not None
    blocks = [range(got_a[0], got_a[1] + 1), range(got_b[0], got_b[1] + 1)]
    assert not set(blocks[0]) & set(blocks[1])
    body, _ = store.get_json(_project_uid(a), f"leases/{PREFIX}") or ({}, "")
    assert body["next"] == max(got_a[1], got_b[1]) + 1
    holders = body["holders"]
    assert isinstance(holders, dict)
    assert len(holders) == 2


def test_first_lease_starts_above_local_numbers(lab: tuple) -> None:
    a, _b, store = lab
    r.make_task(a, f"{PREFIX}-041")
    start, _end = leases.acquire(a.sf, r.SLUG, PREFIX, store=store)
    assert start == 42


# --- SCN-086 ----------------------------------------------------------------------


def test_scn_086_exhausted_offline_lease_gives_provisional_id_pinned_on_push(
    lab: tuple,
) -> None:
    """SCN-086: провизорный ID до push, постоянный номер после, алиас — в task_get."""
    a, b, store = lab
    _push(a, store)
    _pull(b, store)
    # Предусловие: у A осталось 0 номеров аренды; B, пока A офлайн, пушит задачи,
    # заняв следующий блок номеров.
    _exhaust(a, PREFIX)
    _exhaust(b, PREFIX)
    with pytest.warns(ProvisionalIdWarning):
        b_ids = {_auto_task(b, "b-1"), _auto_task(b, "b-2")}
    b_pushed = _push(b, store)
    b_ids = {b_pushed.pinned.get(i, i) for i in b_ids}
    assert all(not leases.is_provisional(i) for i in b_ids)

    # 1. На реплике A создать задачу с префиксом.
    with pytest.warns(ProvisionalIdWarning, match="SYNC-107"):
        prov = _auto_task(a, "offline")
    assert PROVISIONAL.fullmatch(prov), prov
    # 2. Сослаться на её ID в описании другой задачи — и строковыми ссылками.
    with pytest.warns(ProvisionalIdWarning):
        other = _auto_task(a, "refers", description=f"зависит от {prov}")
    with transactional(a.sf) as s:
        adr = adr_service.create(
            s, project_id=a.project_id, title="ADR", author=r.AUTHOR, adr_id="ADR-001"
        )
        adr_service.link_task(
            s, project_id=a.project_id, adr_id=adr.adr_id, task_id=prov, author=r.AUTHOR
        )
        q = question_service.create(
            s,
            project_id=a.project_id,
            title="Вопрос",
            question="?",
            author=r.AUTHOR,
            question_id="Q-001",
        )
        question_service.link(
            s,
            project_id=a.project_id,
            question_id=q.question_id,
            to_kind=QuestionLinkKind.TASK,
            to_ref=prov,
            relation=QuestionRelation.ADDRESSED_BY,
            author=r.AUTHOR,
        )

    # 3. Восстановить сеть и выполнить push.
    pushed = _push(a, store)
    assert set(pushed.pinned) == {prov, other}
    pinned = pushed.pinned[prov]
    assert re.fullmatch(rf"{PREFIX}-\d{{3}}", pinned), pinned
    assert pinned not in b_ids
    assert pushed.pinned[other] not in b_ids

    with transactional(a.sf) as s:
        task = task_service.get(s, prov, project_id=a.project_id)
        assert task is not None
        assert task.task_id == pinned
        assert task_service.get(s, pinned, project_id=a.project_id) == task
        alias = s.get(EntityAliasModel, (a.project_id, "task", prov))
        assert alias is not None
        assert alias.reason == "pinned"
        # human_id_refs переписаны; текст — нет (его резолвит алиас).
        refs = s.scalars(select(ADRTaskModel.task_id)).all()
        assert refs == [pinned]
        assert s.scalars(select(QuestionLinkModel.to_ref)).all() == [pinned]
        described = task_service.get(s, pushed.pinned[other], project_id=a.project_id)
        assert described is not None
        assert described.description == f"зависит от {prov}"

    # Провизорный ID не покинул реплику.
    values = _remote_values(store, _project_uid(a))
    assert not [v for v in values if leases.is_provisional(v)]
    assert f"зависит от {prov}" in values  # текст уезжает как есть

    # B видит закреплённые номера, состояние реплик совпало.
    _pull(b, store)
    assert {pinned, pushed.pinned[other]} <= _task_ids(b)
    with transactional(a.sf) as sa, transactional(b.sf) as sb:
        assert digest.compute(sa, a.project_id) == digest.compute(sb, b.project_id)


def test_task_get_tool_marks_the_rename(lab: tuple) -> None:
    from cod_doc.mcp.server import mcp

    a, _b, store = lab
    with pytest.warns(ProvisionalIdWarning):
        prov = _auto_task(a, "offline")
    pinned = _push(a, store).pinned[prov]
    out = mcp._tool_manager._tools["task_get"].fn(project=r.SLUG, task_id=prov)
    json.dumps(out)
    assert out["task_id"] == pinned
    assert out["renamed_from"] == prov
    assert out["renamed_to"] == pinned
    assert f"переименовано в {pinned}" in out["note"]


def test_other_lookups_resolve_pinned_aliases(lab: tuple) -> None:
    a, _b, store = lab
    with transactional(a.sf) as s, pytest.warns(ProvisionalIdWarning):
        adr = adr_service.create(s, project_id=a.project_id, title="Решение", author=r.AUTHOR)
        q = question_service.create(
            s, project_id=a.project_id, title="Вопрос", question="?", author=r.AUTHOR
        )
        scn = scenario_crud.create(
            s,
            project_id=a.project_id,
            title="Сценарий",
            preconditions="p",
            expected="e",
            author=r.AUTHOR,
            group_key="g",
            kind=ScenarioKind.HAPPY_PATH,
            steps=["шаг"],
        )
        ids = (adr.adr_id, q.question_id, scn.scenario_id)
    pinned = _push(a, store).pinned
    assert set(ids) <= set(pinned)
    with transactional(a.sf) as s:
        found = adr_service.get(s, a.project_id, ids[0])
        assert found is not None
        assert found.adr_id == pinned[ids[0]]
        got_q = question_service.get(s, a.project_id, ids[1])
        assert got_q is not None
        assert got_q.question_id == pinned[ids[1]]
        got_s = scenario_crud.get(s, a.project_id, ids[2])
        assert got_s is not None
        assert got_s.scenario_id == pinned[ids[2]]


# --- двойник арендованного номера (CLO-023 оставил MergeRequiredError) ------------


def test_leased_twin_of_an_unshipped_row_yields_and_is_pinned(lab: tuple) -> None:
    """Номер, выданный мимо аренды, совпал с чужим: своя неуехавшая строка уступает."""
    a, b, store = lab
    _push(a, store)
    _pull(b, store)
    r.make_task(a, f"{PREFIX}-500", description="a")
    _push(a, store)
    r.make_task(b, f"{PREFIX}-500", description="b")  # explicit — мимо аренды
    _pull(b, store)
    with transactional(b.sf) as s:
        mine = s.scalars(select(TaskModel).where(TaskModel.description == "b")).one()
        theirs = s.scalars(select(TaskModel).where(TaskModel.description == "a")).one()
        assert theirs.task_id == f"{PREFIX}-500"
        assert leases.is_provisional(mine.task_id)
    pushed = _push(b, store)
    assert len(pushed.pinned) == 1
    _pull(a, store)
    with transactional(a.sf) as sa, transactional(b.sf) as sb:
        assert digest.compute(sa, a.project_id) == digest.compute(sb, b.project_id)
    assert {f"{PREFIX}-500", *pushed.pinned.values()} <= _task_ids(a)
