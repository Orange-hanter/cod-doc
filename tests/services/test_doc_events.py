"""SVC-007: события документов вместо прямых вызовов doc_service → link_service.

Поведение каскада (ссылки переписаны, история секции цела) стерегут прежние
тесты `test_doc_rename_section.py` и тесты `rename_cascade` — они теперь
проходят через подписчика. Здесь — то, что добавила шина: пропущенная точка
входа падает, а не молчит; синхронизация ссылок после записи осталась
best-effort; все точки входа подписывают; doc_service про link_service не знает.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.services import doc_events
from cod_doc.services import doc_service as docs
from tests.services.test_doc_rename_section import _seed

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRY_POINTS = ("cod_doc/cli/__init__.py", "cod_doc/api/server.py", "cod_doc/mcp/server.py")


def test_publish_without_subscriber_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_events, "_HANDLERS", {})
    with pytest.raises(doc_events.MissingSubscriberError, match=r"event_wiring\.install"):
        doc_events.publish(doc_events.SectionWritten(session=None, section_id=1))  # type: ignore[arg-type]


def test_subscribe_twice_registers_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doc_events, "_HANDLERS", {})
    calls: list[int] = []

    def handler(event: doc_events.SectionWritten) -> None:
        calls.append(event.section_id)

    doc_events.subscribe(doc_events.SectionWritten, handler)
    doc_events.subscribe(doc_events.SectionWritten, handler)
    doc_events.publish(doc_events.SectionWritten(session=None, section_id=7))  # type: ignore[arg-type]
    assert calls == [7]


def test_rename_section_without_subscribers_does_not_skip_the_cascade(
    engine_with_schema: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Раньше каскад был прямым вызовом и не мог потеряться; теперь его теряет
    пропущенная точка входа — и это обязано быть ошибкой, а не битыми ссылками."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)
        monkeypatch.setattr(doc_events, "_HANDLERS", {})
        with pytest.raises(doc_events.MissingSubscriberError):
            docs.rename_section(
                s,
                project_id=pid,
                document_id=target,
                anchor="data-model",
                heading="Data Model v2",
                author="t",
            )


def test_document_write_without_subscribers_is_not_swallowed(
    engine_with_schema: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Синхронизация ссылок best-effort, но пропущенная точка входа — не сбой
    подписчика: её `except Exception` в doc_service не глотает."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)
        monkeypatch.setattr(doc_events, "_HANDLERS", {})
        with pytest.raises(doc_events.MissingSubscriberError):
            docs.patch_section(
                s,
                project_id=pid,
                document_id=target,
                anchor="intro",
                new_body="Changed.",
                author="t",
            )


def test_failing_link_sync_subscriber_does_not_fail_the_write(
    engine_with_schema: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """COD-079 без изменений: ссылки — производные данные, запись документа главнее."""
    factory = make_session_factory(engine_with_schema)
    with transactional(factory) as s:
        pid, target = _seed(s)

        def broken(_event: doc_events.SectionWritten) -> None:
            raise RuntimeError("parser exploded")

        monkeypatch.setattr(doc_events, "_HANDLERS", {doc_events.SectionWritten: [broken]})
        docs.patch_section(
            s,
            project_id=pid,
            document_id=target,
            anchor="intro",
            new_body="Changed.",
            author="t",
        )
    assert "links will be out of date" in caplog.text


@pytest.mark.parametrize("path", ENTRY_POINTS)
def test_entry_point_installs_subscribers(path: str) -> None:
    tree = ast.parse((REPO_ROOT / path).read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "install"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "event_wiring"
    ]
    assert calls, f"{path} не зовёт event_wiring.install(): записи документов упадут"


def test_doc_service_does_not_import_link_service() -> None:
    tree = ast.parse((REPO_ROOT / "cod_doc/services/doc_service.py").read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported += [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
    assert not [m for m in imported if "link_service" in m], imported
