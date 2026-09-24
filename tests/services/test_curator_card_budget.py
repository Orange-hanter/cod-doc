"""AFT-002 (RFC 27 F1): карточка куратора укладывается в 8 КБ.

`curator_next(project="cod-doc")` весил ≈25 КБ при восьми пунктах очереди:
`in_sync`-строки с расхождением frontmatter шли в `card.drift.issues` целиком,
а тела четырёх скиллов инлайнились безусловно. Фикстура воспроизводит первую
причину в масштабе: 40 документов, из них 30 — `in_sync` с `status: resolved`,
которого нет в enum (ADO-092). Импорт хранит fallback, файл — своё, хэши
содержимого совпадают, и только `metadata_mismatch` знает о расхождении.

Вторая причина всплыла на живом корпусе уже после первой правки: карточка
весила 9.5 КБ, а эта фикстура — меньше 8, потому что в ней не было битых
ссылок, а ключи и якоря были короче живых (`doc-00`, `executive-summary`).
Поэтому пять «чистых» документов ссылаются на несуществующие вики-цели, а
ключи и кириллические заголовки — в масштабе `docs/system/audit/…`.

Размер меряется так, как ответ печатает `ctx next --json` и сериализует
FastMCP: UTF-8, `indent=2`, кириллица без экранирования.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest

from cod_doc.infra.db import make_engine, make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import curator_service, import_service
from tests._alembic import run_alembic

if TYPE_CHECKING:
    from pathlib import Path

#: Бюджет ответа `curator_next`. Литерал нарочно: импорт из
#: `curator_service` подвинул бы планку вместе с кодом, который она стережёт.
CURATOR_CARD_BUDGET_BYTES = 8192

SLUG = "budget-proj"

_ADVISORY_DOCS = 30
_CLEAN_DOCS = 10
_BROKEN_LINK_DOCS = 5

_RESOLVED = """---
title: Doc {n}
type: guide
status: resolved
owner: human:dakh
---
# Doc {n}

Preamble.

## 1. Итоги спринта и обратная связь пилотов

Summary text.
"""

_CLEAN = """---
title: Doc {n}
type: guide
status: active
owner: human:dakh
---
# Doc {n}

Preamble.

## 1. Итоги спринта и обратная связь пилотов

Summary text.{link}
"""


def _sha(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@pytest.fixture
def budget_payload(tmp_path: Path) -> dict[str, Any]:
    """Карточка куратора на корпусе из 40 документов (30 advisory, 10 чистых)."""
    repo = tmp_path / "repo"
    (repo / ".cod-doc").mkdir(parents=True)
    db_path = repo / ".cod-doc" / "state.db"
    run_alembic("upgrade", "head", db_url=f"sqlite:///{db_path}")

    engine = make_engine(f"sqlite:///{db_path}")
    factory = make_session_factory(engine)
    try:
        with transactional(factory) as session:
            now = datetime.now(UTC)
            project = ProjectModel(slug=SLUG, title="Budget", root_path=str(repo), config_json={})
            project.created = now
            project.updated = now
            session.add(project)
            session.flush()

            for n in range(_ADVISORY_DOCS + _CLEAN_DOCS):
                if n < _ADVISORY_DOCS:
                    raw = _RESOLVED.format(n=n)
                else:
                    broken = n - _ADVISORY_DOCS < _BROKEN_LINK_DOCS
                    link = f" См. [[missing-target-{n}]]." if broken else ""
                    raw = _CLEAN.format(n=n, link=link)
                # Ключи и кириллические якоря — в масштабе живого корпуса:
                # на коротких `doc-00` карточка легче реальной и тест молчит.
                key = f"docs/system/audit/2026-09-{n:02d}-sprint-feedback-loop"
                path = f"{key}.md"
                report = import_service.import_or_update_markdown(
                    session,
                    project_id=project.row_id,
                    doc_key=key,
                    raw_markdown=raw,
                    author="human:test",
                    source_sha256=_sha(raw),
                    path=path,
                )
                model = session.get(DocumentModel, report.document.row_id)
                assert model is not None
                model.path = path
                (repo / path).parent.mkdir(parents=True, exist_ok=True)
                (repo / path).write_text(raw, encoding="utf-8")
            project_id = project.row_id

        with transactional(factory, commit=False) as session:
            return curator_service.next(
                session,
                project_id=project_id,
                root_path=repo,
                master_path=repo / "MASTER.md",
                project_slug=SLUG,
            )
    finally:
        engine.dispose()


def test_curator_card_under_8kb(budget_payload: dict[str, Any]) -> None:
    size = len(
        json.dumps(budget_payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    )
    assert size <= CURATOR_CARD_BUDGET_BYTES, f"карточка {size} байт > {CURATOR_CARD_BUDGET_BYTES}"


def test_curator_card_advisory_is_summarised(budget_payload: dict[str, Any]) -> None:
    drift = budget_payload["card"]["drift"]
    counts = budget_payload["meta"]["counts"]

    assert drift["issues"] == []
    assert drift["advisory"]["count"] == 30
    assert len(drift["advisory"]["doc_keys"]) == 10
    assert counts["drift_advisory"] == 30
    assert counts["drift_issues"] == 0


def test_curator_card_budget_fixture_has_broken_links(budget_payload: dict[str, Any]) -> None:
    """Без битых ссылок бюджет-тест не видит их веса — ровно так он и промахнулся."""
    assert budget_payload["meta"]["counts"]["links"] == _BROKEN_LINK_DOCS


def test_curator_card_link_rows_are_compact(budget_payload: dict[str, Any]) -> None:
    """title — в priority[].reason, path — из doc_key, anchor — в body и priority[].ref."""
    links = budget_payload["card"]["links"]
    assert links
    for row in links:
        assert set(row) == {"doc_key", "code", "body"}
        assert row["body"]
    reasons = {p["reason"] for p in budget_payload["priority"] if p["kind"] == "link"}
    assert len(reasons) == _BROKEN_LINK_DOCS, "очередь потеряла пункты по ссылкам"
    assert all("missing-target" in r for r in reasons)


def test_curator_card_skill_descriptions_are_one_liners(budget_payload: dict[str, Any]) -> None:
    for skill in budget_payload["navigation"]["applicable_skills"]:
        assert "body" not in skill
        assert len(skill["description"]) <= 60
        assert "Триггеры" not in skill["description"]
