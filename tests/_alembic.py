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

Кэш шаблонной БД
----------------

``upgrade head`` на пустой SQLite — самая частая операция прогона: её зовут
почти все ~900 тестов сервисного слоя плюс фикстуры API и CLI. Каждый вызов
стоил ~0.5 с (старт интерпретатора + 34 миграции подряд) — несколько минут
чистого ожидания на прогон.

Результат при этом всегда один и тот же: пустая база на голове. Поэтому
``upgrade head`` по несуществующему файлу SQLite обслуживается копией шаблона,
который строится настоящим alembic'ом один раз за процесс. Копия — это
``shutil.copyfile``, порядка миллисекунды, и тест по-прежнему получает
собственный свежий файл в своём ``tmp_path``.

Шорткат нарочно узкий и не трогает то, ради чего alembic зовут по-настоящему:
любая другая ревизия, ``downgrade``, non-SQLite URL и уже существующий файл
(миграционные тесты сначала наливают данные на старой ревизии, потом гонят
upgrade) идут в подпроцесс, как и раньше. Шаблон инвалидируется по
размеру/mtime файлов ``versions/`` — правка миграции не прилетает из кэша.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSIONS_DIR = REPO_ROOT / "cod_doc" / "infra" / "migrations" / "versions"

_SQLITE_PREFIX = "sqlite:///"
# Файлы-спутники WAL. После чистого закрытия соединения их нет, но если шаблон
# почему-то остался в WAL — копируем базу целиком, а не половину.
_SQLITE_SIDECARS = ("-wal", "-shm")

_template_db: Path | None = None
_template_key: tuple[tuple[str, int, int], ...] | None = None
_template_dir: Path | None = None


def _migrations_fingerprint() -> tuple[tuple[str, int, int], ...]:
    """Отпечаток каталога миграций: (имя, размер, mtime_ns) по каждому файлу."""
    return tuple(
        (path.name, path.stat().st_size, path.stat().st_mtime_ns)
        for path in sorted(VERSIONS_DIR.glob("*.py"))
    )


def _sqlite_file(db_url: str) -> Path | None:
    """Путь к файлу для file-backed SQLite URL, иначе ``None``."""
    if not db_url.startswith(_SQLITE_PREFIX):
        return None
    raw = db_url[len(_SQLITE_PREFIX) :]
    if not raw or raw == ":memory:" or raw.startswith("file:"):
        return None
    return Path(raw)


def _cleanup_template() -> None:
    if _template_dir is not None:
        shutil.rmtree(_template_dir, ignore_errors=True)


def _build_template() -> Path:
    """Собрать (или переиспользовать) шаблон схемы на голове."""
    global _template_db, _template_key, _template_dir

    key = _migrations_fingerprint()
    if _template_db is not None and _template_key == key and _template_db.exists():
        return _template_db

    if _template_dir is None:
        _template_dir = Path(tempfile.mkdtemp(prefix="cod-doc-schema-"))
        atexit.register(_cleanup_template)

    template = _template_dir / "head.db"
    for suffix in ("", *_SQLITE_SIDECARS):
        Path(f"{template}{suffix}").unlink(missing_ok=True)

    _run_alembic_subprocess("upgrade", "head", db_url=f"{_SQLITE_PREFIX}{template}")

    _template_db = template
    _template_key = key
    return template


def _copy_template(target: Path) -> None:
    template = _build_template()
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(template, target)
    for suffix in _SQLITE_SIDECARS:
        sidecar = Path(f"{template}{suffix}")
        if sidecar.exists():
            shutil.copyfile(sidecar, Path(f"{target}{suffix}"))


def _run_alembic_subprocess(*args: str, db_url: str) -> subprocess.CompletedProcess[bytes]:
    env = {**os.environ, "COD_DOC_DB_URL": db_url}
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        check=True,
        env=env,
        capture_output=True,
    )


def run_alembic(*args: str, db_url: str) -> subprocess.CompletedProcess[bytes]:
    """Выполнить ``alembic <args>`` против ``db_url``.

    Пример вызова — ``upgrade`` до последней ревизии с явным URL базы.
    Бросает ``CalledProcessError`` при ненулевом коде возврата.

    ``upgrade head`` по ещё не существующему файлу SQLite обслуживается копией
    закэшированного шаблона (см. докстринг модуля); всё остальное идёт в
    подпроцесс.
    """
    if args == ("upgrade", "head"):
        target = _sqlite_file(db_url)
        if target is not None and not target.exists():
            _copy_template(target)
            return subprocess.CompletedProcess(
                [sys.executable, "-m", "alembic", *args], 0, b"", b""
            )
    return _run_alembic_subprocess(*args, db_url=db_url)
