"""AFT-012 / RFC 27 F13 — чистые юнит-тесты is_foreign / foreign_paths."""

from __future__ import annotations

from cod_doc.services.task_locality import foreign_paths, is_foreign


def test_is_foreign_cases() -> None:
    root = "/repo/proj"
    # Пустой список файлов — задача локальная.
    assert is_foreign([], root) is False
    # Относительный путь — локальная.
    assert is_foreign(["cod_doc/x.py"], root) is False
    # Смешанные пути — локальная: не ВСЕ файлы чужие.
    assert is_foreign(["/elsewhere/z.py", "cod_doc/y.py"], root) is False
    # Только чужие абсолютные пути — чужая.
    assert is_foreign(["/elsewhere/a.py", "/elsewhere/b.py"], root) is True
    # Абсолютный путь внутри корня — локальная.
    assert is_foreign(["/repo/proj/cod_doc/x.py"], root) is False
    # Граница префикса: /repo/projX не лежит внутри /repo/proj.
    assert is_foreign(["/repo/projX/a.py"], root) is True
    # '~/…' не абсолютный — локальная.
    assert is_foreign(["~/x.py"], root) is False
    # Пустой root_path — всегда локальная.
    assert is_foreign(["/elsewhere/a.py"], "") is False


def test_foreign_paths_keeps_order() -> None:
    assert foreign_paths(
        ["/elsewhere/b", "rel", "/repo/proj/in", "/elsewhere/a"], "/repo/proj"
    ) == ["/elsewhere/b", "/elsewhere/a"]
