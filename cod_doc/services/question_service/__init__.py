"""Open questions — нерешённые вопросы проекта как сущность БД.

Спецификация — ``docs/system/capabilities/decisions-and-questions.md`` §2.
Вопрос — «формулировка без решения»: заголовок, сам вопрос, контекст,
структурированные варианты ответа, статус ``open | resolved | dropped``,
владелец, приоритет и типизированные ссылки на документы, секции, код,
задачи, ADR, истории, сценарии и находки.

Инвариант пакета: вопросы **не проецируются в markdown**. Ни одна функция
здесь не создаёт ``document`` / ``section``; импорт легаси-документов
``type: open-question`` (``import_doc``) переносит их в эту сущность и
удаляет документ. Смотреть и править вопросы — через CLI ``cod-doc
question``, MCP ``question_*`` и веб ``/p/<slug>/questions``.
"""

from ._internals import split_code_ref, validate_question_id, validate_ref
from ._types import (
    LinkCheck,
    QuestionAlreadyExistsError,
    QuestionNotFoundError,
    QuestionOptionNotFoundError,
    QuestionStateError,
    VerifyReport,
)
from .crud import create, drop, get, list_for_project, reopen, resolve, update
from .links import link, list_links, questions_for_targets, unlink
from .options import add_option, list_options, remove_option, update_option
from .serialize import link_to_dict, option_to_dict, question_summary, question_to_dict
from .verify import broken_links, check_edge, code_excerpt, verify_links

__all__ = [
    "LinkCheck",
    "QuestionAlreadyExistsError",
    "QuestionNotFoundError",
    "QuestionOptionNotFoundError",
    "QuestionStateError",
    "VerifyReport",
    "add_option",
    "broken_links",
    "check_edge",
    "code_excerpt",
    "create",
    "drop",
    "get",
    "link",
    "link_to_dict",
    "list_for_project",
    "list_links",
    "list_options",
    "option_to_dict",
    "question_summary",
    "question_to_dict",
    "questions_for_targets",
    "remove_option",
    "reopen",
    "resolve",
    "split_code_ref",
    "unlink",
    "update",
    "update_option",
    "validate_question_id",
    "validate_ref",
    "verify_links",
]
