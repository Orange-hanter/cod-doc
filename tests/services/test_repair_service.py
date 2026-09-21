"""ADO-192 фаза D: исполнитель автопочинки — `repair_service`.

Проверяется не «функция вызвалась», а результат на диске и в БД: после
`apply` дрейф действительно становится `in_sync`, реестр `MASTER.md`
переписан, замки сняты. Отдельно стережётся одобренный объём — `stale_export`
и `missing` чинить нельзя, и ни одного `RepairAction` на них не заводится.

Фикстура строится через CLI (`project add` + `import docs`), как и тесты
`curator_service`: только так на диске оказываются настоящие файлы, по которым
`detect_project_drift` и считает дрейф.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner
from sqlalchemy import func, select

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.domain.entities import Priority, TaskType
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import (
    ActivityEventModel,
    LinkModel,
    PlanModel,
    PlanSectionModel,
    TaskModel,
)
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import (
    checkout_service,
    doc_service,
    drift_gate_service,
    projection_service,
    repair_service,
)
from cod_doc.services.projection_service import DriftStatus

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

_PROJECT = "repsvc"

#: Заведомо неверный хэш в реестре MASTER.md: 12 hex-символов, которых
#: не даст ни один реальный файл.
_WRONG_HASH = "0123456789ab"

#: Возраст замка в фикстуре и две границы TTL вокруг него.
_LOCK_AGE_MIN = 60
_TTL_BELOW_AGE = 30
_TTL_ABOVE_AGE = 120

_ALPHA = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Alpha\n\n## Details\n\nBody.\n"
_BETA = "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Beta\n\nBeta body content.\n"


@pytest.fixture
def project(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[sessionmaker, Path]]:
    """(фабрика сессий, корень проекта) для пустого зарегистрированного проекта."""
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output

    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        yield factory, root
    finally:
        engine.dispose()


def _import_docs() -> None:
    result = CliRunner().invoke(main, ["import", "docs", _PROJECT])
    assert result.exit_code == 0, result.output


def _project_id(session: Session) -> int:
    row = ProjectRepository(session).get_by_slug(_PROJECT)
    assert row is not None and row.row_id is not None
    return row.row_id


def _doc_row_id(session: Session, doc_key: str) -> int:
    doc = next(
        d
        for d in doc_service.list_for_project(session, _project_id(session))
        if d.doc_key == doc_key
    )
    assert doc.row_id is not None
    return doc.row_id


def _diagnose(
    session: Session,
    root: Path,
    *,
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    skip_kinds: frozenset[str] = frozenset(),
) -> repair_service.RepairPlan:
    return repair_service.diagnose(
        session,
        project_id=_project_id(session),
        root_path=root,
        master_path=root / "MASTER.md",
        slug=_PROJECT,
        ttl_minutes=ttl_minutes,
        skip_kinds=skip_kinds,
    )


def _apply(
    session: Session,
    root: Path,
    *,
    dry_run: bool = False,
    ttl_minutes: int = repair_service.DEFAULT_TTL_MINUTES,
    skip_kinds: frozenset[str] = frozenset(),
) -> repair_service.RepairResult:
    return repair_service.apply(
        session,
        project_id=_project_id(session),
        root_path=root,
        master_path=root / "MASTER.md",
        slug=_PROJECT,
        ttl_minutes=ttl_minutes,
        dry_run=dry_run,
        author="cli:update",
        skip_kinds=skip_kinds,
    )


# ------------------------------------------------------------------ #
# edited_in_place → doc import                                        #
# ------------------------------------------------------------------ #


def test_edited_in_place_is_diagnosed_and_repaired(project) -> None:
    factory, root = project
    (root / "alpha.md").write_text(_ALPHA, encoding="utf-8")
    _import_docs()
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка мимо БД — ровно то, что ловит edited_in_place.\n")

    with transactional(factory, commit=False) as session:
        plan = _diagnose(session, root)
    assert plan.curable[repair_service.KIND_DOC_IMPORT] == 1
    assert [a.ref for a in plan.actions] == ["alpha.md"]

    with transactional(factory) as session:
        result = _apply(session, root)
    assert result.ok, result.errors
    assert result.applied == 1

    with transactional(factory, commit=False) as session:
        report = projection_service.detect_drift(
            session, _doc_row_id(session, "alpha"), root_path=root
        )
    assert report.status is DriftStatus.IN_SYNC


# ------------------------------------------------------------------ #
# Реестр хэшей MASTER.md                                              #
# ------------------------------------------------------------------ #


def _seed_stale_registry(root: Path) -> None:
    """MASTER.md с заведомо разошедшимся хэшем beta.md — ещё ДО импорта.

    Порядок важен: реестр, написанный после `import docs`, дал бы вдобавок
    `edited_in_place` на самом MASTER.md, и тест перестал бы быть про хэши.
    """
    (root / "beta.md").write_text(_BETA, encoding="utf-8")
    (root / "MASTER.md").write_text(
        f"# MASTER\n\n- **Ссылка:** 📁 /beta.md | 🗃️ doc:beta_md | 🔑 sha:{_WRONG_HASH}\n",
        encoding="utf-8",
    )
    _import_docs()


def test_dry_run_leaves_master_byte_identical(project) -> None:
    """`commit=False` не покрывает диск: `update_hashes` не должна звучать вовсе."""
    factory, root = project
    _seed_stale_registry(root)
    before = (root / "MASTER.md").read_bytes()

    with transactional(factory, commit=False) as session:
        result = _apply(session, root, dry_run=True)

    assert result.dry_run is True
    assert any(a.kind == repair_service.KIND_HASH_UPDATE for a in result.actions)
    assert result.applied == 0
    assert (root / "MASTER.md").read_bytes() == before


def test_apply_rewrites_the_stale_registry(project) -> None:
    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory) as session:
        result = _apply(session, root)

    assert result.ok, result.errors
    assert _WRONG_HASH not in (root / "MASTER.md").read_text(encoding="utf-8")

    with transactional(factory, commit=False) as session:
        plan = _diagnose(session, root)
    assert plan.curable[repair_service.KIND_HASH_UPDATE] == 0


def test_master_is_imported_only_after_the_registry_is_rewritten(project) -> None:
    """Протокол drift.md: hash update → doc import MASTER.md, не наоборот."""
    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory, commit=False) as session:
        kinds = [(a.kind, a.ref) for a in _diagnose(session, root).actions]

    assert kinds.index((repair_service.KIND_HASH_UPDATE, "MASTER.md")) < kinds.index(
        (repair_service.KIND_DOC_IMPORT, "MASTER.md")
    )


# ------------------------------------------------------------------ #
# Ссылки                                                              #
# ------------------------------------------------------------------ #


def _seed_links(root: Path) -> None:
    """alpha.md ссылается и на живой beta, и на несуществующий документ."""
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"
        "# Alpha\n\n## Details\n\nSee [[doc:beta]] and [[doc:nowhere]].\n",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(_BETA, encoding="utf-8")
    _import_docs()


def _resolved_links(factory: sessionmaker) -> int:
    with transactional(factory, commit=False) as probe:
        return int(
            probe.execute(
                select(func.count()).select_from(LinkModel).where(LinkModel.resolved.is_(True))
            ).scalar_one()
        )


def test_apply_persists_resolved_link_rows(project) -> None:
    factory, root = project
    _seed_links(root)

    with transactional(factory) as session:
        result = _apply(session, root)

    assert any(a.kind == repair_service.KIND_LINK_BACKFILL for a in result.actions)
    assert _resolved_links(factory) > 0


def test_dry_run_leaves_no_link_rows_behind(project) -> None:
    factory, root = project
    _seed_links(root)

    with transactional(factory, commit=False) as session:
        _apply(session, root, dry_run=True)

    assert _resolved_links(factory) == 0


# ------------------------------------------------------------------ #
# skip_kinds: выключить один вид, не выключая починку                 #
# ------------------------------------------------------------------ #


def _seed_links_and_drift(root: Path) -> None:
    """Ссылки (битая и живая) плюс правленый мимо БД beta.md."""
    _seed_links(root)
    with (root / "beta.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка мимо БД.\n")


def test_skip_links_reaches_the_diagnostician_not_only_the_runner(
    project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Обход всех секций не должен случиться ни в чинилке, ни в диагнозе."""
    factory, root = project
    _seed_links_and_drift(root)

    def _must_not_run(*_args: object, **_kwargs: object) -> tuple[int, int, int]:
        raise AssertionError("link backfill выключен — звать его нельзя")

    def _must_not_collect(*_args: object, **_kwargs: object) -> list[object]:
        raise AssertionError("сбор link-карточки выключен — обходить секции нельзя")

    monkeypatch.setattr(repair_service, "backfill_project_links", _must_not_run)
    monkeypatch.setattr(drift_gate_service, "link_findings", _must_not_collect)

    with transactional(factory) as session:
        result = _apply(session, root, skip_kinds=frozenset({repair_service.KIND_LINK_BACKFILL}))

    assert result.errors == []
    # Действия нет вовсе: находки не собраны, обещать их починку нечем.
    assert not [a for a in result.actions if a.kind == repair_service.KIND_LINK_BACKFILL]
    # Но отказ смотреть виден — это не «ссылки в порядке».
    assert result.not_collected == ["links"]
    assert result.as_dict()["not_collected"] == ["links"]

    # Выключен один вид, а не починка целиком.
    imported = [a for a in result.actions if a.kind == repair_service.KIND_DOC_IMPORT]
    assert imported and all(a.applied for a in imported)


