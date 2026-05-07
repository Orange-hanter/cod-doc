"""COD-070: SQL-side ordering helpers shared across service modules.

Priority is stored as a string (``"critical"|"high"|"medium"|"low"``), so a
naive ``ORDER BY priority`` returns alphabetic order: critical, high, low,
medium. Use ``priority_sql_order`` to get a CASE expression that yields the
intended semantic order (critical → high → medium → low).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import case

from cod_doc.domain.entities import Priority

if TYPE_CHECKING:
    from sqlalchemy.sql import ColumnElement


_PRIORITY_RANK = {
    Priority.CRITICAL.value: 0,
    Priority.HIGH.value: 1,
    Priority.MEDIUM.value: 2,
    Priority.LOW.value: 3,
}


def priority_sql_order(col: Any) -> ColumnElement[Any]:
    """Return a SQL CASE expression mapping ``col`` to its semantic rank.

    ``ORDER BY priority_sql_order(TaskModel.priority)`` yields critical-first.
    Unknown values map to a high rank (99) so they sink to the bottom rather
    than crashing the query.

    Param type is intentionally ``Any`` at the boundary — SQLAlchemy's ORM
    ``InstrumentedAttribute`` and ``ColumnElement`` participate in the same
    SQL-AST but are not subtype-related in mypy, and the structural typing
    here adds no runtime safety. Callers pass either freely.
    """
    return case(
        _PRIORITY_RANK,
        value=col,
        else_=99,
    )
