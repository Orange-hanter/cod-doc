"""Error types + DTOs for question-service."""

from __future__ import annotations

from dataclasses import dataclass, field


class QuestionNotFoundError(LookupError):
    pass


class QuestionAlreadyExistsError(ValueError):
    pass


class QuestionStateError(ValueError):
    """Переход статуса, которого нет: resolve закрытого, reopen открытого и т. п."""


class QuestionOptionNotFoundError(LookupError):
    pass


@dataclass(slots=True)
class LinkCheck:
    """Результат проверки одной ссылки вопроса."""

    question_id: str
    to_kind: str
    to_ref: str
    relation: str
    resolved: bool | None
    broken_reason: str | None


@dataclass(slots=True)
class VerifyReport:
    """Итог ``verify_links``: ``unchecked`` — ссылки вида ``url`` (сеть не трогаем)."""

    checked: int = 0
    ok: int = 0
    broken: int = 0
    unchecked: int = 0
    broken_links: list[LinkCheck] = field(default_factory=list)
