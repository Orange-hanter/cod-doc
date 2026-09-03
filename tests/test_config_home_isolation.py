"""ADO-068: COD_DOC_HOME обязан действовать и на чтение, и на запись.

Дефект: ``CONFIG_DIR``/``CONFIG_FILE`` были константами уровня модуля и
вычислялись на импорте ``cod_doc.config`` — то есть на стадии коллекции тестов,
ДО того как autouse-фикстура подменит ``COD_DOC_HOME``. Внутри процесса
изоляцию дотягивал ``monkeypatch.setattr`` на сами константы, но подпроцессы
(CLI, alembic) запускались с урезанным окружением без ``COD_DOC_HOME`` — и
писали в настоящий ``~/.cod-doc``. В рабочем конфиге пользователя оказались
``model: m`` / ``api_key: sk-test`` и pytest-каталоги в ``projects:`` —
побайтовая копия тестовых фикстур. Рецидив находки F5 (ADO-001).
"""

from __future__ import annotations

import subprocess
import sys
from typing import TYPE_CHECKING

import cod_doc.config as cfgmod
from cod_doc.config import Config, config_dir, config_file

if TYPE_CHECKING:
    from pathlib import Path


def test_config_paths_are_not_frozen_at_import(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Смена COD_DOC_HOME видна сразу — путь не «замерзает» на импорте."""
    first = tmp_path / "home-one"
    second = tmp_path / "home-two"
    monkeypatch.setenv("COD_DOC_HOME", str(first))
    assert config_dir() == first
    assert config_file() == first / "config.yaml"

    monkeypatch.setenv("COD_DOC_HOME", str(second))
    assert config_dir() == second, "config_dir() кэширует значение с импорта"
    assert config_file() == second / "config.yaml"


def test_module_level_path_constants_are_gone() -> None:
    """Константы удалены намеренно: их наличие возвращает дефект.

    Пока путь можно взять модульным атрибутом, он снова будет вычислен один раз
    на импорте, и подпроцесс/поздняя смена окружения его не переопределят.
    """
    for name in ("CONFIG_DIR", "CONFIG_FILE"):
        assert not hasattr(cfgmod, name), (
            f"cod_doc.config.{name} снова стала модульной константой — "
            "путь опять замёрзнет на импорте (ADO-068)"
        )


def test_save_writes_under_cod_doc_home(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Config.save() пишет в подменённый HOME, а не в домашний каталог."""
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("COD_DOC_HOME", str(home))
    Config(api_key="sk-test", model="m", base_url="https://x").save()

    written = home / "config.yaml"
    assert written.exists(), "save() не записал конфиг в COD_DOC_HOME"
    assert "sk-test" in written.read_text(encoding="utf-8")


def test_subprocess_inherits_cod_doc_home(
    tmp_path: Path,
    monkeypatch,  # type: ignore[no-untyped-def]
) -> None:
    """Свежий интерпретатор тоже уважает COD_DOC_HOME.

    Именно этот путь и утекал: тесты звали CLI/alembic через subprocess с
    ``env={"PATH": "/usr/bin:/bin", ...}``, где COD_DOC_HOME не было вовсе.
    """
    home = tmp_path / "subprocess-home"
    home.mkdir()
    monkeypatch.setenv("COD_DOC_HOME", str(home))

    proc = subprocess.run(
        [sys.executable, "-c", "from cod_doc.config import config_file; print(config_file())"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert proc.stdout.strip() == str(home / "config.yaml")
