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
from .import_doc import (
    AlreadyImportedError,
    ImportPlan,
    ImportResult,
    NotAQuestionDocumentError,
    PlannedQuestion,
    import_document,
    plan_import,
)
from .links import (
    answered_by_tasks,
    link,
    linked_questions,
    list_links,
    open_questions_for_context,
    questions_for_targets,
    unlink,
)
from .options import add_option, list_options, remove_option, update_option
from .serialize import (
    import_result_to_dict,
    link_to_dict,
    option_to_dict,
    question_summary,
    question_to_dict,
)
from .verify import broken_links, check_edge, code_excerpt, verify_links

__all__ = [
    "AlreadyImportedError",
    "ImportPlan",
    "ImportResult",
    "LinkCheck",
    "NotAQuestionDocumentError",
    "PlannedQuestion",
    "QuestionAlreadyExistsError",
    "QuestionNotFoundError",
    "QuestionOptionNotFoundError",
    "QuestionStateError",
    "VerifyReport",
    "add_option",
    "answered_by_tasks",
    "broken_links",
    "check_edge",
    "code_excerpt",
    "create",
    "drop",
    "get",
    "import_document",
    "import_result_to_dict",
    "link",
    "link_to_dict",
    "linked_questions",
    "list_for_project",
    "list_links",
    "list_options",
    "open_questions_for_context",
    "option_to_dict",
    "plan_import",
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
