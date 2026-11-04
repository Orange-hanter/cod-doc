"""Ответ на вопрос слабой ролью (0057): reader — по адресу, responder — любой.

``request_identity.check_question_answer`` — объектная проверка на путях
``question_service.resolve`` / ``add_option``: роль ``reader`` отвечает только
на вопрос, адресованный ему (``owner`` — его handle), ``responder`` — на любой
вопрос проекта. ``preauthorized`` пропускает общий ``check_write`` в
``activity_service.emit`` только для этой мутации; остальные записи слабым
ролям по-прежнему запрещены.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import select

from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import ActivityEventModel, ProjectModel
from cod_doc.services import question_service, request_identity
from cod_doc.services.request_identity import AccessDeniedError, Identity

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

    from sqlalchemy.orm import Session, sessionmaker

UID = "proj-uid-1"
SLUG = "oq"
ASKER = "human:asker"


def _identity(handle: str, role: str) -> Identity:
    """Владелец токена с одной ролью; авторов — только он сам (без агентов)."""
    roles: Mapping[str, str] = {UID: role}
    return Identity(
        actor_uid="uid-1",
        handle=handle,
        kind="human",
        token_label="laptop",
        authors={handle: "human"},
        roles=roles,
    )


@contextlib.contextmanager
def _as_member(handle: str, role: str) -> Iterator[None]:
    """Привязать владельца токена и «войти» в проект — как делает гейт."""
    with request_identity.bind(_identity(handle, role)):
        request_identity.enter_project(UID, SLUG)
        yield


@pytest.fixture
def factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


@pytest.fixture
def project_id(factory: sessionmaker[Session]) -> int:
    """Проект с uid и два вопроса: адресованный pavel и без owner."""

    def seed(session: Session) -> int:
        now = datetime.now(UTC)
        proj = ProjectModel(uid=UID, slug=SLUG, title="P", root_path="/tmp/oq", config_json={})
        proj.created = now
        proj.updated = now
        session.add(proj)
        session.flush()
        question_service.create(
            session,
            project_id=proj.row_id,
            title="Адресованный",
            question="Как?",
            author=ASKER,
            owner="pavel",
        )
        question_service.create(
            session, project_id=proj.row_id, title="Общий", question="Что?", author=ASKER
        )
        return proj.row_id

    with transactional(factory) as session:
        return seed(session)


def test_check_question_answer_matrix() -> None:
    """Чистая функция: привязка есть — матрица ролей, владельца и автора."""
    # (роль, owner, author, handle, authors, ожидаемый preauthorized)
    allowed: list[tuple[str, str | None, str, str, Mapping[str, str], bool]] = [
        ("writer", None, "human:walt", "walt", {"walt": "human"}, False),
        ("admin", "pavel", "human:root", "root", {"root": "human"}, False),
        ("responder", None, "human:po", "po", {"po": "human"}, True),
        ("responder", "кто-то-другой", "human:po", "po", {"po": "human"}, True),
        ("reader", "r", "human:r", "r", {"r": "human"}, True),
        # вопрос адресован агенту владельца: agent-автор отвечает своим author'ом
        (
            "reader",
            "r/claude-code",
            "agent:r/claude-code",
            "r",
            {"r": "human", "r/claude-code": "agent"},
            True,
        ),
    ]
    for role, owner, author, handle, authors, expected in allowed:
        identity = Identity(
            actor_uid="uid-1",
            handle=handle,
            kind="human",
            token_label="laptop",
            authors=authors,
            roles={UID: role},
        )
        with request_identity.bind(identity):
            request_identity.enter_project(UID, SLUG)
            got = request_identity.check_question_answer(author, owner, "Q-001")
        assert got is expected, (role, owner, author, handle)


@pytest.mark.parametrize(
    ("role", "owner", "author"),
    [
        ("reader", None, "human:r"),  # без адресата
        ("reader", "кто-то-другой", "human:r"),  # адресован не ему
        ("reader", "r/claude-code", "human:r"),  # агент, которого у владельца нет
    ],
)
def test_check_question_answer_denies_unaddressed(
    role: str, owner: str | None, author: str
) -> None:
    identity = _identity("r", role)
    with request_identity.bind(identity):
        request_identity.enter_project(UID, SLUG)
        with pytest.raises(AccessDeniedError, match="не адресован"):
            request_identity.check_question_answer(author, owner, "Q-001")


@pytest.mark.parametrize("role", ["reader", "responder"])
def test_local_write_unaffected_without_binding(
    factory: sessionmaker[Session], project_id: int, role: str
) -> None:
    """Без привязки (CLI/локальный MCP) ответы работают как раньше."""
    with transactional(factory) as session:
        question_service.resolve(
            session,
            project_id=project_id,
            question_id="Q-001",
            author=f"human:{role}",
            resolution="локально",
        )
        resolved = question_service.get(session, project_id, "Q-001")
        assert resolved is not None
        assert resolved.status.value == "resolved"


def test_reader_answers_only_addressed_question(
    factory: sessionmaker[Session], project_id: int
) -> None:
    with (
        transactional(factory) as session,
        _as_member("pavel", "reader"),
    ):
        question_service.resolve(
            session,
            project_id=project_id,
            question_id="Q-001",
            author="human:pavel",
            resolution="Адресованный — можно",
        )
        # чужой/неадресованный вопрос — отказ
        with pytest.raises(AccessDeniedError, match="не адресован"):
            question_service.resolve(
                session,
                project_id=project_id,
                question_id="Q-002",
                author="human:pavel",
                resolution="Общий — нельзя",
            )
        # отказ случился до мутаций: Q-2 остался открытым
        general = question_service.get(session, project_id, "Q-002")
        assert general is not None
        assert general.status.value == "open"
    with factory() as session:
        event = session.scalar(
            select(ActivityEventModel).where(ActivityEventModel.kind == "question.resolved")
        )
        assert event is not None
        assert event.actor_id == "human:pavel"


def test_reader_option_add_addressed_and_denied_otherwise(
    factory: sessionmaker[Session], project_id: int
) -> None:
    with (
        transactional(factory) as session,
        _as_member("pavel", "reader"),
    ):
        question_service.add_option(
            session,
            project_id=project_id,
            question_id="Q-001",
            title="Вариант от PO",
            author="human:pavel",
        )
        with pytest.raises(AccessDeniedError, match="не адресован"):
            question_service.add_option(
                session,
                project_id=project_id,
                question_id="Q-002",
                title="Чужой вариант",
                author="human:pavel",
            )


def test_responder_answers_any_question(factory: sessionmaker[Session], project_id: int) -> None:
    with (
        transactional(factory) as session,
        _as_member("po", "responder"),
    ):
        question_service.resolve(
            session,
            project_id=project_id,
            question_id="Q-001",
            author="human:po",
            resolution="Любой вопрос — можно",
        )
        question_service.resolve(
            session,
            project_id=project_id,
            question_id="Q-002",
            author="human:po",
            resolution="И без owner — можно",
        )


@pytest.mark.parametrize("role", ["reader", "responder"])
def test_foreign_author_rejected_even_for_allowed_object(
    factory: sessionmaker[Session], project_id: int, role: str
) -> None:
    with (
        transactional(factory) as session,
        _as_member("pavel", role),
        pytest.raises(AccessDeniedError, match="не принадлежит токену"),
    ):
        question_service.resolve(
            session,
            project_id=project_id,
            question_id="Q-001",
            author="human:не-pavel",
            resolution="подмена автора",
        )


def test_reader_still_cannot_write_docs(factory: sessionmaker[Session], project_id: int) -> None:
    """Поблажка только для ответов: общий check_write для reader/responder жив."""
    from cod_doc.domain.entities import DocumentStatus, DocumentType
    from cod_doc.services import doc_service

    with (
        transactional(factory) as session,
        _as_member("pavel", "reader"),
        pytest.raises(AccessDeniedError, match="не позволяет запись"),
    ):
        doc_service.create(
            session,
            project_id=project_id,
            doc_key="x",
            type=DocumentType.MODULE_SPEC,
            status=DocumentStatus.DRAFT,
            title="X",
            author="human:pavel",
        )


def test_preauthorized_still_checks_author(factory: sessionmaker[Session], project_id: int) -> None:
    """preauthorized снимает только требование к роли: чужой author отклонён."""
    from cod_doc.services import activity_service

    with (
        transactional(factory) as session,
        _as_member("pavel", "responder"),
        pytest.raises(AccessDeniedError, match="не принадлежит токену"),
    ):
        activity_service.emit(
            session,
            project_id,
            "question.resolved",
            actor_kind="human",
            actor_id="human:не-pavel",
            preauthorized=True,
        )