def test_skipped_action_stays_visible_in_the_plan(project) -> None:
    """Для видов, чей диагноз всё равно собран, находка остаётся в отчёте."""
    factory, root = project
    task_id = _seed_stale_lock(factory)

    with transactional(factory) as session:
        result = _apply(
            session,
            root,
            ttl_minutes=_TTL_BELOW_AGE,
            skip_kinds=frozenset({repair_service.KIND_RELEASE_STALE}),
        )

    released = [a for a in result.actions if a.kind == repair_service.KIND_RELEASE_STALE]
    assert [a.ref for a in released] == [task_id]
    assert released[0].skipped is True
    assert released[0].applied is False
    assert "пропущено" in (released[0].detail or "")
    assert result.skipped == 1
    assert result.as_dict()["skipped"] == 1

    with transactional(factory, commit=False) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == task_id)).scalar_one()
        assert model.checked_out_by == "agent-X", "замок не тронут — вид выключен"


def test_skip_kinds_does_not_promise_what_it_will_not_do(project) -> None:
    factory, root = project
    _seed_stale_lock(factory)

    with transactional(factory, commit=False) as session:
        plan = _diagnose(
            session,
            root,
            ttl_minutes=_TTL_BELOW_AGE,
            skip_kinds=frozenset({repair_service.KIND_RELEASE_STALE}),
        )

    assert plan.curable[repair_service.KIND_RELEASE_STALE] == 0
    assert plan.skipped == 1
    assert any(a.kind == repair_service.KIND_RELEASE_STALE for a in plan.actions)


