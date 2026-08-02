"""
Retry-логика для вызовов OpenRouter API.
Экспоненциальный backoff с jitter для rate limits и сетевых ошибок.
Специальная обработка ошибок context_length_exceeded.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import TYPE_CHECKING, Any, TypeVar

from openai import APIConnectionError, APIStatusError, RateLimitError

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

logger = logging.getLogger("cod_doc.agent.retry")

T = TypeVar("T")

# Ошибки, после которых смысла повторять нет
_FATAL_STATUSES = {401, 403, 404, 422}


class LLMError(Exception):
    """Обёртка ошибок LLM с человекочитаемым сообщением."""

    def __init__(
        self, message: str, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code

    @classmethod
    def from_openai(cls, exc: Exception) -> LLMError:
        if isinstance(exc, RateLimitError):
            return cls(
                "OpenRouter: превышен лимит запросов (429). Подождите и повторите.",
                retryable=True,
                status_code=429,
            )
        if isinstance(exc, APIStatusError):
            status = exc.status_code
            # Проверяем context_length_exceeded внутри 400
            if status == 400:
                error_code = _extract_error_code(exc)
                if error_code == "context_length_exceeded":
                    return ContextLengthExceededError(
                        f"Контекст превышен (context_length_exceeded): {exc.message}",
                        retryable=True,
                        status_code=400,
                    )
                return cls(
                    f"Неверный запрос (400): {exc.message}",
                    retryable=False,
                    status_code=400,
                )
            if status in _FATAL_STATUSES:
                hints = {
                    401: "Неверный API-ключ. Проверьте COD_DOC_API_KEY.",
                    403: "Нет доступа. Проверьте права API-ключа.",
                    404: "Не найдено. Проверьте URL и модель.",
                    422: f"Модель отклонила запрос: {exc.message}",
                }
                return cls(
                    hints.get(status, f"HTTP {status}: {exc.message}"),
                    retryable=False,
                    status_code=status,
                )
            return cls(f"HTTP {status}: {exc.message}", retryable=True, status_code=status)
        if isinstance(exc, APIConnectionError):
            return cls(
                f"Сетевая ошибка OpenRouter: {exc}. Проверьте интернет-соединение.",
                retryable=True,
            )
        return cls(str(exc), retryable=False)


class ContextLengthExceededError(LLMError):
    """Ошибка превышения контекстного окна модели.

    Может быть поймана оркестратором для повторной попытки
    с урезанным контекстом (degraded mode).
    """

    def __init__(self, message: str, retryable: bool = True, status_code: int = 400) -> None:
        super().__init__(message, retryable=retryable, status_code=status_code)


def _extract_error_code(exc: APIStatusError) -> str | None:
    """Извлечь код ошибки из тела ответа 400."""
    try:
        body_str = exc.body
        if isinstance(body_str, (bytes, bytearray)):
            body_str = body_str.decode("utf-8", errors="replace")
        body: dict[str, Any] = json.loads(body_str) if isinstance(body_str, str) else {}
        code = body.get("error", {}).get("code")
        return str(code) if code is not None else None
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


async def with_retry(
    coro_factory: Callable[[], Coroutine[Any, Any, T]],
    max_attempts: int = 4,
    base_delay: float = 2.0,
) -> T:
    """
    Выполнить async-корутину с экспоненциальным backoff.

    Args:
        coro_factory: callable без аргументов, возвращающий coroutine
        max_attempts: максимальное число попыток
        base_delay: базовая задержка в секундах

    Raises:
        LLMError: если все попытки исчерпаны или ошибка non-retryable
    """
    last_err: LLMError | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await coro_factory()
        except (RateLimitError, APIStatusError, APIConnectionError) as exc:
            err = LLMError.from_openai(exc)
            if not err.retryable:
                raise err from exc
            last_err = err
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 1)
            logger.warning(f"[retry {attempt}/{max_attempts}] {err} — повтор через {delay:.1f}s")
            await asyncio.sleep(delay)
        except Exception as exc:
            raise LLMError(str(exc), retryable=False) from exc

    raise last_err or LLMError("Все попытки исчерпаны")
