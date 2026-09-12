---
type: capability
scope: audit-and-ci
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-28
related_docs:
  - ../audit/2026-04-19-initial-audit.md
  - ../audit/2026-04-28-section-c-capabilities.md
---

# Capability — Audit & CI

> A consolidated catalog of `cod-doc audit` checks and a git/CI integration scheme.

## 1. Run levels

| Level | Command | Use |
|---------|---------|---------------|
| **soft** | `cod-doc audit` | Returns warnings + errors, exit 0 |
| **strict** | `cod-doc audit --strict` | Any error → exit 1 (CI) |
| **staged** | `cod-doc audit --strict --staged` | Only files changed in the git stage (pre-commit) |
| **deep** | `cod-doc audit --deep` | + verify external URLs, code-drift, embeddings freshness |

## 2. Check catalog

### 2.1 Frontmatter (see [standards/frontmatter.md](../standards/frontmatter.md))

Codes and severity are synchronized with [`cod_doc/services/validation.py`](../../../cod_doc/services/validation.py) (COD-020 and follow-ups).

| ID | Severity | Description | Where implemented |
|----|----------|----------|------------------|
| FM-001 | error | Unknown `type` value (outside enum) | domain enum upstream |
| FM-002 | error | `status=active` with empty `owner` | `audit_frontmatter` |
| FM-003 | error | `source_of_truth=false` without `canonical_source` | `audit_frontmatter` |
| FM-004 | warning | `last_updated` in the future | `audit_frontmatter` |
| FM-005 | warning | `last_updated` older than 180 days with `status=active` | `audit_frontmatter` |
| FM-006 | error | Incompatible `type`/`status` pair (see [frontmatter.md §2a](../standards/frontmatter.md)) | reserved (COD-031) |
| FM-007 | warning | Missing `sensitivity` for `module-spec`/`architecture`/`standard` | reserved (COD-025) |
| TY-001 | error | Documents still on the import default (`module-spec`+`draft` without an authorial `type:`) | `audit_import_fallback` |

### 2.2 Task plan (see [standards/task-plan.md](../standards/task-plan.md))

| ID | Severity | Description |
|----|----------|----------|
| TP-001 | error | task ID is not unique |
| TP-002 | error | title does not match the verb-pattern |
| TP-003 | error | type / priority / status outside enum |
| TP-004 | error | Cycle in dependencies |
| TP-005 | error | `done` task has a `pending` dependency |
| TP-006 | error | section letter does not match frontmatter |
| TP-007 | warning | feature/bug/refactor without `affected_files` |
| TP-008 | warning | section file > 400 lines |
| TP-009 | warning | inline-plan > 600 lines |
| TP-010 | warning | plan ≥ 20 tasks without `completed_log` |
| TP-011 | warning | Progress Overview diverges from the DB |

### 2.3 Links (see [standards/document-link.md](../standards/document-link.md))

| ID | Severity | Description |
|----|----------|----------|
| LK-001 | error | New link does not resolve (write-path) |
| LK-002 | warning | Existing link became broken |
| LK-003 | warning | Fuzzy-matched link (Lev ≤ 2) |
| LK-004 | warning | Plaintext mention of an ID without a link |
| LK-005 | info  | External URL does not return 200 (only `--deep`) |

### 2.4 Sensitivity (see [standards/sensitive-data.md](../standards/sensitive-data.md))

| ID | Severity | Description |
|----|----------|----------|
| SD-001 | error | Secret pattern found (regex + entropy) |
| SD-002 | error | `public` doc references a `confidential` one |
| SD-003 | warning | Document without `sensitivity` |

### 2.5 Drift / freshness

| ID | Severity | Description |
|----|----------|----------|
| DR-001 | warning | `implemented_in` points to a non-existent path |
| DR-002 | warning | plan `last_updated` < `max(task.last_updated)` |
| DR-003 | warning | `projection_hash` does not match disk |
| DR-004 | warning | `revision` for the document is missing for > 180 days with `status=active` |

## 3. Git integration

```bash
cod-doc hooks install
```

Installs:

- `pre-commit`: `cod-doc audit --strict --staged`
- `post-commit`: `cod-doc task sync_from_diff && cod-doc projection freeze`
- `prepare-commit-msg`: adds `[<TASK-ID>]` if staged files match a single ready-task

Removal: `cod-doc hooks uninstall`.

## 4. CI integration

### 4.1 Current workflow (internal — for the repo itself)

Active workflow: [`.github/workflows/ci.yml`](../../../.github/workflows/ci.yml) (COD-024).

Three jobs:

| Job | Trigger | Status |
|-----|---------|--------|
| **pytest** | `pull_request`, `push: main`, matrix `python-version: ['3.11', '3.12']` | ✅ blocking |
| **ruff** | same | ⚠️ advisory (`continue-on-error`) — lifted after COD-024a |
| **mypy** | same | ⚠️ advisory (`continue-on-error`) — lifted after COD-024a |

The concurrency group cancels superseded runs, the pip cache — via `cache-dependency-path: pyproject.toml`.

### 4.2 External workflow for user projects (target example)

For projects using `cod-doc` as a utility (after COD-031):

```yaml
name: COD-DOC audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install cod-doc
      - run: cod-doc audit --strict --json > audit.json
      - if: failure()
        run: cat audit.json
```

### 4.3 GitLab CI (example)

```yaml
audit:
  image: python:3.11
  script:
    - pip install cod-doc
    - cod-doc audit --strict --json | tee audit.json
  artifacts:
    when: always
    paths: [audit.json]
```

### 4.4 Extended patterns (DOC-ME-3)

- **Shared-token / pinned version.** Pin the version: `pip install cod-doc==X.Y.Z`
  (or git-pin) so CI does not "drift" on a minor release. Tokens/secrets for
  private indices — via CI-secrets, not in YAML.
- **Cache `state.db` between jobs.** The DB projection is expensive to rebuild;
  cache `.cod-doc/state.db` keyed by the hash of `docs/**` so audit/drift-jobs
  reuse the index:

  ```yaml
  - uses: actions/cache@v4
    with:
      path: .cod-doc/state.db
      key: coddoc-db-${{ hashFiles('docs/**', 'cod_doc/infra/migrations/**') }}
  ```

  A cache miss → a single `cod-doc doc import` rebuilds the DB; a hit → the job
  starts with a ready index.
- **Fail-on-warning toggle.** By default warnings do not fail the build
  (exit 0). For strict branches: `cod-doc audit --strict` (error → exit 1).
  Route-drift and similar advisory checks (`--web-routes`) remain
  non-blocking even under `--strict` — they signal, but do not block merge.

## 5. JSON report format

```json
{
  "project": "restate",
  "started_at": "2026-04-19T12:00:00Z",
  "checks_run": 28,
  "errors": [
    {"check":"TP-004","severity":"error","entity":"AUTH-025",
     "message":"Cycle: AUTH-025 → AUTH-020 → AUTH-025"}
  ],
  "warnings": [...],
  "info": [...],
  "exit_code": 1
}
```

Stable for machine processing (CI badges, Slack notifications).

## 6. Suppressing false positives

Local suppression — via frontmatter:

```yaml
audit_suppress:
  - check: FM-004
    reason: "Spec frozen pending compliance review; do not auto-stale"
    until: 2026-06-01
```

Suppression with an expired `until` raises the warning again.
