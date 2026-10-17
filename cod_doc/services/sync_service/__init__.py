"""SyncService — синхронизация проекта с remote (RFC 29 §10.6).

Пакет по образцу ``story_service/``. Сейчас в нём формат changeset'а
(``changeset``, CLO-010), коды ошибок §10.10 (``errors``) и ``push``
(CLO-012); ``_local`` — состояние реплики относительно remote, ``_values`` —
форма значений на проводе.

Подмодули импортируются явно (``from cod_doc.services.sync_service import
changeset``): пакет ничего не реэкспортирует, чтобы импорт одного модуля не
тянул остальные.
"""
