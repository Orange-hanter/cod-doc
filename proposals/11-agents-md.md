# 11 — `AGENTS.md` as a contract for contributors

> Category: 🔵 Architecture · Risk: low · Dependencies: 01

## Context: like paperclip

The root [`AGENTS.md`](https://github.com/paperclipai/paperclip/blob/master/AGENTS.md) — a single document for **human and AI contributors**:

Structure:
1. **Purpose** — what the project does, the current iteration.
2. **Read This First** — an ordered list of docs mandatory to read.
3. **Repo Map** — where things live.
4. **Dev Setup** — how to run locally.
5. **Core Engineering Rules** — key invariants ("single-assignee model", "atomic checkout", "approval gates").
6. **Database Change Workflow** — a step-by-step recipe.
7. **Verification Before Hand-off** — what to run before a PR.
8. **API and Auth Expectations** — mandatory patterns.
9. **UI Expectations** — mandatory patterns.
10. **Pull Request Requirements** — a mandatory template + a "Model Used" field.
11. **Definition of Done** — a formal checklist.

Effect:
- An AI agent does not need to guess conventions — reads one file and acts.
- A human — the same + a single point of rule updates.
- The **Model Used** field in the PR template makes explicit which model wrote the code (audit, transparency).

## Current state of cod-doc

There is `MASTER.md` at the root — but it is a **navigator over the project**, not a contract "how to work with this repo".

What is missing or scattered:
- Quick commands (lint/test/build) are in `MASTER.md` § 4 "Quick Actions", but not singled out as a contract.
- Engineering rules (where which modules are, how to write code) — partially in `arch/` and `specs/`, not consolidated.
- Definition of Done — not fixed anywhere.
- PR-template — none (or no special structure).
- Memory rules (FM-escalations, audit-cadence) live in the user's personal memory, not available to a contributor.

## Proposal

### 1. Create `AGENTS.md` at the root

Do not duplicate `MASTER.md`, but **complement it** — `MASTER.md` is about **what is in the project**, `AGENTS.md` is about **how to work with the project**.

Minimal structure:

```markdown
# AGENTS.md — Contributor Guide (people & AI)

## 1. Purpose
COD-DOC — an automated documentation management system with MCP integration.
Current iteration: <see MASTER.md → Project Status>.

## 2. Read first
1. `MASTER.md` — project map.
2. `arch/architecture.md` — architecture.
3. `specs/modules.md` — module contracts.
4. `cod_doc/skills/orchestrator/SKILL.md` — agent heartbeat protocol (see proposal 01).

## 3. Repo map
- `cod_doc/agent/` — orchestrator and LLM interaction
- `cod_doc/mcp/` — MCP server and tools
- `cod_doc/core/` — domain models
- `cod_doc/skills/` — modular instructions for the agent
- `arch/`, `specs/`, `models/`, `docs/` — project specifications
- `proposals/` — improvement RFCs
- `tests/` — pytest

## 4. Dev setup
pip install -e .[dev]
pytest tests/ -v --tb=short

## 5. Core rules
1. **Hash-verified docs.** Any doc change → recompute sha → update in MASTER.md.
2. **Snowball Protocol.** Load context by levels L0/L1/L2.
3. **Atomic checkout** (see proposal 06) — the `todo → in_progress` transition only through checkout.
4. **Run-id on all mutations** (see proposal 04).
5. **MCP-tool contracts** — change in sync with tests and registration in `tool_defs.py`.

## 6. DB schema change workflow
1. Edit `cod_doc/core/...` or tables in `_db.py`.
2. Create a migration via alembic.
3. Run `alembic upgrade head` + tests.

## 7. Verification before hand-off
ruff check cod_doc/ tests/
ruff format --check cod_doc/ tests/
mypy cod_doc/
pytest tests/ -v --tb=short --timeout=120

If something was not run — explicitly note "not run, because <reason>".

## 8. MCP-tool conventions
- Pure functions, no global mutations.
- Return — typed dataclass or TypedDict.
- Document in the docstring (this goes into `tools/list`).
- Registration in `cod_doc/agent/tool_defs.py` in sync with the implementation.

## 9. UI expectations
- Markdown tables — always with an explicit header.
- Links between documents — via hybrid refs.

## 10. PR requirements
Fill in the `.github/PULL_REQUEST_TEMPLATE.md` template fully:
- **What changed** — bullet list
- **Why** — motivation
- **How to verify** — steps
- **Risks** — what can go wrong
- **Model used** — the model used during development (or "human-authored")
- **Checklist** — all items

## 11. Definition of Done
- [ ] Behavior matches the task / specification.
- [ ] Tests, lint, mypy, formatting green.
- [ ] Contracts synchronized (models ↔ MCP ↔ UI ↔ docs).
- [ ] Documents (MASTER, arch, specs, models) updated on behavior change.
- [ ] PR filled per the template.
- [ ] If the change is visible in UI — a screenshot / description is attached.
```

### 2. Create `.github/PULL_REQUEST_TEMPLATE.md`

With a mandatory `Model used` field (like paperclip — a useful signal for AI-contribution audit).

### 3. Connection with skills

`AGENTS.md` references `cod_doc/skills/` (proposal [01](01-skills-layer.md)) as **canonical instructions for the agent**. This eliminates split-brain: the agent reads the same as a human.

## Implementation plan

1. **Draft `AGENTS.md`** — based on the template above + current implicit conventions (probe via greps over `# TODO`, `# FIXME`, existing docstrings).
2. **PR-template.**
3. **Move rules from `MEMORY.md`** — what is shared (FM-validation, audit-cadence) — into skills (see [01](01-skills-layer.md)) and here. Personal (preference on language, tone) — stays in memory.
4. **Link from `MASTER.md`** — add an item "Read AGENTS.md before contributing".
5. **Hooks (optional):** a pre-commit hook checking that the PR description contains required sections.

## Risks

- **Drift.** The file can go stale. Solution: one of the routines (see [07](07-routines.md)) — `agents_md_freshness` — flags if AGENTS.md has not been touched for > 90 days under active development.
- **Duplication with MASTER.md.** A clear separation: MASTER = "what is", AGENTS = "how to work".

## Success metrics

- A new contributor (or a new AI session) can run the tests and make a meaningful PR after reading only AGENTS.md.
- 100% of PRs contain a filled "Model used" field.
- No shared rules that live only in personal memory.

## Related

- 01 (skills) — `AGENTS.md` references skills as the canonical source for the agent.
- 04 (run-id) — the "Model used" field complements the run-id audit (what AI wrote vs human).

## Notes (cod-doc context)

- **Do after [01](01-skills-layer.md).** AGENTS.md references `cod_doc/skills/` as the canonical source for the agent. If there are no skills yet — there is nothing to reference, and AGENTS.md becomes both a meta-document and a rule store, which is bad.
- **Move from MEMORY.md.** Now shared knowledge (FM-validation, audit-cadence) lives in the user's personal memory — it is invisible to a new contributor and to a new session without memory. AGENTS.md (via references to skills) makes it part of the repo.
- **"Model used" as a signal for audit.** The field in the PR-template is a cheap improvement, the real value opens up in combination with [04](04-run-id-audit.md): you can compare "what the author declared" with "what some run actually wrote".
- **Duplication with MASTER.md § 4 Quick Actions.** Decide up front: keep the commands in MASTER (then AGENTS just references "see MASTER § 4") or move them to AGENTS (then clean up MASTER). Do not leave in both.
- **Routine `agents_md_freshness`.** A simple defense against drift — an alert if AGENTS.md has not been touched for > N days under active development. A good candidate for the first routine from [07](07-routines.md).

## Open questions

- **Q1.** Quick Actions — keep in MASTER.md § 4 or move to AGENTS.md? Where is the source of truth?
- **Q2.** "Model used" — mandatory or optional field? What to write for PRs from purely human editing?
- **Q3.** Pre-commit hook on required PR sections — mandatory, recommended, or no enforcement at all?
- **Q4.** Who audits the freshness of AGENTS.md — the routine `agents_md_freshness` or a manual quarterly review?
- **Q5.** Connection with the built-in `AGENTS.md` (some tools — ChatGPT, Cursor, VS Code Copilot — have a similar file-standard) — do we write a universal one or a cod-doc-specific one?
- **Q6.** Localization — AGENTS.md in Russian (like the rest of the project docs) or in English (the open-code standard)?