def test_default_skip_kinds_keeps_the_old_behaviour(project) -> None:
    factory, root = project
    _seed_links_and_drift(root)

    with transactional(factory) as session:
        result = _apply(session, root)

    assert result.skipped == 0
    assert result.not_collected == []
    assert any(a.kind == repair_service.KIND_LINK_BACKFILL and a.applied for a in result.actions)


def test_skipping_hash_update_drops_the_import_that_served_it(project) -> None:
    """Третий шаг протокола существует только ради результата второго."""
    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory, commit=False) as session:
        plan = _diagnose(session, root, skip_kinds=frozenset({repair_service.KIND_HASH_UPDATE}))

    hash_actions = [a for a in plan.actions if a.kind == repair_service.KIND_HASH_UPDATE]
    assert len(hash_actions) == 1
    assert hash_actions[0].skipped is True
    assert not [
        a for a in plan.actions if a.kind == repair_service.KIND_DOC_IMPORT and a.ref == "MASTER.md"
    ]


def test_unknown_skip_kind_is_a_programming_error(project) -> None:
    """Молча проигнорированный skip — тот же дефект, что и инертный флаг."""
    factory, root = project

    with (
        transactional(factory, commit=False) as session,
        pytest.raises(ValueError, match="skip_kinds"),
    ):
        _diagnose(session, root, skip_kinds=frozenset({"links"}))


