"""Ошибки документов и секций — лист без зависимостей (SVC-004).

Их бросают и ловят модули по обе стороны `doc_service`: помощники
`link_service` и `projection_service` импортировали весь `doc_service` ради
одного класса исключения, и это было ребро кольца импортов без какой-либо
логики. Импортируй отсюда; `doc_service` реэкспортирует те же классы, так что
`doc_service.SectionNotFoundError` и старые `except` продолжают работать.
"""

from __future__ import annotations


class DocumentNotFoundError(LookupError):
    pass


class SectionNotFoundError(LookupError):
    pass


class SectionAlreadyExistsError(ValueError):
    pass
