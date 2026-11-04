"""0057: роль ``responder`` в ``project_member``.

PO/бизнес-аналитик отвечает на вопросы проекта, не получая прав на всё
остальное: ``responder`` между ``reader`` и ``writer`` — читает всё, пишет
только ответы (``question_service.resolve`` / ``add_option``, проверка —
``request_identity.check_question_answer``). Роль ``reader`` получила
адресацию: ответ на вопрос, где он ``owner``.

CHECK-констрейнт SQLite не умеет менять — ``batch_alter_table`` пересобирает
таблицу. ``copy_from`` задаёт определение целиком: отражение CHECK в SQLite
ненадёжно, а потерять констрейнт при пересборке — молчаливая дыра.
``actor_uid`` держит FK с ``ON DELETE CASCADE``: batch снимает
``PRAGMA foreign_keys`` на время перестройки, участники переживают её
(проверено в 0036 на аналогичной связке).

Downgrade симметричен, только сначала сужает данные: ``responder`` → ``reader``
(старый констрейнт responder-строку не пустил бы).

Revision ID: 0057_responder_role
Revises: 0056_oidc_web_login
Create Date: 2026-11-04
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

revision: str = "0057_responder_role"
down_revision: str | None = "0056_oidc_web_login"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACTOR_FK = "actor.uid"


def _member_table(roles: tuple[str, ...]) -> sa.Table:
    # Колонки — свежие на каждый вызов: один Column не может принадлежать
    # двум таблицам (_OLD и _NEW).
    return sa.Table(
        "project_member",
        sa.MetaData(),
        sa.Column("project_uid", sa.Text(), primary_key=True),
        sa.Column(
            "actor_uid", sa.Text(), sa.ForeignKey(_ACTOR_FK, ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("role", sa.Text(), nullable=False),
        sa.CheckConstraint(f"role IN {roles}", name="ck_project_member_role"),
    )


_OLD = _member_table(("reader", "writer", "admin"))
_NEW = _member_table(("reader", "responder", "writer", "admin"))


def upgrade() -> None:
    with op.batch_alter_table("project_member", copy_from=_OLD) as batch:
        batch.drop_constraint("ck_project_member_role", type_="check")
        batch.create_check_constraint(
            "ck_project_member_role", "role IN ('reader','responder','writer','admin')"
        )


def downgrade() -> None:
    op.execute("UPDATE project_member SET role = 'reader' WHERE role = 'responder'")
    with op.batch_alter_table("project_member", copy_from=_NEW) as batch:
        batch.drop_constraint("ck_project_member_role", type_="check")
        batch.create_check_constraint(
            "ck_project_member_role", "role IN ('reader','writer','admin')"
        )