# ------------------------------------------------------------------ #
# Протухшие замки                                                     #
# ------------------------------------------------------------------ #


def _seed_stale_lock(factory: sessionmaker) -> str:
    """Задача с замком возрастом ровно `_LOCK_AGE_MIN` минут."""
    task_id = "REP-001"
    with transactional(factory) as session:
        project_id = _project_id(session)
        plan = PlanModel(
            project_id=project_id,
            scope="rep-plan",
            created=datetime.now(UTC),
            last_updated=datetime.now(UTC),
        )
        session.add(plan)
        session.flush()
        section = PlanSectionModel(
            plan_id=plan.row_id, letter="A", title="Sec", slug="A-Sec", position=0
        )
        session.add(section)
        session.flush()

        from cod_doc.services import task_service

        task_service.create(
            session,
            project_id=project_id,
            plan_id=plan.row_id,
            section_id=section.row_id,
            task_id=task_id,
            title="Locked",
            type=TaskType.FEATURE,
            priority=Priority.MEDIUM,
            author="human:test",
        )
        checkout_service.checkout(session, task_id, agent="agent-X")
        model = session.execute(select(TaskModel).where(TaskModel.task_id == task_id)).scalar_one()
        model.checked_out_at = datetime.now(UTC) - timedelta(minutes=_LOCK_AGE_MIN)
    return task_id


def test_stale_lock_is_released_when_ttl_is_shorter_than_the_lock(project) -> None:
    factory, root = project
    task_id = _seed_stale_lock(factory)

    with transactional(factory) as session:
        result = _apply(session, root, ttl_minutes=_TTL_BELOW_AGE)

    released = [a for a in result.actions if a.kind == repair_service.KIND_RELEASE_STALE]
    assert [a.ref for a in released] == [task_id]
    assert released[0].applied is True

    with transactional(factory, commit=False) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == task_id)).scalar_one()
        assert model.checked_out_by is None


def test_fresh_enough_lock_is_left_alone(project) -> None:
    factory, root = project
    task_id = _seed_stale_lock(factory)

    with transactional(factory) as session:
        result = _apply(session, root, ttl_minutes=_TTL_ABOVE_AGE)

    assert not [a for a in result.actions if a.kind == repair_service.KIND_RELEASE_STALE]

    with transactional(factory, commit=False) as session:
        model = session.execute(select(TaskModel).where(TaskModel.task_id == task_id)).scalar_one()
        assert model.checked_out_by == "agent-X"


# ------------------------------------------------------------------ #
# Одобренный объём: чего чинилка не трогает никогда                   #
# ------------------------------------------------------------------ #


