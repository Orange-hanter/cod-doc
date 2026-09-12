---
type: audit-report
scope: cod_doc/* (Section G — hardening & DevX completion)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-05-01
last_updated: 2026-05-01
audit_target_revision: post COD-024a + COD-014a + COD-025 + COD-026
related_docs:
  - ../MASTER.md
  - ../roadmap/cod-doc-task-plan.md
  - 2026-04-28-section-c-capabilities.md
---

# Section G (Hardening & DevX) — Completion Report

> Closes 5 tasks of the section: CI gates (COD-024, previously), strict lint/type debt cleanup (COD-024a), markdown-relative rename cascade (COD-014a), sensitive-data infrastructure (COD-025), TUI smoke tests (COD-026).

## Summary

| ID | Title | Status | Tests | Notes |
|----|-------|--------|------:|-------|
| COD-024  | CI workflow (pytest+mypy+ruff) | done (prev) | — | mypy now blocking |
| COD-024a | clean ruff/mypy debt | done | suite green | OpenAI overload + FastMCP API + TUI BindingType |
| COD-014a | markdown-relative rename cascade | done | +6 | path_map in LinkService, per-section rewrite |
| COD-025  | Sensitive-data infrastructure | done | +36 | scanner+FM-007+SD-001 audit+SD-002 redaction+clearance helper |
| COD-026  | TUI smoke tests | done | +9 | bug in WizardScreen surfaced and fixed |

**Total tests added:** +51 (from 351 to 402). **Suite at the end of the section:** 402/402 ✅. **CI gates:** ruff/mypy/pytest all blocking ✅.

## Detailed results

### COD-024a — strict-mode debt cleared

Starting state: 407 ruff lint + 59 unformatted + 101 mypy strict in 28 files. After two partial commits, 6 ruff + 32 mypy remained. Closure:

- **OpenAI SDK overload** in `agent/orchestrator.py:154,234` — added `# type: ignore[call-overload,misc]`. Alternatives (cast to TypedDict types from `openai.types.chat`) — too invasive for two call-sites.
- **`tool_calls` union** — filter `if tc.type != "function": continue` (new union-alternative `ChatCompletionMessageCustomToolCall` in the openai SDK).
- **FastMCP API drift** in `cli/cmd_serve.py` + `mcp/server.py` — `host`/`port`/`stateless_http`/`transport` literals moved to `mcp.settings`. The SDK updated, kwargs removed.
- **TUI `BINDINGS` invariance** — `list[Binding]` → `list[BindingType]` (covariant union from `textual.binding`). Unified across all 5 screens + `app.py`.
- **`_StepBar._render` override** in `wizard.py` — renamed to `_refresh_label` so it does not conflict with `Static._render` → `Visual`.
- **bare `dict`** (~10 places) — `dict[str, Any]`; `cast(dict[str, Any], …)` for JSON loading.
- **`collections.abc` TC003** — Callable/Coroutine/AsyncGenerator moved to `TYPE_CHECKING`.
- **73 files** auto-formatted with `ruff format`.

CI: `continue-on-error` removed from the mypy job. The ruff job was removed earlier.

### COD-014a — markdown-relative cascade

Design:
- `rename_cascade(..., path_map: dict[str, str] | None)` — a dict for forward-compat (bulk rename), a single doc passes `{old_path: target_path}`.
- Helpers: `_resolve_md_href` (normalizes via `posixpath.normpath` relative to the source-dir), `_make_relative_href`, `_rewrite_markdown_relative_refs`.
- **Expanded search for affected sections.** `parse()` drops `../` prefixes when deriving `to_doc_key`, so for nested folders `to_doc_key` is NOT equal to the canonical one. When `path_map` is set — pull all sections with `LinkKind` ∈ {MARKDOWN, SECTION} and filter by actual body change. Trade-off: O(N) sections, but O(1) per body-rewrite (most bodies do not change).
- `link.raw` for markdown-rows is also rewritten — re-resolve stays consistent.
- `DocService.rename` builds a path_map when `new_path` differs, and also cascades on a path-only change (when the doc_key does not change).

Tests: markdown-rewrite via path_map, path-only rename, anchor preservation, idempotency, URL/anchor skip, end-to-end DocService.rename.

### COD-025 — sensitive-data (3 of 4 components inline; clearance — preview)

Delivered:
- **SD-001 SensitivityScanner** — `services/sensitivity_scanner.py`. 5 high-conf patterns (`aws_access_key`, `github_pat`, `slack_token`, `pem_private_key`, `jwt_token`) + generic high-entropy (cap 25/doc) + PII window email+phone. Snippets partially masked.
- **SD-001 audit** — `validation.audit_sensitivity` (advisory): high-conf secrets in public/internal → error, in confidential/restricted → warning; PII always warning.
- **SD-002 Redaction** — `ProjectionService.render_markdown(audience=...)` + `export_document(audience=...)`. Audience tier ranking; refuses to replace body with `> [content redacted: <level> — see DB]`. Audience-specific export does NOT update `projection_hash`.
- **SD-003 helper** — `sensitivity_scanner.clearance_meets(actor, doc)`. Pure helper; unified semantics for the future ContextService and audit/redaction.
- **FM-007** — warning when `sensitivity` is missing for `module-spec`/`architecture`/`standard`.

**Deferred:**
- `ContextService.get(actor, …)` filter — the `agent_definition` table does not exist yet (Section D). `clearance_meets` — ready API when the service appears.
- Migration `0007_agent_clearance.py` — deferred to Section D together with `agent_definition`.
- CLI `cod-doc audit --sensitivity` — in COD-031 (CLI audit gate).

### COD-026 — TUI smoke

9 tests: 3 boot (wizard/dashboard routing + `q`-quit), 6 screen mounts (WizardScreen, DashboardScreen + 2 dialogs, AgentRunScreen + refresh action). Uses `App.run_test()` + `Pilot.pause()`.

**Bug surfaced:** WizardScreen used `id=f"model-{model_id}"` where `model_id` contains `/` and `.` (for example `anthropic/claude-sonnet-4-6`). Textual `BadIdentifier`. Added a `_model_widget_id()` helper. This is exactly the class of bugs that smoke tests exist for — without them the bug would have gone to production and broken the wizard for the very first user.

## Surfaced risks (non-blocking)

1. **OpenAI SDK overload** — `# type: ignore` on two call-sites. If in the future the openai SDK changes the messages/tools shape, the type-ignore will mask the regression. Mitigation: 5 `test_orchestrator.py` tests run in every CI and check the structure against mocks.
2. **chromadb generic variance** — `# type: ignore[arg-type]` on `embedding_function`. The chromadb stubs are stricter than runtime; a known gap, an open bug upstream.
3. **`affected_section_ids` in COD-014a** with path_map — O(N) scan over all sections with MD/SECTION links. On projects <10K sections — acceptable. As it grows — an index on `link.raw LIKE '%.md%'` or a separate path-target column.

## Metrics

- Files changed: 18 (cod_doc), 4 (tests), 2 (CI/roadmap).
- Tests added: 51 (5 link-cascade, 36 sensitivity, 9 TUI; +1 mock-fix in test_orchestrator).
- LOC: +740 / -120 (≈ services/sensitivity_scanner 220 LOC + tests 480 LOC + delta link/projection/validation).
- Coverage: TUI exited the "0 tests" zone; the sensitivity flow is covered e2e (scanner → audit → redaction).

## Section closure

All 5 G-Hardening tasks are closed. Roadmap counters updated: G 5/5 done, TOTAL 20/31. The next focus area — Section D (MCP & CLI), which is already partially done as COD-030/031/032 (3 of 4 tasks), and Section E (Retrieval) — ContextService will be restored there and SD-003 closed.
