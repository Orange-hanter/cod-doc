"""События документов — синхронная шина внутри транзакции (SVC-007).

`doc_service` пишет документы и секции, а производные данные — строки ссылок
и тела чужих секций, которые ссылаются на переименованное, — ведёт
`link_service`. Раньше `doc_service` звал `link_service` напрямую (импортами в
телах функций), а `link_service` правит тела через `doc_service.patch_section`:
взаимная рекурсия, последний цикл импортов ядра services.

Теперь `doc_service` только публикует факт, а кто на него реагирует, решает
точка сборки — `services/event_wiring.py`. Модуль — лист: ничего не
импортирует из `cod_doc`.

Обработчики исполняются **синхронно, в той же сессии и транзакции**, что и
публикация: каскад — часть записи, а не фоновая работа, и откат записи
откатывает каскад. События этого модуля **обязательны**: публикация без
подписчика падает `MissingSubscriberError`. Тихий пропуск означал бы битые
ссылки, которые никто не заметит; падение замечает первый же вызов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class SectionWritten:
    """Тело секции записано (`add_section`, `patch_section`): ссылки из него
    нужно перечитать. Подписчик обязан быть best-effort — см. `doc_service`."""

    session: Session
    section_id: int


@dataclass(frozen=True, slots=True)
class SectionAnchorChanged:
    """Якорь секции сменился (`rename_section`): ссылки на ``#old_anchor`` во всех
    документах проекта должны вести на ``#new_anchor``. Строка секции к моменту
    публикации уже несёт новый якорь."""

    session: Session
    project_id: int
    document_id: int
    old_anchor: str
    new_anchor: str
    author: str
    reason: str | None


@dataclass(frozen=True, slots=True)
class DocumentRenamed:
    """Сменились ``doc_key`` и/или путь документа (`rename`): входящие ссылки и
    канонические ссылки в телах нужно переписать. Строка документа к моменту
    публикации уже несёт новые значения."""

    session: Session
    project_id: int
    old_doc_key: str
    new_doc_key: str
    author: str
    reason: str | None
    path_map: dict[str, str] | None


@dataclass(frozen=True, slots=True)
class Rewrites:
    """Ответ подписчика `SectionAnchorChanged`: что переписано.

    ``rewritten`` — ``doc_key#anchor`` секций, чьи тела переписаны;
    ``links`` — сколько строк ссылок перенаправлено."""

    rewritten: list[str]
    links: int


class MissingSubscriberError(RuntimeError):
    """Событие опубликовано, а подписчиков нет: точка входа не вызвала
    `cod_doc.services.event_wiring.install()`."""


# Значение — обработчики события своего типа; связь «ключ ↔ тип аргумента»
# держит `subscribe`, а не аннотация словаря.
_HANDLERS: dict[type[object], list[Callable[[Any], object]]] = {}


def subscribe[E](event_type: type[E], handler: Callable[[E], object]) -> None:
    """Подписать ``handler`` на ``event_type``. Повторная подписка — no-op."""
    handlers = _HANDLERS.setdefault(event_type, [])
    if handler not in handlers:
        handlers.append(handler)


def is_subscribed(event_type: type[object]) -> bool:
    return bool(_HANDLERS.get(event_type))


def publish(event: object) -> list[object]:
    """Вызвать подписчиков события по порядку подписки; вернуть их ответы.

    Исключение подписчика летит к публикующему: каскад — часть записи.
    """
    handlers = _HANDLERS.get(type(event))
    if not handlers:
        raise MissingSubscriberError(
            f"no subscriber for {type(event).__name__}: the entry point must call "
            "cod_doc.services.event_wiring.install() before writing documents"
        )
    return [handler(event) for handler in handlers]
