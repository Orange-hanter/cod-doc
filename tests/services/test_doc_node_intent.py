"""Вердикт LLM о покрытии назначения раздела.

Главное, что здесь проверяется, — не разбор JSON, а инвариант: **ошибка модели
не должна закрывать партицию находок**. Проглоченное исключение оставляет
пустой список вердиктов, сверка считает все пробелы вылеченными и закрывает их.
Ровно так вёл себя прежний `nav_service` (удалён): ловил `Exception` и всё равно писал пустой
результат в кэш, из-за чего «API-ключ не настроен» выглядело как «пробелов
нет».
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from cod_doc.domain.entities import DocumentStatus, DocumentType
from cod_doc.infra.db import make_session_factory, transactional
from cod_doc.infra.models import DocumentModel, ProjectModel
from cod_doc.services import doc_node_intent as intent
from cod_doc.services import doc_tree_service as tree
from cod_doc.services.ai_text import AIBackendError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@pytest.fixture
def session_factory(engine_with_schema):  # type: ignore[no-untyped-def]
    return make_session_factory(engine_with_schema)


def _seed_project(session: Session) -> int:
    now = datetime.now(UTC)
    proj = ProjectModel(slug="p", title="P", root_path="/tmp/p", config_json={})
    proj.created = now
    proj.updated = now
    session.add(proj)
    session.flush()
    assert proj.row_id is not None
    return proj.row_id


def _doc(session: Session, project_id: int, doc_key: str, node_key: str) -> None:
    now = datetime.now(UTC)
    session.add(
        DocumentModel(
            project_id=project_id,
            doc_key=doc_key,
            path=f"{doc_key}.md",
            type=DocumentType.GUIDE.value,
            status=DocumentStatus.ACTIVE.value,
            title=doc_key,
            preamble="тело документа",
            created=now,
            last_updated=now,
        )
    )
    session.flush()
    tree.assign(
        session, project_id=project_id, doc_key=doc_key, node_key=node_key, author="human:test"
    )


# ------------------------------------------------------------------ #
# Разбор ответа                                                       #
# ------------------------------------------------------------------ #


def test_parse_strips_the_fence() -> None:
    raw = '```json\n{"verdicts": [{"node_key": "vision", "covers_intent": false}]}\n```'
    verdicts = intent.parse_verdicts(raw)
    assert len(verdicts) == 1
    assert verdicts[0].node_key == "vision"
    assert verdicts[0].covers_intent is False


def test_parse_rejects_garbage() -> None:
    """Мусор — это ошибка, а не пустой список: пустой закрыл бы партицию."""
    with pytest.raises(json.JSONDecodeError):
        intent.parse_verdicts("не json вовсе")


def test_verdict_about_an_unknown_section_is_dropped() -> None:
    """Модель придумывает ключи; находка о несуществующем разделе не закроется
    никогда и будет висеть вечно."""
    verdicts = [intent.Verdict("призрак", False, ["что-то"], "")]
    assert intent.verdicts_to_issues(verdicts, {"vision"}) == []


def test_covered_section_produces_no_issue() -> None:
    verdicts = [intent.Verdict("vision", True, [], "всё хорошо")]
    assert intent.verdicts_to_issues(verdicts, {"vision"}) == []


def test_uncovered_section_becomes_an_issue() -> None:
    verdicts = [intent.Verdict("vision", False, ["нет аудитории", "нет ограничений"], "тонко")]
    issues = intent.verdicts_to_issues(verdicts, {"vision"})
    assert len(issues) == 1
    assert issues[0].code == "NODE-INTENT-AI"
    assert issues[0].scope_id == "vision"
    assert "нет аудитории" in issues[0].body


# ------------------------------------------------------------------ #
# Состав промпта                                                      #
# ------------------------------------------------------------------ #


def test_inbox_is_not_offered_to_the_model(session_factory) -> None:  # type: ignore[no-untyped-def]
    """У Инбокса нет назначения — он очередь разбора, а не раздел."""
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        keys = {s["node_key"] for s in intent.collect_sections(session, pid)}

        assert "inbox" not in keys
        assert "vision" in keys


def test_prompt_carries_intent_and_contents(session_factory) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")
        _doc(session, pid, "docs/VISION", "vision")

        prompt = intent.build_prompt(intent.collect_sections(session, pid))

        assert "Назначение:" in prompt
        assert "docs/VISION" in prompt
        assert "(пусто)" in prompt, "пустой раздел обязан быть виден модели"


# ------------------------------------------------------------------ #
# Запись и главный инвариант                                          #
# ------------------------------------------------------------------ #


def _open_ai_findings(session: Session, project_id: int) -> list[dict]:
    from cod_doc.services import finding_service

    return [
        f
        for f in finding_service.list_findings(session, project_id, status="open")
        if f["source_ref"] == intent.FINDING_SOURCE_REF
    ]


def test_analyze_writes_findings(session_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        monkeypatch.setattr(
            intent,
            "_call_lite_raw",
            lambda *_a, **_k: json.dumps(
                {"verdicts": [{"node_key": "vision", "covers_intent": False, "missing": ["X"]}]}
            ),
        )
        result = intent.analyze(session, project_id=pid, cfg=object())  # type: ignore[arg-type]
        session.flush()

        assert result["issues"] == 1
        assert result["created"] == 1
        assert len(_open_ai_findings(session, pid)) == 1


def test_llm_failure_does_not_close_the_partition(session_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    """Ошибка модели обязана дойти до вызывающего.

    Проглоти её — и сверка получит пустой список вердиктов, решит, что все
    пробелы вылечены, и закроет их. Находки должны пережить падение.
    """
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        monkeypatch.setattr(
            intent,
            "_call_lite_raw",
            lambda *_a, **_k: json.dumps(
                {"verdicts": [{"node_key": "vision", "covers_intent": False, "missing": ["X"]}]}
            ),
        )
        intent.analyze(session, project_id=pid, cfg=object())  # type: ignore[arg-type]
        session.flush()
        assert len(_open_ai_findings(session, pid)) == 1

        def _boom(*_a: object, **_k: object) -> str:
            raise AIBackendError("LLM backend not configured")

        monkeypatch.setattr(intent, "_call_lite_raw", _boom)

        with pytest.raises(AIBackendError):
            intent.analyze(session, project_id=pid, cfg=object())  # type: ignore[arg-type]

        assert len(_open_ai_findings(session, pid)) == 1, (
            "падение LLM не имеет права закрывать находки как «вылеченные»"
        )


def test_healed_section_closes_its_ai_finding(session_factory, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    with transactional(session_factory) as session:
        pid = _seed_project(session)
        tree.init_tree(session, project_id=pid, author="human:test")

        monkeypatch.setattr(
            intent,
            "_call_lite_raw",
            lambda *_a, **_k: json.dumps(
                {"verdicts": [{"node_key": "vision", "covers_intent": False, "missing": ["X"]}]}
            ),
        )
        intent.analyze(session, project_id=pid, cfg=object())  # type: ignore[arg-type]
        session.flush()
        assert len(_open_ai_findings(session, pid)) == 1

        monkeypatch.setattr(
            intent,
            "_call_lite_raw",
            lambda *_a, **_k: json.dumps(
                {"verdicts": [{"node_key": "vision", "covers_intent": True, "missing": []}]}
            ),
        )
        result = intent.analyze(session, project_id=pid, cfg=object())  # type: ignore[arg-type]
        session.flush()

        assert result["resolved"] == 1
        assert _open_ai_findings(session, pid) == []
