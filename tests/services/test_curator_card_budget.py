"""AFT-002 (RFC 27 F1): карточка куратора укладывается в 8 КБ.

`curator_next(project="cod-doc")` весил ≈25 КБ при восьми пунктах очереди:
`in_sync`-строки с расхождением frontmatter шли в `card.drift.issues` целиком,
а тела четырёх скиллов инлайнились безусловно. Фикстура воспроизводит первую
причину в масштабе: 40 документов, из них 30 — `in_sync` с `status: resolved`,
которого нет в enum (ADO-092). Импорт хранит fallback, файл — своё, хэши
содержимого совпадают, и только `metadata_mismatch` знает о расхождении.

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

_RESOLVED = """---
title: Doc {n}
type: guide
status: resolved
owner: human:dakh
---
# Doc {n}

Preamble.

## Executive Summary

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

## Executive Summary

Summary text.
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
                template = _RESOLVED if n < _ADVISORY_DOCS else _CLEAN
                raw = template.format(n=n)
                path = f"doc-{n:02d}.md"
                report = import_service.import_or_update_markdown(
                    session,
                    project_id=project.row_id,
                    doc_key=f"doc-{n:02d}",
                    raw_markdown=raw,
                    author="human:test",
                    source_sha256=_sha(raw),
                    path=path,
                )
                model = session.get(DocumentModel, report.document.row_id)
                assert model is not None
                model.path = path
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
