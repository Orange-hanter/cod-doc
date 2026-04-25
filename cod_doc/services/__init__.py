"""Service layer — encapsulates write-paths over the data model.

Each service operates on a SQLAlchemy `Session` passed by the caller; commit/
rollback is the caller's responsibility (typically `transactional()` from
`cod_doc.infra.db`).
"""
