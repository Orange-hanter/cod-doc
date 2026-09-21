"""0037: один словарь статусов в `task.status` — легаси-написания сведены в канон.

В одной колонке жили два словаря сразу. `task_service.create()` жёстко писал
`pending`, `checkout_service` переводил такую задачу в `in-progress`, а
задачу из `todo` — в `in_progress`; `task_update_status` принимал любое
написание и клал его as-is. Написание статуса определялось тем, когда задачу
создали, а не тем, в каком она состоянии: в БД Restate на 2026-09-20 лежало
407 `pending` против 1 `todo` при одинаковом смысле.

Правка кода без бэкфилла не закрывает вопрос, а делает хуже: с этого момента
новые задачи канонические, старые легаси, и каждое место, сравнивающее
статус точной строкой, отвечает на половину базы. 0035 закрыл это для трёх
view'ов классом эквивалентности; здесь закрывается сама колонка.

Порядок с `0035_totals_status_aliases` обязателен. До него `ready_tasks` —
это `WHERE status = 'pending'`, единственный источник ready-множества для
`plan_ready`, `task_next_ready` и блока Next Batch в экспорте планов. Бэкфилл
под старой вьюхой погасил бы всё ready-множество молча, без единой ошибки —
поэтому `down_revision` указывает именно на неё, а не на общий head.

Номер 0037, а не 0036: пока PR #68 висел, main ушёл вперёд на две ревизии
(`0035_doc_nodes`, `0036_finding_miss_streak`), и 0036 уже занят.

Литералы захардкожены намеренно: миграция — застывший снимок мира на своей
ревизии и не должна ехать вместе с `domain.entities.TASK_STATUS_ALIASES`.
Совпадение карты с этими литералами на момент ревизии проверяет
`tests/infra/test_task_status_canonicalisation_migration.py`.

ADO-040 («write-path обязан оставлять след») сюда не распространяется, и это
структурно, а не поблажка. `tests/services/test_activity_write_path.py`
перечисляет модули сервисов руками — миграция не сервис; прецедент прямой:
`0033_document_status_recoercion` переписал `document.status` на живых
строках без ревизий и событий. По существу — хуже: 408 ревизий пришлось бы
подписать несуществующим автором (а `actor_kind` выводится ТОЛЬКО из
`author`, ADR-012), после чего `revision_service.revert` на такой ревизии
вернул бы задачу в `pending` — бэкфилл раздал бы каждой задаче кнопку
«откатить в легаси». Провенанс бэкфилла несёт `alembic_version`.

История не переписывается: `revision.diff` и `activity_event.payload`
сохраняют свои `"pending"` — это запись о том, что было правдой тогда.

`downgrade()` лоссовый, и это единственный доступный выбор. До бэкфилла в
Restate 407 `pending` И 1 `todo`; после — 408 `todo`, и оракула, который
скажет, кто из них родился каноническим, здесь нет (у 0033 для этого был
`frontmatter_json`). Откат возвращает в легаси-написание ВЕСЬ бакет, включая
задачи, которые канонической строкой и были. No-op downgrade хуже: он оставил
бы `todo` под вернувшейся вьюхой 0027, которая ловит только `pending`, — то
есть сам откат и погасил бы ready-множество, ради которого делается порядок
с 0035.

Данные, не схема: `task.status` — `VARCHAR` без CHECK и без database-ENUM,
DDL не меняется.

Revision ID: 0037_task_status_canonicalisation
Revises: 0035_totals_status_aliases
Create Date: 2026-09-20
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0037_task_status_canonicalisation"
down_revision: str | None = "0035_totals_status_aliases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: Легаси-написание → канонический бакет, копия `TASK_STATUS_ALIASES` на
#: момент этой ревизии. Копия, а не импорт: см. шапку.
LEGACY_TO_CANONICAL: dict[str, str] = {
    "pending": "todo",
    "in-progress": "in_progress",
}

#: Колонки, в которых лежит написание статуса задачи.
#: `expected_status_at_checkout` — снимок статуса ДО чекаута
#: (`checkout_service`), то есть та же строка из того же словаря; оставить её
#: легаси значило бы вернуть задачу в `pending` на первом же `release`.
STATUS_COLUMNS: tuple[str, ...] = ("status", "expected_status_at_checkout")


def _rewrite(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    for column in STATUS_COLUMNS:
        for source, target in mapping.items():
            # Имя колонки — из константы модуля, не из данных; значения
            # статуса связаны как параметры, а не вклеены в текст.
            bind.execute(
                sa.text(f"UPDATE task SET {column} = :target WHERE {column} = :source"),
                {"target": target, "source": source},
            )


def upgrade() -> None:
    """Свести легаси-написания к каноническим бакетам."""
    _rewrite(LEGACY_TO_CANONICAL)


def downgrade() -> None:
    """Вернуть бакет целиком в легаси-написание — лоссово, см. шапку."""
    _rewrite({canonical: legacy for legacy, canonical in LEGACY_TO_CANONICAL.items()})
