"""0037: бэкфилл `task.status` — и машинная проверка порядка с 0035.

Интересный случай здесь не «UPDATE отработал», а **что бэкфилл не гасит
ready-множество**. До `0035_totals_status_aliases` view `ready_tasks` — это
`WHERE t.status = 'pending'`, единственный источник готовых задач для
`plan_ready`, `task_next_ready` и блока Next Batch в экспорте планов.
Перевести данные в `todo` под такой вьюхой значит молча обнулить очередь: ни
ошибки, ни исключения, просто ноль строк. Поэтому тест сравнивает набор
`ready_tasks` ДО и ПОСЛЕ апгрейда — это и есть проверка того, что
`down_revision` указывает на 0035, а не на что-то раньше.

Строки наливаются сырым SQL на предыдущей ревизии: сервисный слой после
ADO-156 легаси-написание уже не пишет, и через него такую базу не собрать.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from cod_doc.domain.entities import TASK_STATUS_ALIASES
from cod_doc.infra.db import make_engine
from tests._alembic import run_alembic

BEFORE = "0035_totals_status_aliases"

_MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "cod_doc"
    / "infra"
    / "migrations"
    / "versions"
    / "20260920_0037_task_status_canonicalisation.py"
)


def _migration_module() -> Any:  # noqa: ANN401
    """Импорт по пути: имя файла начинается с цифры, обычный import невозможен."""
    spec = importlib.util.spec_from_file_location("_m0037", _MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

#: Одна задача на каждое написание плюс контрольная в `done`.
SEEDED: dict[str, str] = {
    "T-001": "pending",
    "T-002": "todo",
    "T-003": "in-progress",
    "T-004": "in_progress",
    "T-005": "done",
}


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'canon.db'}"


def _seed(db_url: str) -> None:
    now = datetime.now(UTC).isoformat()
    engine = make_engine(db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO project (slug, title, root_path, config_json, created, updated) "
                    "VALUES ('p', 'P', '/tmp/p', '{}', :now, :now)"
                ),
                {"now": now},
            )
            project_id = conn.execute(text("SELECT row_id FROM project WHERE slug = 'p'")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO plan (project_id, scope, created, last_updated) "
                    "VALUES (:pid, 'p-plan', :now, :now)"
                ),
                {"pid": project_id, "now": now},
            )
            plan_id = conn.execute(text("SELECT row_id FROM plan WHERE scope = 'p-plan'")).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO plan_section (plan_id, letter, title, slug, position) "
                    "VALUES (:plan, 'A', 'Sec', 'A-Sec', 0)"
                ),
                {"plan": plan_id},
            )
            section_id = conn.execute(text("SELECT row_id FROM plan_section")).scalar_one()
            for task_id, status in SEEDED.items():
                conn.execute(
                    text(
                        "INSERT INTO task (project_id, task_id, plan_id, section_id, title, "
                        "status, type, priority, created, last_updated, "
                        "expected_status_at_checkout) "
                        "VALUES (:pid, :tid, :plan, :sec, :title, :status, 'feature', 'medium', "
                        ":now, :now, :status)"
                    ),
                    {
                        "pid": project_id,
                        "tid": task_id,
                        "plan": plan_id,
                        "sec": section_id,
                        "title": task_id,
                        "status": status,
                        "now": now,
                    },
                )
    finally:
        engine.dispose()


def _column(db_url: str, column: str) -> dict[str, str]:
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            return {
                str(task_id): str(value)
                for task_id, value in conn.execute(text(f"SELECT task_id, {column} FROM task"))
            }
    finally:
        engine.dispose()


def _ready(db_url: str) -> set[str]:
    engine = make_engine(db_url)
    try:
        with engine.connect() as conn:
            return {str(row[0]) for row in conn.execute(text("SELECT task_id FROM ready_tasks"))}
    finally:
        engine.dispose()


@pytest.fixture
def seeded_pre_0037(db_url: str) -> str:
    run_alembic("upgrade", BEFORE, db_url=db_url)
    _seed(db_url)
    return db_url


def test_upgrade_canonicalises_every_legacy_spelling(seeded_pre_0037: str) -> None:
    run_alembic("upgrade", "head", db_url=seeded_pre_0037)
    statuses = _column(seeded_pre_0037, "status")
    assert statuses == {
        "T-001": "todo",
        "T-002": "todo",
        "T-003": "in_progress",
        "T-004": "in_progress",
        "T-005": "done",
    }


def test_upgrade_canonicalises_the_checkout_snapshot_column(seeded_pre_0037: str) -> None:
    """`expected_status_at_checkout` — та же строка из того же словаря.

    Оставить её легаси значило бы вернуть задачу в `pending` на первом же
    `release`, откатывающем статус к снимку.
    """
    run_alembic("upgrade", "head", db_url=seeded_pre_0037)
    snapshots = _column(seeded_pre_0037, "expected_status_at_checkout")
    assert not set(snapshots.values()) & set(TASK_STATUS_ALIASES)


def test_ready_set_survives_the_backfill(seeded_pre_0037: str) -> None:
    """Главная проверка порядка с 0035: очередь готовых задач не гаснет."""
    before = _ready(seeded_pre_0037)
    assert before == {"T-001", "T-002"}, "на 0035 ready_tasks обязана видеть оба написания"

    run_alembic("upgrade", "head", db_url=seeded_pre_0037)

    assert _ready(seeded_pre_0037) == before


def test_downgrade_returns_the_whole_bucket_to_the_legacy_spelling(seeded_pre_0037: str) -> None:
    """Откат лоссовый по построению: `todo`, который таким и родился, тоже уедет.

    Восстановить, кто из бакета был каноническим до бэкфилла, нечем —
    оракула вроде `frontmatter_json` у 0033 здесь нет. Тест фиксирует
    именно это поведение, чтобы «починка» симметрии не прошла молча.
    """
    run_alembic("upgrade", "head", db_url=seeded_pre_0037)
    run_alembic("downgrade", "-1", db_url=seeded_pre_0037)

    statuses = _column(seeded_pre_0037, "status")
    assert statuses == {
        "T-001": "pending",
        "T-002": "pending",
        "T-003": "in-progress",
        "T-004": "in-progress",
        "T-005": "done",
    }


def test_hardcoded_map_matches_the_alias_map_at_this_revision() -> None:
    """Литералы миграции захардкожены намеренно — но на своей ревизии обязаны совпадать.

    Расхождение значит одно из двух: в `TASK_STATUS_ALIASES` появился новый
    алиас (тогда ему нужна СВОЯ миграция, а не правка этой) или кто-то
    подправил застывший снимок задним числом.
    """
    assert _migration_module().LEGACY_TO_CANONICAL == dict(TASK_STATUS_ALIASES)
