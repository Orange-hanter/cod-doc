"""Ingest adapter for ai-review JSON exports.

Dispatches on ``payload.version``. Only version 1 is supported today; any
other version raises ``ValueError`` so the caller knows the export must be
handled explicitly rather than silently best-efforted.
"""

from __future__ import annotations

import json
from typing import TextIO

from cod_doc.services.ingest_service.models import RawFinding

_KNOWN_VERSIONS = frozenset({1})


class AiReviewAdapter:
    """Parse ai-review JSON artifacts into ``RawFinding`` records."""

    def parse(self, stream: TextIO) -> list[RawFinding]:
        """Parse the export and return one ``RawFinding`` per finding/blocker."""
        payload = json.load(stream)
        if not isinstance(payload, dict):
            raise ValueError("ai_review payload must be a JSON object")
        version = payload.get("version")
        if version not in _KNOWN_VERSIONS:
            raise ValueError(f"Unsupported ai_review payload.version: {version!r}")
        return self._parse_v1(payload)

    def _parse_v1(self, payload: dict[str, object]) -> list[RawFinding]:
        pr = payload.get("pr") or {}
        if not isinstance(pr, dict):
            pr = {}
        source_ref = self._as_text(pr.get("number"))
        common_payload: dict[str, object] = {
            "version": 1,
            "head_sha": self._as_text(payload.get("headSha")),
            "review_mode": self._as_text(payload.get("reviewMode")),
        }

        findings_data = self._list_field(payload, "findings")
        blockers_data = self._list_field(payload, "blockers")
        return [
            self._raw_to_finding(raw, source_ref, common_payload)
            for raw in findings_data + blockers_data
        ]

    @staticmethod
    def _list_field(payload: dict[str, object], key: str) -> list[object]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        return []

    def _raw_to_finding(
        self,
        raw: object,
        source_ref: str,
        common_payload: dict[str, object],
    ) -> RawFinding:
        if not isinstance(raw, dict):
            raise ValueError("Each ai_review finding must be a JSON object")
        line_value = raw.get("line")
        line = None
        if isinstance(line_value, int) and line_value > 0:
            line = line_value
        return RawFinding(
            source="ai_review",
            source_ref=source_ref or None,
            title=self._as_text(raw.get("title")) or "(no title)",
            severity=self._normalize_severity(raw.get("severity")),
            kind=self._as_text(raw.get("model")) or None,
            body=self._as_text(raw.get("body")) or None,
            path=self._as_text(raw.get("file")) or None,
            line=line,
            confidence=self._normalize_confidence(raw.get("confidence")),
            fp=self._as_text(raw.get("fp")) or None,
            payload=common_payload,
            raw=raw,
        )

    @staticmethod
    def _normalize_severity(value: object) -> str:
        mapping = {
            "critical": "critical",
            "major": "major",
            "minor": "minor",
            "nit": "info",
        }
        if isinstance(value, str) and value in mapping:
            return mapping[value]
        return "info"

    @staticmethod
    def _normalize_confidence(value: object) -> float | None:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
            if 0.0 <= number <= 1.0:
                return number
        return None

    @staticmethod
    def _as_text(value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()
