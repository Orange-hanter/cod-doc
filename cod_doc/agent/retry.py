"""
Retry-логика для вызовов OpenRouter API.
Экспоненциальный backoff с jitter для rate limits и сетевых ошибок.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TypeVar

from openai import APIConnectionError, APIStatusError, RateLimitError

logger = logging.getLogger("cod_doc.agent.retry")

T = TypeVar("T")

# Ошибки, после которых смысла повторять нет
_FATAL_STATUSES = {400, 401, 403, 404, 422}


class LLMError(Exception):
    """Обёртка ошибок LLM с человекочитаемым сообщением."""

    def __init__(self, message: str, retryable: bool = False, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code

    @classmethod
    def from_openai(cls, exc: Exception) -> "LLMError":
        if isinstance(exc, RateLimitError):
            return cls(
                "OpenRouter: превышен лимит запросов (429). Подождите и повторите.",
                retryable=True,
                status_code=429,
            )
        if isinstance(exc, APIStatusError):
            status = exc.status_code
            if status in _FATAL_STATUSES:
                hints = {
                    401: "Неверный API-ключ. Проверьте COD_DOC_API_KEY.",
                    403: "Нет доступа. Проверьте права API-ключа.",
                    400: f"Неверный запрос: {exc.message}",
                    422: f"Модель отклонила запрос: {exc.message}",
                }
                return cls(hints.get(status, f"HTTP {status}: {exc.message}"), retryable=False, status_code=status)
            return cls(f"HTTP {status}: {exc.message}", retryable=True, status_code=status)
        if isinstance(exc, APIConnectionError):
            return cls(
                f"Сетевая ошибка OpenRouter: {exc}. Проверьте интернет-соединение.",
                retryable=True,
            )
        return cls(str(exc), retryable=False)


async def with_retry(coro_factory, max_attempts: int = 4, base_delay: float = 2.0):
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
            logger.warning(
                f"[retry {attempt}/{max_attempts}] {err} — повтор через {delay:.1f}s"
            )
            await asyncio.sleep(delay)
        except Exception as exc:
            raise LLMError(str(exc), retryable=False) from exc

    raise last_err or LLMError("Все попытки исчерпаны")
