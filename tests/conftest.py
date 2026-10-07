"""Global test isolation for COD-DOC runtime state.

Keeps tests from reading or writing the user's real ``~/.cod-doc`` registry and
prevents workspace-local project discovery from leaking the current checkout into
tests that intentionally create an empty config.
"""

from __future__ import annotations

import os
import zlib
from pathlib import Path

import pytest

#: ADO-212: переменные, по которым git находит репозиторий, — тот же набор, что
#: сбрасывает scripts/gate.sh. Пользовательский конфиг git (GIT_SSH, GIT_AUTHOR_*)
#: сознательно не трогаем: он не меняет, какой репозиторий видит фикстура.
_GIT_REPO_LOCATORS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_PREFIX",
    "GIT_NAMESPACE",
)


@pytest.fixture(autouse=True)
def isolated_cod_doc_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    home = tmp_path / "cod-doc-home"
    home.mkdir()

    # ADO-070: прогон обязан быть герметичным. `Config` читает окружение с
    # префиксом COD_DOC_ (env_prefix), поэтому любая внешняя COD_DOC_*
    # подменяет поле конфига в тестах. Workflow CI выставляет
    # COD_DOC_API_KEY=sk-test-placeholder — и тесты, проверяющие поведение
    # «ключ не настроен», падали только в CI. Локально это не воспроизводилось,
    # потому что переменной нет; а увидеть это раньше было нельзя — джоба
    # умирала на alembic до самих тестов.
    for key in [k for k in os.environ if k.startswith("COD_DOC_")]:
        monkeypatch.delenv(key, raising=False)

    # ADO-212: переменные, по которым git находит репозиторий. При push из
    # worktree git экспортирует GIT_DIR в окружение pre-push, и pytest,
    # запущенный из хука, наследует его. Git-фикстуры вида
    # `git -C <tmp_path> init/add/commit` тогда работают с настоящим
    # репозиторием, а не со своим: `init` ставит core.bare=true в общий конфиг,
    # `commit` кладёт на ветку дерево из одних файлов фикстуры. Сбрасываем
    # здесь, а не только в scripts/gate.sh: хук — не единственный путь.
    for key in _GIT_REPO_LOCATORS:
        monkeypatch.delenv(key, raising=False)

    # ADO-068: достаточно переменной окружения. Раньше здесь дополнительно
    # подменялись константы cod_doc.config.CONFIG_DIR/CONFIG_FILE — они
    # вычислялись на импорте, и без подмены изоляция не работала. Теперь путь
    # резолвится в момент вызова (config_dir()/config_file()), поэтому setenv
    # покрывает и внутрипроцессный код, и подпроцессы, которым окружение
    # наследуется (см. tests/_alembic.py).
    monkeypatch.setenv("COD_DOC_HOME", str(home))

    # Workspace discovery is a useful runtime fallback, but in tests it makes
    # an empty Config accidentally include the repository under test. Keep the
    # dedicated discovery tests real; isolate everything else.
    if Path(str(request.node.path)).name != "test_config_workspace_discovery.py":
        monkeypatch.setattr("cod_doc.config._discover_workspace_projects", lambda start=None: [])
        monkeypatch.setattr("cod_doc.config._discover_workspace_project", lambda name: None)
    return home


@pytest.fixture(autouse=True)
def isolated_api_runtime_state() -> None:
    """Reset process-wide API state that can leak between TestClient cases."""
    from cod_doc.api.deps import dispose_all_engines, set_config, stop_daemon, webhook_registry
    from cod_doc.config import Config

    stop_daemon()
    dispose_all_engines()
    webhook_registry.clear()
    set_config(Config())
    yield
    stop_daemon()
    dispose_all_engines()
    webhook_registry.clear()
    set_config(Config())


#: `COD_DOC_TEST_SHARD=<i>/<n>` — прогнать только i-ю из n долей набора (1-based).
#: Так CI раскладывает pytest на несколько раннеров: на четырёх ядрах одного
#: раннера прогон шёл ~150 с и был всем критическим путём.
_SHARD_ENV = "COD_DOC_TEST_SHARD"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Оставить в прогоне только файлы своей доли, остальные — deselected.

    Делим по файлам, а не по тестам — по той же причине, что `--dist loadfile`:
    тесты одного файла делят внутрипроцессные глобалы и должны идти в одном
    процессе. Доля файла — crc32 его пути: стабильна между прогонами и не
    перетасовывает раскладку, когда добавляется новый файл. Перекос долей на
    замере — 14% от идеала при трёх долях.
    """
    spec = os.environ.get(_SHARD_ENV, "").strip()
    if not spec:
        return
    index, _, total = spec.partition("/")
    shard, shards = int(index), int(total)
    if not 1 <= shard <= shards:
        raise pytest.UsageError(f"{_SHARD_ENV}={spec}: ожидалось <i>/<n> с 1 ≤ i ≤ n")
    keep: list[pytest.Item] = []
    drop: list[pytest.Item] = []
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        (keep if zlib.crc32(path.encode()) % shards == shard - 1 else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
