"""Шардирование набора для CI (`COD_DOC_TEST_SHARD`, tests/conftest.py).

Раскладка обязана быть разбиением: доли не пересекаются и вместе дают весь
набор. Иначе CI зеленеет, молча не прогнав часть тестов, — и ни один из
оставшихся этого не заметит.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.conftest import pytest_collection_modifyitems

_FILES = [f"tests/area_{i}/test_mod_{i}.py" for i in range(60)]


def _items() -> list[SimpleNamespace]:
    return [SimpleNamespace(nodeid=f"{f}::test_{k}") for f in _FILES for k in range(3)]


def _run(monkeypatch: pytest.MonkeyPatch, spec: str) -> tuple[list[str], list[str]]:
    monkeypatch.setenv("COD_DOC_TEST_SHARD", spec)
    items = _items()
    dropped: list[str] = []
    hook = SimpleNamespace(pytest_deselected=lambda items: dropped.extend(i.nodeid for i in items))
    pytest_collection_modifyitems(SimpleNamespace(hook=hook), items)  # type: ignore[arg-type]
    return [i.nodeid for i in items], dropped


def test_shards_partition_the_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    everything = {i.nodeid for i in _items()}
    kept = [_run(monkeypatch, f"{n}/3")[0] for n in (1, 2, 3)]

    assert sum(len(k) for k in kept) == len(everything)
    assert set().union(*kept) == everything
    assert all(kept), "доля без единого теста — раскладка вырождена"


def test_a_file_never_straddles_two_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тесты одного файла делят глобалы процесса — как и под `--dist loadfile`."""
    for n in (1, 2, 3):
        kept, dropped = _run(monkeypatch, f"{n}/3")
        files_kept = {k.split("::")[0] for k in kept}
        files_dropped = {d.split("::")[0] for d in dropped}
        assert not files_kept & files_dropped


def test_no_shard_env_keeps_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    kept, dropped = _run(monkeypatch, "")
    assert len(kept) == len(_items())
    assert dropped == []


@pytest.mark.parametrize("spec", ["0/3", "4/3", "2/0"])
def test_out_of_range_shard_is_a_usage_error(monkeypatch: pytest.MonkeyPatch, spec: str) -> None:
    with pytest.raises(pytest.UsageError):
        _run(monkeypatch, spec)
