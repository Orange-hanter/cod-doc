"""Ingest adapter for ZAIrgRush experiment findings (JSON Lines).

Expected shape per line::

    {
      "ts": "2026-08-07T00:00:00",
      "exp": "SMOKE-1",
      "variant": "raw-manual",
      "task": "T-a1b2",
      "kind": "defect",
      "note": "...",
      "metric_ref": "run-smoke1"
    }

The ``note`` becomes ``title`` (truncated) and ``body`` (full text).
``kind`` is mapped to a normalized severity.
"""

from __future__ import annotations

import json
from typing import TextIO

from cod_doc.services.ingest_service.models import RawFinding

_TITLE_MAX = 200


class ZairgrushFindingsAdapter:
    """Parse ZAIrgRush ``findings.jsonl`` exports into ``RawFinding`` records."""

    def parse(self, stream: TextIO) -> list[RawFinding]:
        """Parse the JSONL stream and return one ``RawFinding`` per line."""
        findings: list[RawFinding] = []
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
            findings.append(self._record_to_finding(record))
        return findings

    def _record_to_finding(self, record: dict[str, object]) -> RawFinding:
        note = self._as_text(record.get("note"))
        title = note if len(note) <= _TITLE_MAX else note[:_TITLE_MAX]
        kind = self._as_text(record.get("kind")) or "unknown"
        return RawFinding(
            source="zairgrush",
            source_ref=self._as_text(record.get("task")) or None,
            title=title or "(no note)",
            severity=self._kind_to_severity(kind),
            kind=kind,
            body=note or None,
            path=None,
            line=None,
            confidence=None,
            exp=self._as_text(record.get("exp")) or None,
            variant=self._as_text(record.get("variant")) or None,
            payload={
                "ts": self._as_text(record.get("ts")),
                "metric_ref": self._as_text(record.get("metric_ref")),
            },
            raw=record,
        )

    @staticmethod
    def _kind_to_severity(kind: str) -> str:
        mapping = {"defect": "major", "surprise": "minor"}
        return mapping.get(kind, "info")

    @staticmethod
    def _as_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()
