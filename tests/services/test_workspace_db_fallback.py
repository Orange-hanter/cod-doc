"""Cycle-4 partial PCA-945 close: ``session_factory`` falls back to
the workspace default when the caller passes an empty/None project.

Note: full auto-resolution (allowing the caller to **omit** ``project``
entirely) requires changing every DB-tool signature to keyword-only with
``project: str | None = None`` — invasive across 45 tools. The empty-string
fallback is the wire-level mechanism agents can use today via
``set_default_project(name)`` + later calls with ``project=""``.
"""

from __future__ import annotations

import pytest

from cod_doc.mcp.tools import _db, _workspace


def test_session_factory_empty_string_falls_back_to_default(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """If a tool is called with ``project=""``, _db._resolve picks up
    the workspace default and the rest of the path proceeds normally.

    We monkeypatch make_engine to avoid actually opening a SQLite file —
    the only thing under test is the resolution step.
    """
    captured: dict = {}

    class _FakeEntry:
        path = str(tmp_path)

    def fake_get_project(name):
        captured["resolved"] = name
        return _FakeEntry()

    class _FakeCfg:
        get_project = staticmethod(fake_get_project)

    monkeypatch.setattr("cod_doc.config.Config.load", lambda: _FakeCfg())
    monkeypatch.setattr(
        "cod_doc.infra.db.resolve_db_url", lambda p: f"sqlite:///{p}/x.db"
    )
    monkeypatch.setattr("cod_doc.infra.db.make_engine", lambda url: object())
    monkeypatch.setattr(
        "cod_doc.infra.db.make_session_factory", lambda eng: (lambda: None)
    )

    _workspace.clear()
    _workspace.set_("from-default-ws")
    try:
        _db.session_factory("")
    finally:
        _workspace.clear()

    assert captured["resolved"] == "from-default-ws"


def test_session_factory_no_default_no_arg_gives_actionable_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    _workspace.clear()
    with pytest.raises(ValueError, match="set_default_project"):
        _db.session_factory("")


def test_session_factory_explicit_overrides_default(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Explicit ``project="x"`` wins over workspace default."""
    captured: dict = {}

    class _FakeEntry:
        path = str(tmp_path)

    def fake_get_project(name):
        captured["resolved"] = name
        return _FakeEntry()

    class _FakeCfg:
        get_project = staticmethod(fake_get_project)

    monkeypatch.setattr("cod_doc.config.Config.load", lambda: _FakeCfg())
    monkeypatch.setattr(
        "cod_doc.infra.db.resolve_db_url", lambda p: f"sqlite:///{p}/x.db"
    )
    monkeypatch.setattr("cod_doc.infra.db.make_engine", lambda url: object())
    monkeypatch.setattr(
        "cod_doc.infra.db.make_session_factory", lambda eng: (lambda: None)
    )

    _workspace.clear()
    _workspace.set_("ignored-default")
    try:
        _db.session_factory("explicit-arg")
    finally:
        _workspace.clear()

    assert captured["resolved"] == "explicit-arg"
