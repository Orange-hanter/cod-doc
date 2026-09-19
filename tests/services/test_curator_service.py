"""CUR-016 (RFC 25 §3.5): doc card куратора — `curator_service.next`.

Проверяется то, ради чего карточка и делалась: четыре источника санитарии
сведены в одну очередь, порядок в ней фиксирован (сначала то, что делает
документ недоступным), а `limit` режет хвост честно — с отметкой в `meta`.

Фикстура строится через CLI (`project add` + `import docs`), как и тесты
drift-гейта: только так на диске оказываются настоящие файлы, по которым
`detect_project_drift` и считает дрейф.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.repositories import ProjectRepository
from cod_doc.services import curator_service

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session

_PROJECT = "curnext"

#: Заведомо неверный хэш в реестре MASTER.md: 12 hex-символов, которых
#: не даст ни один реальный файл.
_WRONG_HASH = "0123456789ab"


def _init_project(tmp_path: Path) -> Path:
    root = tmp_path / _PROJECT
    root.mkdir()
    result = CliRunner().invoke(main, ["project", "add", str(root), "--name", _PROJECT])
    assert result.exit_code == 0, result.output
    return root


def _import_docs(root: Path) -> None:
    """alpha.md — с нерезолвящейся ссылкой; beta.md — чистый."""
    (root / "alpha.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n"
        "# Alpha\n\n## Details\n\nSee [[doc:missing]].\n",
        encoding="utf-8",
    )
    (root / "beta.md").write_text(
        "---\ntype: standard\nstatus: active\nowner: dakh\n---\n# Beta\n\nBeta body content.\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(main, ["import", "docs", _PROJECT])
    assert result.exit_code == 0, result.output


def _write_master_with_stale_ref(root: Path) -> None:
    """Реестр гибридных ссылок, где хэш beta.md заведомо разошёлся."""
    (root / "MASTER.md").write_text(
        f"# MASTER\n\n- **Ссылка:** 📁 /beta.md | 🗃️ doc:beta_md | 🔑 sha:{_WRONG_HASH}\n",
        encoding="utf-8",
    )


@pytest.fixture
def curator_session(tmp_path: Path, isolated_cod_doc_home: Path) -> Iterator[tuple[Session, Path]]:
    """(сессия, корень проекта) для пустого зарегистрированного проекта."""
    root = _init_project(tmp_path)
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)
    try:
        with transactional(factory, commit=False) as session:
            yield session, root
    finally:
        engine.dispose()


def _project_id(session: Session) -> int:
    project = ProjectRepository(session).get_by_slug(_PROJECT)
    assert project is not None and project.row_id is not None
    return project.row_id


def _call(session: Session, root: Path, *, limit: int = 10) -> dict:
    return curator_service.next(
        session,
        project_id=_project_id(session),
        root_path=root,
        master_path=root / "MASTER.md",
        limit=limit,
        project_slug=_PROJECT,
    )


# ------------------------------------------------------------------ #
# Пустой проект                                                       #
# ------------------------------------------------------------------ #


def test_empty_project_gives_empty_card_and_queue(curator_session) -> None:
    session, root = curator_session
    payload = _call(session, root)

    assert payload["card"]["drift"]["issues"] == []
    assert payload["card"]["links"] == []
    assert payload["card"]["master"] == []
    assert payload["card"]["findings"] == []
    assert payload["priority"] == []
    assert payload["meta"]["truncated"] is False
    assert payload["meta"]["counts"]["priority_total"] == 0


def test_empty_project_still_carries_navigation(curator_session) -> None:
    """Скиллы и критерии успеха — не функция от находок, они всегда есть."""
    session, root = curator_session
    nav = _call(session, root)["navigation"]

    names = [s["name"] for s in nav["applicable_skills"]]
    assert names[0] == "orchestrator", "orchestrator обязан идти первым"
    assert names == ["orchestrator", "drift-handling", "ground-truth-reconcile", "doc-style"]
    assert all(s["body"] for s in nav["applicable_skills"]), "тела скиллов инлайнятся"
    assert nav["next_actions"] and nav["success_criteria"]


# ------------------------------------------------------------------ #
# Очередь: порядок и содержимое                                       #
# ------------------------------------------------------------------ #


def _seed_all_three(root: Path) -> None:
    """edited_in_place (alpha.md) + LINK-BROKEN (alpha.md) + STALE (beta.md)."""
    _import_docs(root)
    _write_master_with_stale_ref(root)
    with (root / "alpha.md").open("a", encoding="utf-8") as fh:
        fh.write("\nПравка мимо БД — ровно то, что ловит edited_in_place.\n")


def test_priority_order_is_drift_then_link_then_master(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    payload = _call(session, root)
    kinds = [item["kind"] for item in payload["priority"]]

    assert kinds, "очередь не может быть пустой на подготовленном дрейфе"
    assert "drift" in kinds and "link" in kinds and "master" in kinds
    # Порядок из RFC 25 §3.5: edited_in_place → LINK-BROKEN → hash STALE.
    assert kinds.index("drift") < kinds.index("link") < kinds.index("master")


def test_drift_item_suggests_doc_import_with_the_slug(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    priority = _call(session, root)["priority"]
    drift_item = next(i for i in priority if i["kind"] == "drift" and i["ref"] == "alpha")
    assert drift_item["suggested_action"] == f"cod-doc doc import alpha.md -p {_PROJECT}"
    assert "не доехала до БД" in drift_item["reason"]


def test_link_item_points_at_the_section_anchor(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    link_item = next(i for i in _call(session, root)["priority"] if i["kind"] == "link")
    assert link_item["ref"].startswith("alpha#")
    assert "link_verify(" in link_item["suggested_action"]
    assert f'project="{_PROJECT}"' in link_item["suggested_action"]


def test_master_item_suggests_hash_update(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    master_item = next(i for i in _call(session, root)["priority"] if i["kind"] == "master")
    assert master_item["ref"] == "beta.md"
    assert master_item["suggested_action"] == "cod-doc hash update MASTER.md"
    assert _WRONG_HASH in master_item["reason"]


def test_master_broken_ref_outranks_stale_one(curator_session) -> None:
    """BROKEN (файла нет) обязан стоять выше STALE (файл есть, хэш устарел)."""
    session, root = curator_session
    _import_docs(root)
    (root / "MASTER.md").write_text(
        "# MASTER\n\n"
        f"- 📁 /beta.md | 🗃️ doc:beta_md | 🔑 sha:{_WRONG_HASH}\n"
        f"- 📁 /gone.md | 🗃️ doc:gone_md | 🔑 sha:{_WRONG_HASH}\n",
        encoding="utf-8",
    )

    master_items = [i for i in _call(session, root)["priority"] if i["kind"] == "master"]
    assert [i["ref"] for i in master_items] == ["gone.md", "beta.md"]


# ------------------------------------------------------------------ #
# limit / truncated                                                   #
# ------------------------------------------------------------------ #


def test_limit_truncates_the_queue_and_says_so(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    full = _call(session, root)
    total = full["meta"]["counts"]["priority_total"]
    assert total > 1, "фикстура обязана давать больше одного пункта"

    cut = _call(session, root, limit=1)
    assert len(cut["priority"]) == 1
    assert cut["meta"]["truncated"] is True
    assert cut["meta"]["counts"]["priority_total"] == total
    # Карточка — полный срез: её `limit` не режет.
    assert len(cut["card"]["drift"]["issues"]) == len(full["card"]["drift"]["issues"])

    assert full["meta"]["truncated"] is False


def test_card_counts_match_the_card_itself(curator_session) -> None:
    session, root = curator_session
    _seed_all_three(root)

    payload = _call(session, root)
    counts = payload["meta"]["counts"]
    card = payload["card"]

    assert counts["drift_issues"] == len(card["drift"]["issues"])
    assert counts["links"] == len(card["links"])
    assert counts["master"] == len(card["master"])
    assert counts["findings"] == len(card["findings"])


def test_drift_half_keeps_the_ctx_drift_shape(curator_session) -> None:
    """Потребитель, умеющий читать `ctx_drift`, читает и половину карточки."""
    session, root = curator_session
    _seed_all_three(root)

    drift = _call(session, root)["card"]["drift"]
    assert set(drift) == {"total_docs", "problem_count", "counts", "issues"}
    assert drift["total_docs"] >= 2
    for issue in drift["issues"]:
        assert set(issue) == {
            "doc_key",
            "path",
            "status",
            "projection_hash",
            "db_content_hash",
            "file_hash",
        }


def test_next_writes_nothing(tmp_path: Path, isolated_cod_doc_home: Path) -> None:
    """Read-only: ``commit=False`` обязан откатить всё, что тронул сборщик.

    Мерить в той же сессии нельзя: ``link_service.resolve_section``
    синхронизирует derived-таблицу ``link`` и пишет события — они видны
    внутри транзакции и исчезают на rollback. Поэтому счётчики снимаются
    в отдельных сессиях, до и после.
    """
    from sqlalchemy import func, select

    from cod_doc.infra.models import ActivityEventModel, LinkModel, RevisionModel

    root = _init_project(tmp_path)
    _seed_all_three(root)
    entry = Config.load().get_project(_PROJECT)
    assert entry is not None
    factory, engine = db_for_entry(entry)

    def _counts() -> tuple[int, int, int]:
        with transactional(factory, commit=False) as probe:
            return (
                probe.execute(select(func.count()).select_from(RevisionModel)).scalar_one(),
                probe.execute(select(func.count()).select_from(ActivityEventModel)).scalar_one(),
                probe.execute(select(func.count()).select_from(LinkModel)).scalar_one(),
            )

    try:
        before = _counts()
        with transactional(factory, commit=False) as session:
            payload = _call(session, root)
        assert payload["meta"]["counts"]["priority_total"] > 0
        assert _counts() == before
    finally:
        engine.dispose()
