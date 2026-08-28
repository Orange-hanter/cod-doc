"""Инициализация глобальной hub-БД COD-DOC.

Hub — это мультипроектный реестр, хранящийся в ``~/.cod-doc/hub.db``
(или в ``$COD_DOC_HOME/hub.db``). Схема та же, что и у проектных БД:
миграции берутся из ``cod_doc.infra.migrations``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import command as alembic_command

from cod_doc import config
from cod_doc.services.project_service import _alembic_config_for

if TYPE_CHECKING:
    from pathlib import Path


def hub_db_path() -> Path:
    """Путь к файлу hub-БД с учётом ``COD_DOC_HOME``."""
    return config.CONFIG_DIR / "hub.db"


def init_hub() -> Path:
    """Идемпотентно создать и мигрировать ``hub.db``.

    Возвращает путь к файлу БД.
    """
    db_path = hub_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_url = f"sqlite:///{db_path}"
    cfg = _alembic_config_for(db_url)
    alembic_command.upgrade(cfg, "head")
    return db_path