def test_stale_export_and_missing_are_reported_never_repaired(project) -> None:
    """Негативный тест объёма: обе находки дают ноль действий."""
    factory, root = project
    (root / "alpha.md").write_text(_ALPHA, encoding="utf-8")
    (root / "beta.md").write_text(_BETA, encoding="utf-8")
    _import_docs()

    with transactional(factory) as session:
        doc_service.add_section(
            session,
            document_id=_doc_row_id(session, "alpha"),
            anchor="extra",
            heading="Extra",
            level=2,
            position=1,
            body="В БД есть, на диске нет — это stale_export.",
            author="human:test",
        )
    (root / "beta.md").unlink()

    with transactional(factory, commit=False) as session:
        plan = _diagnose(session, root)

    assert plan.actions == []
    assert set(plan.curable.values()) == {0}
    assert plan.reported_only[DriftStatus.STALE_EXPORT.value] == 1
    assert plan.reported_only[DriftStatus.MISSING.value] == 1


def test_apply_on_the_reported_only_project_writes_no_file(project) -> None:
    """`doc export` не зовётся вовсе: пропавший файл так и остаётся пропавшим."""
    factory, root = project
    (root / "beta.md").write_text(_BETA, encoding="utf-8")
    _import_docs()
    (root / "beta.md").unlink()

    with transactional(factory) as session:
        result = _apply(session, root)

    assert result.ok, result.errors
    assert result.applied == 0
    assert not (root / "beta.md").exists()


# ------------------------------------------------------------------ #
# Изоляция ошибок и сводное событие                                   #
# ------------------------------------------------------------------ #


def test_one_broken_repairer_does_not_stop_the_rest(
    project, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, root = project
    _seed_stale_registry(root)
    with (root / "beta.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка мимо БД.\n")

    def _boom(_master_path: Path) -> tuple[int, list[str]]:
        raise RuntimeError("реестр не переписался")

    monkeypatch.setattr(repair_service, "update_hashes", _boom)

    with transactional(factory) as session:
        result = _apply(session, root)

    assert result.ok is False
    assert result.errors == ["hash_update MASTER.md: реестр не переписался"]

    by_kind = {(a.kind, a.ref): a for a in result.actions}
    assert by_kind[(repair_service.KIND_HASH_UPDATE, "MASTER.md")].error
    # Импорт правленого beta.md идёт до сломанной чинилки, импорт MASTER.md —
    # после неё. Обрыва нет ни там, ни там.
    assert by_kind[(repair_service.KIND_DOC_IMPORT, "beta.md")].applied is True
    assert by_kind[(repair_service.KIND_DOC_IMPORT, "MASTER.md")].applied is True


def _repaired_events(factory: sessionmaker) -> int:
    with transactional(factory, commit=False) as probe:
        return int(
            probe.execute(
                select(func.count())
                .select_from(ActivityEventModel)
                .where(ActivityEventModel.kind == "project.repaired")
            ).scalar_one()
        )


def test_apply_emits_exactly_one_summary_event(project) -> None:
    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory) as session:
        _apply(session, root)

    assert _repaired_events(factory) == 1


def test_dry_run_emits_nothing(project) -> None:
    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory, commit=False) as session:
        _apply(session, root, dry_run=True)

    assert _repaired_events(factory) == 0


# ------------------------------------------------------------------ #
# JSON-снимок                                                         #
# ------------------------------------------------------------------ #


def test_plan_as_dict_is_json_safe(project) -> None:
    """`update_service` сериализует план и в `--json`, и в resume-payload."""
    import json

    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory, commit=False) as session:
        payload = _diagnose(session, root).as_dict()

    assert json.loads(json.dumps(payload, ensure_ascii=False))["project"] == _PROJECT
    assert payload["curable"][repair_service.KIND_HASH_UPDATE] == 1
    assert len(payload["actions"]) == sum(payload["curable"].values())


def test_as_dict_is_json_safe(project) -> None:
    import json

    factory, root = project
    _seed_stale_registry(root)

    with transactional(factory) as session:
        payload = _apply(session, root).as_dict()

    assert json.loads(json.dumps(payload, ensure_ascii=False))["project"] == _PROJECT
    assert payload["ok"] is True
    assert payload["applied"] == len(payload["actions"])
