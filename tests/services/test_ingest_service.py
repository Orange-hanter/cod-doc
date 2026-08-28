"""SYM-006A: ingest adapter registry and parser tests."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from cod_doc.services.ingest_service import (
    INGEST_ADAPTERS,
    lookup_adapter,
)
from cod_doc.services.ingest_service.ai_review import AiReviewAdapter
from cod_doc.services.ingest_service.zairgrush_findings import (
    ZairgrushFindingsAdapter,
)
from cod_doc.services.ingest_service.zairgrush_tasks import (
    ZairgrushTasksAdapter,
)

FIXTURES = Path(__file__).parent / "fixtures" / "ingest"


# --------------------------------------------------------------------------- #
# Registry tests                                                              #
# --------------------------------------------------------------------------- #


class TestRegistry:
    def test_registry_contains_expected_adapters(self) -> None:
        assert set(INGEST_ADAPTERS) == {
            "ai_review",
            "zairgrush_findings",
            "zairgrush_tasks",
        }

    @pytest.mark.parametrize(
        ("name", "expected_class"),
        [
            ("ai_review", AiReviewAdapter),
            ("zairgrush_findings", ZairgrushFindingsAdapter),
            ("zairgrush_tasks", ZairgrushTasksAdapter),
        ],
    )
    def test_lookup_returns_registered_adapter(self, name: str, expected_class: type[Any]) -> None:
        adapter = lookup_adapter(name)
        assert isinstance(adapter, expected_class)
        assert adapter is INGEST_ADAPTERS[name]

    def test_lookup_unknown_adapter_raises(self) -> None:
        with pytest.raises(KeyError, match="Unknown ingest adapter"):
            lookup_adapter("does_not_exist")


# --------------------------------------------------------------------------- #
# ai_review adapter tests                                                     #
# --------------------------------------------------------------------------- #


class TestAiReviewAdapter:
    def test_parses_real_v1_export(self) -> None:
        adapter = AiReviewAdapter()
        fixture = FIXTURES / "ai_review_v1.json"
        with fixture.open(encoding="utf-8") as stream:
            findings = adapter.parse(stream)

        assert len(findings) == 1
        finding = findings[0]
        assert finding.source == "ai_review"
        assert finding.title == "ast-grep: raw-x-forwarded-for"
        assert finding.severity == "critical"
        assert finding.path == "server/auth.ts"
        assert finding.line == 42
        assert finding.kind == "astgrep-prescan"
        assert finding.confidence == 1.0
        assert finding.source_ref == "257"
        assert finding.fp is None

        seed = finding.to_seed()
        assert len(seed.fingerprint) == 64
        assert seed.payload["fp_basis"] == "title"
        assert seed.raw is not None
        assert seed.raw.get("ruleId") == "raw-x-forwarded-for"

    def test_blockers_are_ingested_alongside_findings(self) -> None:
        adapter = AiReviewAdapter()
        payload = {
            "version": 1,
            "pr": {"number": 1},
            "findings": [],
            "blockers": [
                {
                    "file": "a.py",
                    "line": 10,
                    "severity": "major",
                    "title": "blocker",
                    "body": "blocks merge",
                    "model": "m1",
                }
            ],
        }
        findings = adapter.parse(io.StringIO(_json_dumps(payload)))
        assert len(findings) == 1
        assert findings[0].title == "blocker"

    @pytest.mark.parametrize(
        "version",
        [2, "2", None, 0],
    )
    def test_unknown_version_raises(self, version: object) -> None:
        adapter = AiReviewAdapter()
        payload = {"version": version, "findings": [], "blockers": []}
        stream = io.StringIO(_json_dumps(payload))
        with pytest.raises(ValueError, match=r"Unsupported ai_review payload\.version"):
            adapter.parse(stream)

    def test_nit_severity_normalizes_to_info(self) -> None:
        adapter = AiReviewAdapter()
        payload = {
            "version": 1,
            "pr": {"number": 1},
            "findings": [
                {
                    "file": "a.py",
                    "line": 1,
                    "severity": "nit",
                    "title": "style",
                    "model": "m1",
                }
            ],
            "blockers": [],
        }
        findings = adapter.parse(io.StringIO(_json_dumps(payload)))
        assert findings[0].severity == "info"


# --------------------------------------------------------------------------- #
# ZAIrgRush findings adapter tests                                            #
# --------------------------------------------------------------------------- #


class TestZairgrushFindingsAdapter:
    def test_parses_real_findings_jsonl(self) -> None:
        adapter = ZairgrushFindingsAdapter()
        fixture = FIXTURES / "zairgrush_findings.jsonl"
        with fixture.open(encoding="utf-8") as stream:
            findings = adapter.parse(stream)

        assert len(findings) == 10
        assert all(f.source == "zairgrush" for f in findings)
        assert all(f.exp == "SMOKE-1" for f in findings)
        assert all(f.variant == "raw-manual" for f in findings)

        defect = next(f for f in findings if f.kind == "defect")
        assert defect.severity == "major"
        assert defect.source_ref == "T-a1b2"
        assert defect.body is not None
        seed = defect.to_seed()
        assert len(seed.fingerprint) == 64
        assert seed.payload["fp_basis"] == "exp_variant_kind"

        surprise = next(f for f in findings if f.kind == "surprise")
        assert surprise.severity == "minor"

        observation = next(f for f in findings if f.kind == "observation")
        assert observation.severity == "info"

    def test_malformed_json_line_raises(self) -> None:
        adapter = ZairgrushFindingsAdapter()
        stream = io.StringIO('{"valid": true}\nnot json\n')
        with pytest.raises(ValueError, match="Invalid JSON on line 2"):
            adapter.parse(stream)


# --------------------------------------------------------------------------- #
# ZAIrgRush tasks adapter tests                                               #
# --------------------------------------------------------------------------- #


class TestZairgrushTasksAdapter:
    def test_parses_real_tasks_jsonl(self) -> None:
        adapter = ZairgrushTasksAdapter()
        fixture = FIXTURES / "zairgrush_tasks.jsonl"
        with fixture.open(encoding="utf-8") as stream:
            tasks = adapter.parse(stream)

        assert len(tasks) == 19
        assert all(t.source == "zairgrush" for t in tasks)
        assert all(t.exp == "tasks" for t in tasks)

        first = tasks[0]
        assert first.source_ref == "p1fn"
        assert first.variant == "p1fn"
        assert first.kind == "feature-tests"
        assert first.title == "load: номер листа больше 999 обязан диагностироваться при загрузке"
        seed = first.to_seed()
        assert len(seed.fingerprint) == 64
        assert seed.payload["fp_basis"] == "exp_variant_kind"

    def test_task_body_includes_context_fields(self) -> None:
        adapter = ZairgrushTasksAdapter()
        stream = io.StringIO(
            '{"id": "x1", "title": "t", "type": "feature", "status": "blocked", '
            '"diagnosis": "hard", "deps": ["a", "b"]}\n'
        )
        tasks = adapter.parse(stream)
        assert len(tasks) == 1
        assert tasks[0].severity == "major"
        assert tasks[0].body is not None
        assert "diagnosis: hard" in tasks[0].body
        assert "deps: ['a', 'b']" in tasks[0].body


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _json_dumps(value: dict[str, object]) -> str:
    import json

    return json.dumps(value)
