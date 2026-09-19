"""COD-DOC — Context Orchestrator for Documentation."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

#: Версия выводится из git через setuptools-scm и попадает в метаданные
#: дистрибутива при сборке — читаем оттуда, а не из литерала в коде.
#:
#: Пока версия была захардкожена, все установки на машине назывались `1.1.0`
#: независимо от того, из какого коммита собраны: отличить свежую от
#: полугодовой было нечем, а `uv pip install` без `--reinstall` считал, что
#: обновлять нечего. Теперь `cod-doc --version` печатает ревизию.
#:
#: `PackageNotFoundError` возникает ровно в одном случае — пакет импортируют
#: из дерева исходников, не установив (например, `PYTHONPATH=.`). Это рабочий
#: сценарий, ронять импорт из-за него нельзя.
try:
    __version__ = _pkg_version("cod-doc")
except PackageNotFoundError:  # pragma: no cover — установленный пакет есть всегда
    __version__ = "0+unknown"

__all__ = ["__version__"]
