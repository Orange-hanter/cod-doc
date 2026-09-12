---
type: standard
scope: code-quality
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-25
last_updated: 2026-08-25
related_docs:
  - ../ARCHITECTURE.md
  - ../../../AGENTS.md
---

# Code Quality Standard

> The code quality policy: what KISS/DRY/SOLID mean in this repository,
> which tool guards which rule, and how the ratchet of existing debt is organized.
> The single source of truth for the configuration is `pyproject.toml`; this document explains **why** it is the way it is.

## 1. Principles → tools

| Principle | Enforced by | Where |
|---|---|---|
| **Mandatory types** | mypy `strict` on all of `cod_doc/` + ruff `ANN` (lint-level: missing annotations are visible before mypy runs) | `[tool.mypy]`, `select += ANN` |
| **`Any` ban** | ruff `ANN401` — a bare `param: Any` / `-> Any` is forbidden; `**kwargs: Any` is allowed consciously (`allow-star-arg-any`); mypy `disallow_any_unimported` — `Any` does not leak from untyped dependencies | `[tool.ruff.lint.flake8-annotations]` |
| **Magic numbers ban** | ruff `PLR2004` — comparison with a numeric literal requires a named constant. Code only: in tests, literals are test data | `select += PLR2004` |
| **KISS (complexity)** | ruff `C901`: target 10, hard ceiling **15**. The threshold is never raised; existing offenders are in the ratchet (§3) | `[tool.ruff.lint.mccabe]` |
| **Dead code is a defect** | mypy `warn_unreachable`. False positives (mypy narrows member-expressions via calls) and forward-compatible guards are silenced with a **targeted** `# type: ignore[unreachable]` with a justification comment — see `agent/orchestrator.py:606`, `link_service/resolver.py:484` | `[tool.mypy]` |
| **Return discipline / perf minutiae / pathlib** | ruff `RET`, `PERF`, `PTH` | `select` |
| **DRY** | Not linted — caught at review. Rule of thumb: a third copy of a shape = extract into a shared module. Known debt: `_make_session`/`_require_project_id` duplicated across 8 CLI modules (`cli/*/`_common.py) — a candidate for consolidation |
| **SOLID (layer boundaries)** | Unidirectionality of `cli/tui/api/mcp → services → domain ← infra` — a contract of [ARCHITECTURE.md](../ARCHITECTURE.md); "new functionality = all four surfaces" — AGENTS.md §5. There is no automatic check of import directions yet (candidate: import-linter, as a separate decision) |

## 2. What we decided NOT to include (and why)

| Rule | Reason for refusal |
|---|---|
| mypy `disallow_any_explicit` | 502 errors: `dict[str, Any]` is a legitimate form of MCP/JSON payloads. Reconsider — only together with typing payloads via TypedDict |
| `PLR0913` (many arguments) | 97 violations: keyword-only service signatures (`task_service.create` etc.) — a conscious house style, and a threshold that passes the current code is meaningless |
| `ARG` (unused arguments) | Noise on interface implementations (click-callbacks, event-listeners, protocols) |
| `A002` (shadowing builtins in parameters) | `type=`, `id=`, `hash=` — public MCP/CLI parameter names mirroring domain fields; renaming = breaking the contract |

## 3. Ratchet: how existing debt is quenched

As of the policy's introduction (2026-08-25), `pyproject.toml
[tool.ruff.lint.per-file-ignores]` fixes the list of offender files:
**ANN401 — 40 places · PLR2004 — 39 · C901 — 14 functions · PERF401 — 25**
(those whose auto-fix ruff deemed unsafe).

Ratchet rules:

1. **The list only shrinks.** Fixed a file — remove its row from
   per-file-ignores in the same PR.
2. **New files are not added to the list.** A new entry in the ratchet block
   is a policy deviation and a subject of review, not a way to fix CI.
3. A targeted `# noqa: <RULE>` / `# type: ignore[<code>]` in new code
   is allowed only with a justification comment on the same line or the line
   above.
4. Removing a rule entirely — only by editing this standard with a record in §2.

## 4. Gate

Exactly the same as in CI (`.github/workflows/ci.yml`), without changes:

```bash
.venv/bin/ruff check cod_doc/ tests/
.venv/bin/ruff format --check cod_doc/ tests/
.venv/bin/mypy cod_doc/
.venv/bin/pytest tests/ --tb=short
```

⚠️ When running tests from a colored terminal session, unset `FORCE_COLOR`
(`env -u FORCE_COLOR`): rich colors the CLI output inside `CliRunner`, and string
assertions fail on ANSI codes.

## 5. A precedent of benefit (2026-08-25)

Enabling `ANN` immediately exposed a runtime bug: `cli/link.py:348`
unpacked a `sessionmaker` into two names
(`session_factory, _project_id_fn = _make_session(...)`) — the
`link suggest` command would fail with `TypeError` on the first run.
The untyped signature hid this from mypy strict.

## Changelog

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-08-25 | First revision: ANN/C901/PLR2004/RET/PERF/PTH + mypy warn_unreachable/disallow_any_unimported, ratchet mechanics |
