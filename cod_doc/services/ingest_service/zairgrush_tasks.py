"""Ingest adapter for ZAIrgRush task exports (JSON Lines).

Expected shape per line::

    {
      "id": "p1fn",
      "title": "load: номер листа больше 999 ...",
      "type": "feature-tests",
      "status": "done",
      "human_decisions": [...],
      "diagnosis": null,
      "commit": "d8d18e3",
      "deps": []
    }

Tasks have no ``exp``/``variant`` fields, so the task id is used as the
variant in the fingerprint while ``kind`` keeps the task type. This gives
each task a stable, unique identity without changing the fingerprint formula.
"""

from __future__ import annotations

import json
from typing import TextIO

from cod_doc.services.ingest_service.models import RawFinding


class ZairgrushTasksAdapter:
    """Parse ZAIrgRush ``tasks.jsonl`` exports into ``RawFinding`` records."""

    def parse(self, stream: TextIO) -> list[RawFinding]:
        """Parse the JSONL stream and return one ``RawFinding`` per task."""
        tasks: list[RawFinding] = []
        for line_no, line in enumerate(stream, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"Expected JSON object on line {line_no}")
            tasks.append(self._record_to_finding(record))
        return tasks

    def _record_to_finding(self, record: dict[str, object]) -> RawFinding:
        task_id = self._as_text(record.get("id"))
        task_type = self._as_text(record.get("type")) or "unknown"
        title = self._as_text(record.get("title")) or task_id or "(no title)"
        body_parts: list[str] = []
        for key in ("diagnosis", "human_decisions", "deps"):
            value = record.get(key)
            if value not in (None, "", []):
                body_parts.append(f"{key}: {value}")
        body = "\n".join(body_parts) or None
        return RawFinding(
            source="zairgrush",
            source_ref=task_id or None,
            title=title,
            severity=self._status_to_severity(self._as_text(record.get("status"))),
            kind=task_type,
            body=body,
            path=None,
            line=None,
            confidence=None,
            exp="tasks",
            variant=task_id or "unknown",
            payload={
                "commit": self._as_text(record.get("commit")),
                "status": self._as_text(record.get("status")),
            },
            raw=record,
        )

    @staticmethod
    def _status_to_severity(status: str) -> str:
        mapping = {"blocked": "major", "done": "info", "todo": "minor"}
        return mapping.get(status, "info")

    @staticmethod
    def _as_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()
