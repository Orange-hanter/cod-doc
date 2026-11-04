"""Какие MCP-тулы доступны роли ``reader`` (RFC 29 §3.8, CLO-036).

Проверка записи в ``activity_service.emit`` откатывает транзакцию, но не
побочные эффекты до неё: ``doc_export`` пишет файлы проекции, ``sync_*`` ходит
в сеть, ``*_analyze`` зовёт LLM. Поэтому reader пускается не «во всё, кроме
записи», а только в явный список чтения — **deny по умолчанию**. Тул, которого
здесь нет, reader'у недоступен; забыть сюда тул чтения стоит reader'у
возможности, а не безопасности.

``tool_call_safe`` в списке нет намеренно: он вызывает другой тул по имени, и
пустить его — значит пустить reader'а в любой тул каталога.

``QUESTION_ANSWER_TOOLS`` — единственное исключение из «только чтение»:
ответ на вопрос. По имени тула право ещё не дано — объектная проверка в
``question_service`` (``request_identity.check_question_answer``): reader
отвечает только на вопрос, адресованный ему (``owner``), роль ``responder`` —
на любой вопрос проекта.

Гейт тест ``tests/api/test_bearer_gate.py::test_reader_tools_exist`` держит
оба списка честными: каждое имя — живой тул полного каталога.
"""

from __future__ import annotations

from typing import Final

__all__ = ["QUESTION_ANSWER_TOOLS", "READER_TOOLS"]

READER_TOOLS: Final = frozenset(
    {
        "activity_list",
        "activity_summary",
        "adr_get",
        "adr_graph",
        "adr_list",
        "adr_topic_list",
        "agent_capabilities",
        "agent_get",
        "approval_get",
        "approval_list",
        "capabilities",
        "context_get",
        "ctx_docs",
        "ctx_drift",
        "ctx_search",
        "curator_next",
        "doc_body",
        "doc_drift",
        "doc_drift_all",
        "doc_get",
        "doc_list",
        "doc_node_health_get",
        "doc_section_get",
        "doc_tree_get",
        "doc_tree_unplaced",
        "finding_get",
        "finding_list",
        "link_list",
        "link_verify",
        "plan_critical_path",
        "plan_forward_chain",
        "plan_list",
        "plan_progress",
        "plan_ready",
        "plan_reverse_chain",
        "plan_sections_list",
        "roadmap_get",
        "question_get",
        "question_list",
        "revision_get",
        "revision_list",
        "revision_summary",
        "routine_get",
        "routine_history",
        "routine_list",
        "run_get",
        "scenario_coverage",
        "scenario_get",
        "scenario_list",
        "skill_get",
        "skill_list",
        "story_coverage",
        "story_get",
        "story_list",
        "story_section_list",
        "structure_context",
        "structure_diff",
        "structure_get",
        "structure_scenarios",
        "sync_conflicts",
        "task_doc_get",
        "task_doc_list",
        "task_doc_revisions",
        "task_find_duplicate",
        "task_get",
        "task_heartbeat_context",
        "task_list",
        "task_list_blocked",
        "task_next_ready",
        "task_stale",
        "task_summary",
        "tool_describe",
        "tool_search",
        "tools_diff",
    }
)

#: Письменные тулы, доступные слабым ролям поверх чтения: ответ на вопрос.
#: Право решает не имя тула, а объектная проверка в ``question_service``
#: (``resolve`` / ``add_option``), поэтому тулов здесь ровно два.
QUESTION_ANSWER_TOOLS: Final = frozenset({"question_resolve", "question_option_add"})
