"""ADO-070: единая точка запуска alembic из тестов.

До этого 24 места в 23 файлах дублировали один и тот же блок: сборка команды из
``.venv/bin/alembic`` с фолбэком на голое имя ``alembic`` и запуск через
``subprocess.run`` с ``env={"PATH": "/usr/bin:/bin", ...}``.

В нём две ошибки, и обе видны только вне ноутбука разработчика:

1. Фолбэк на бинарь ``alembic`` по PATH. На CI-раннере нет ``.venv``, значит
   берётся фолбэк.
2. ``env`` **заменял** окружение целиком на ``PATH=/usr/bin:/bin``. Установленный
   ``alembic`` живёт не там, поэтому фолбэк не находился никогда — даже когда
   пакет стоит. Отсюда ``FileNotFoundError: [Errno 2] ... 'alembic'`` и массовые
   ERROR на setup фикстур: CI на main не был зелёным ни разу с 2026-05-06.

``sys.executable -m alembic`` не зависит от PATH вообще — он запускает alembic
тем же интерпретатором, что и pytest. Окружение наследуется (а не собирается
с нуля), поэтому в подпроцесс попадает и ``COD_DOC_HOME``, подменённый
autouse-фикстурой в ``tests/conftest.py`` — подпроцесс больше не ходит в
настоящий домашний каталог пользователя.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess[bytes]:
    """Выполнить ``alembic <args>`` против ``db_url``.

    Пример вызова — ``upgrade`` до последней ревизии с явным URL базы.
    Бросает ``CalledProcessError`` при ненулевом коде возврата.
    """
    env = {**os.environ, "COD_DOC_DB_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        check=True,
        env=env,
        capture_output=True,
    )
