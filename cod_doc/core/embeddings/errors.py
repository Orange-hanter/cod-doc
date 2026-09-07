"""Ошибки и синхронные ретраи эмбеддера (ADO-071).

Почему это не переиспользует ``agent/retry.py``:

1. **Слой.** ``agent/retry.py`` живёт в ``agent/``, а ``core/embeddings``
   импортируется из ``services/``; импорт вверх завёл бы новую дугу.
2. **Sync vs async — блокирующая причина.** ``with_retry`` это ``async def``
   и принимает корутин-фабрику, а контракт chroma
   ``EmbeddingFunction.__call__`` синхронный. Крутить event loop внутри
   синхронного вызова внутри chroma-потоков — генератор дедлоков.
3. **Семантика.** У чата важны 429 и ``context_length_exceeded``; у эмбеддера
   — «у провайдера вообще нет /embeddings» (ровно случай Ollama Cloud),
   «dimensions должен быть одним из N» и «кончились кредиты».

Форма (поля, классификация, backoff с jitter) намеренно повторяет
``agent/retry.py`` — это осознанный синхронный близнец, а не забытый дубликат.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Mapping  # рантайм: isinstance в разборе тела ошибки
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger("cod_doc.core.embeddings")

T = TypeVar("T")

HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_PAYMENT_REQUIRED = 402
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR = 500

_FATAL_STATUSES = frozenset(
    {
        HTTP_BAD_REQUEST,
        HTTP_UNAUTHORIZED,
        HTTP_PAYMENT_REQUIRED,
        HTTP_FORBIDDEN,
        HTTP_NOT_FOUND,
        HTTP_UNPROCESSABLE,
    }
)

_MAX_ERROR_NESTING = 4
"""OpenRouter кладёт апстримную ошибку JSON-строкой внутрь error.message;
у некоторых провайдеров вложенность двойная. Больше четырёх уровней не
встречалось — глубже не разбираем, чтобы не крутиться на мусоре."""

_DEFAULT_ATTEMPTS = 6
_DEFAULT_BASE_DELAY = 2.0
_MAX_BACKOFF_S = 30.0


class EmbeddingError(Exception):
    """Ошибка эмбеддера, уже переведённая с языка провайдера на человеческий."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        hint: str = "",
    ) -> None:
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        self.hint = hint

    def __str__(self) -> str:
        return f"{self.message} {self.hint}".strip()

    @classmethod
    def from_provider(
        cls,
        exc: Exception,
        *,
        backend: str,
        model: str,
        base_url: str,
    ) -> EmbeddingError:
        """Перевести исключение openai-SDK в ``EmbeddingError``."""
        status = _status_code_of(exc)
        message = _deepest_message(_raw_message_of(exc))
        hint = _hint_for(status, message, backend=backend, model=model, base_url=base_url)
        retryable = (
            status is None or status >= HTTP_SERVER_ERROR or status == HTTP_TOO_MANY_REQUESTS
        )
        return cls(
            f"{backend}: {message}" if message else f"{backend}: {exc!r}",
            retryable=retryable and status not in _FATAL_STATUSES,
            status_code=status,
            hint=hint,
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> EmbeddingError:
        """Ошибка, приехавшая телом при HTTP 200 (так умеет OpenRouter)."""
        raw = payload.get("error")
        message = _deepest_message(raw)
        status = None
        if isinstance(raw, Mapping) and isinstance(raw.get("code"), int):
            status = int(raw["code"])  # у OpenRouter code — int, а не строка как у OpenAI
        return cls(
            f"провайдер вернул ошибку в теле ответа: {message}",
            retryable=status == HTTP_TOO_MANY_REQUESTS,
            status_code=status,
        )


class EmbeddingConfigError(EmbeddingError):
    """Чинится правкой конфига — ретраить бессмысленно."""


class EmbeddingDimensionMismatch(EmbeddingError):
    """Коллекция построена другим эмбеддером; векторы несовместимы."""


def _status_code_of(exc: Exception) -> int | None:
    status = getattr(exc, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _raw_message_of(exc: Exception) -> object:
    body = getattr(exc, "body", None)
    if isinstance(body, Mapping):
        error = body.get("error")
        if error is not None:
            return error
        return body
    return str(exc)


def _deepest_message(raw: object) -> str:
    """Достать самое внутреннее сообщение об ошибке.

    OpenRouter отдаёт ``{"error": {"message": "HTTP 400: {\\"message\\": …}"}}``
    — апстримная ошибка вложена **строкой**, и наивный вывод показал бы
    пользователю JSON вместо причины.
    """
    if isinstance(raw, Mapping):
        inner = raw.get("message")
        msg = str(inner) if inner is not None else str(raw)
    else:
        msg = str(raw)

    for _ in range(_MAX_ERROR_NESTING):
        start = msg.find("{")
        if start < 0:
            break
        try:
            parsed = json.loads(msg[start:])
        except json.JSONDecodeError:
            break
        if not isinstance(parsed, dict):
            break
        nested = parsed.get("message")
        if nested is None and isinstance(parsed.get("error"), dict):
            nested = parsed["error"].get("message")
        if not isinstance(nested, str) or nested == msg:
            break
        msg = nested
    return msg.strip()


def _hint_for(
    status: int | None,
    message: str,
    *,
    backend: str,
    model: str,
    base_url: str,
) -> str:
    """Подсказка, которая экономит пользователю час — по коду и тексту ошибки."""
    if status == HTTP_NOT_FOUND:
        return (
            f"У провайдера по адресу {base_url} нет маршрута /embeddings "
            "(так ведёт себя Ollama Cloud — у него эмбеддингов нет вовсе). "
            "Поставьте embedding_backend=openrouter и embedding_api_key."
        )
    if status == HTTP_UNAUTHORIZED:
        return (
            f"Ключ для backend={backend} не принят. Ключ эмбеддера задаётся "
            "отдельно (embedding_api_key) и не наследуется от ключа LLM."
        )
    if status == HTTP_PAYMENT_REQUIRED:
        return "Недостаточно кредитов у провайдера эмбеддингов."
    if status == HTTP_BAD_REQUEST and "dimensions" in message.lower():
        return (
            f"Модель {model} не поддерживает такую размерность. "
            "Уберите embedding_dimensions или возьмите значение из сообщения выше."
        )
    if status == HTTP_TOO_MANY_REQUESTS:
        return "Лимит запросов провайдера; уменьшите embedding_batch_size."
    return ""


def sync_retry(
    call: Callable[[], T],
    *,
    attempts: int = _DEFAULT_ATTEMPTS,
    base_delay: float = _DEFAULT_BASE_DELAY,
) -> T:
    """Синхронный экспоненциальный backoff с jitter.

    Ретраит только то, что адаптер пометил ``retryable``: 429, 5xx и обрывы
    соединения. Конфигурационные ошибки (400/401/402/404) проходят насквозь —
    ретраить их значит греть провайдера и прятать причину от пользователя.
    """
    last: EmbeddingError | None = None
    for attempt in range(attempts):
        try:
            return call()
        except EmbeddingError as exc:
            last = exc
            if not exc.retryable or attempt == attempts - 1:
                raise
            delay = min(base_delay * (2**attempt), _MAX_BACKOFF_S)
            delay += random.uniform(0, delay / 2)
            logger.debug(
                "embedding retry %d/%d after %.1fs: %s", attempt + 1, attempts, delay, exc.message
            )
            time.sleep(delay)
    raise last if last else EmbeddingError("retry loop exited without result")
