"""SYM-010: чистые инварианты drift-гейта — маркер, рендер, фингерпринты.

Тесты без БД: сбор находок покрыт через CLI (`tests/cli/test_ctx_drift_gate.py`),
здесь — то, на чём держится идемпотентность комментария и дедуп на стороне
движка.
"""

from __future__ import annotations

from cod_doc.services import drift_gate_service as gate
from cod_doc.services.finding_service import fingerprint_drift_gate


def _finding(**kw) -> gate.GateFinding:
    defaults = {
        "path": "docs/a.md",
        "doc_key": "docs/a",
        "rule": "link",
        "code": "LINK-BROKEN",
        "severity": "major",
        "title": "Ссылка не резолвится: ADR-001",
        "body": "`docs/a#changelog` → `ADR-001` (adr): adr not found: ADR-001.",
        "fp": "deadbeef",
    }
    return gate.GateFinding(**{**defaults, **kw})


def _report(findings: list[gate.GateFinding]) -> gate.GateReport:
    return gate.GateReport(
        project="orakul",
        changed_files=["docs/a.md"],
        scanned_docs=1,
        findings=findings,
        drift_counts={"in_sync": 1, "stale_export": 0, "edited_in_place": 0, "missing": 0},
    )


def test_marker_is_namespaced_per_project() -> None:
    assert gate.marker("orakul") == "<!-- cod-doc:drift-gate:orakul -->"
    assert gate.marker("orakul") != gate.marker("cod-doc")


def test_comment_body_is_byte_stable_for_equal_reports() -> None:
    """Идемпотентность держится на этом: одинаковый вход → одинаковое тело."""
    first = gate.render_comment(_report([_finding()]), pr=562)
    second = gate.render_comment(_report([_finding()]), pr=562)
    assert first == second


def test_comment_always_carries_the_marker() -> None:
    for findings in ([], [_finding()]):
        body = gate.render_comment(_report(findings), pr=1)
        assert body.startswith(gate.marker("orakul"))


def test_empty_report_says_so_explicitly() -> None:
    body = gate.render_comment(_report([]), pr=1)
    assert "Находок нет." in body


def test_pipes_in_findings_do_not_break_the_table() -> None:
    body = gate.render_comment(_report([_finding(body="a | b")]), pr=1)
    row = next(line for line in body.splitlines() if "LINK-BROKEN" in line and line.startswith("|"))
    assert "a \\| b" in row, "pipe внутри находки обязан быть экранирован"
    assert row.replace("\\|", "").count("|") == 5, "разъехалась разметка строки таблицы"


def test_engine_shape_carries_prescan_and_model() -> None:
    payload = _report([_finding()]).as_dict()
    assert payload["model"] == "cod-doc/drift"
    assert payload["prescan"] is True
    assert payload["findings"][0]["model"] == "cod-doc/drift"
    assert payload["findings"][0]["prescan"] is True


def test_counts_by_rule_covers_all_three_classes() -> None:
    report = _report(
        [
            _finding(rule="link"),
            _finding(rule="drift", code="DRIFT-EDITED_IN_PLACE"),
            _finding(rule="frontmatter", code="FM-002"),
        ]
    )
    assert report.counts_by_rule == {"drift": 1, "link": 1, "frontmatter": 1}
    assert report.finding_count == 3


def test_fingerprint_is_stable_and_discriminating() -> None:
    args = {"path": "docs/a.md", "rule": "link", "code": "LINK-BROKEN", "subject": "changelog|X"}
    first, basis = fingerprint_drift_gate(**args)
    second, _ = fingerprint_drift_gate(**args)
    assert first == second
    assert basis["fp_basis"] == "path_rule_code_subject"

    other, _ = fingerprint_drift_gate(**{**args, "subject": "changelog|Y"})
    assert other != first
