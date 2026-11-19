"""Служебная запись ссылок под Bearer-токеном (SYNC-202 на `author='system'`).

Таблица ``link`` — производная: её пересчитывает сервер, а не владелец
токена. ``link_service.resolver`` пишет activity от ``"system"``; под
привязанным владельцем токена ``activity_service.emit`` сверял этот author с
токеном и падал ``SYNC-202: author 'system' не принадлежит токену``. Так на
удалённом MCP падали ``curator_next`` (сборщик ``links`` гоняет
``resolve_section`` в ``commit=False``-сессии) и любая запись секции со
ссылкой — подписчик ``SectionWritten`` зовёт ``sync_section``.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import LinkModel, ProjectModel
from cod_doc.services import curator_service, doc_service, request_identity
from cod_doc.services.request_identity import AccessDeniedError, Identity

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.orm import Session, sessionmaker

UID = "proj-uid-links"
SLUG = "lk"
AUTHOR = "agent:dakh/claude-code"


def _identity(role: str) -> Identity:
    return Identity(
        actor_uid="uid-dakh",
        handle="dakh",
        kind="human",
        token_label="laptop",
        authors={"dakh": "human"} | {"dakh/claude-code": "agent"},
        roles={UID: role},
    )


@contextlib.contextmanager
def _as_member(role: str) -> Iterator[None]:
    with request_identity.bind(_identity(role)):
        request_identity.enter_project(UID, SLUG)
        yield


@pytest.fixture
def factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def seeded(factory: sessionmaker[Session], tmp_path: Path) -> tuple[int, int]:
    """Проект с uid и документ с одной секцией без ссылок."""
    with transactional(factory) as session:
        now = datetime.now(UTC)
        proj = ProjectModel(uid=UID, slug=SLUG, title="L", root_path=str(tmp_path), config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        doc = doc_service.create(
            session,
            project_id=proj.row_id,
            doc_key="guide",
            type=DocumentType.GUIDE,
            status=DocumentStatus.ACTIVE,
            title="Guide",
            author="human:dakh",
            path="guide.md",
            owner="dakh",
        )
        assert doc.row_id is not None
        doc_service.add_section(
            session,
            project_id=proj.row_id,
            document_id=doc.row_id,
            anchor="intro",
            heading="Intro",
            level=2,
            position=0,
            body="Без ссылок.",
            author="human:dakh",
        )
        assert doc.row_id is not None
        return proj.row_id, doc.row_id


def test_section_write_with_link_under_token(
    factory: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    """Писатель правит секцию со ссылкой: каскад ссылок проходит, а не глотается.

    ``doc_service`` ведёт каскад best-effort: SYNC-202 из подписчика он
    логирует и идёт дальше, запись при этом успешна. Поэтому проверяется
    результат каскада: ссылка из нового тела разрешена.
    """
    project_id, document_id = seeded
    with _as_member("writer"), transactional(factory) as session:
        section = doc_service.patch_section(
            session,
            project_id=project_id,
            document_id=document_id,
            anchor="intro",
            new_body="См. [гайд](guide.md#intro).",
            author=AUTHOR,
        )
    with transactional(factory) as session:
        links = list(
            session.execute(
                select(LinkModel).where(LinkModel.from_section_id == section.row_id)
            ).scalars()
        )
    assert [(link.raw, link.resolved) for link in links] == [("[гайд](guide.md#intro)", True)]


def test_curator_links_card_under_token(
    factory: sessionmaker[Session], seeded: tuple[int, int], tmp_path: Path
) -> None:
    """Read-only карточка куратора собирает ``links`` под токеном читателя."""
    project_id, document_id = seeded
    with transactional(factory) as session:
        doc_service.patch_section(
            session,
            project_id=project_id,
            document_id=document_id,
            anchor="intro",
            new_body="[нет](missing.md)",
            author="human:dakh",
        )
    with _as_member("reader"), transactional(factory, commit=False) as session:
        card = curator_service.next(
            session,
            project_id=project_id,
            root_path=tmp_path,
            master_path=tmp_path / "MASTER.md",
            project_slug=SLUG,
            limit=5,
        )
    assert "links" not in card["meta"].get("not_collected", [])


def test_reader_still_cannot_write_sections(
    factory: sessionmaker[Session], seeded: tuple[int, int]
) -> None:
    """Служебная запись ссылок не открывает читателю запись секций."""
    project_id, document_id = seeded
    with (
        _as_member("reader"),
        pytest.raises(AccessDeniedError),
        transactional(factory) as session,
    ):
        doc_service.patch_section(
            session,
            project_id=project_id,
            document_id=document_id,
            anchor="intro",
            new_body="[гайд](guide.md)",
            author=AUTHOR,
        )
