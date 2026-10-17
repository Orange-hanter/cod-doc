"""SyncService — синхронизация проекта с remote (RFC 29 §10.6).

Пакет по образцу ``story_service/``. Сейчас в нём формат changeset'а
(``changeset``, CLO-010) и коды ошибок §10.10 (``errors``); ``push``/``pull``
приходят следующими задачами секции B.

Подмодули импортируются явно (``from cod_doc.services.sync_service import
changeset``): пакет ничего не реэкспортирует, чтобы импорт одного модуля не
тянул остальные.
"""
