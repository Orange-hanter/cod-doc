---
type: standard
scope: frontmatter
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-28
---

# Frontmatter Standard

> Describes the mandatory and recommended YAML frontmatter fields for all documents of a project managed by COD-DOC.
> Inherited from Restate (`Docs/standards/frontmatter.md`) with extensions for DB validation.

## 1. General rule

Every document has frontmatter. When a document is created via `cod-doc doc new`, the frontmatter is generated automatically from the `Document` entity's fields. On manual markdown editing and subsequent import — COD-DOC parses the frontmatter and applies it as a patch to the `Document` record.

## 2. Mandatory fields

| Field | Values | DB mapping |
|------|----------|--------------|
| `type` | `module-spec`, `module-subdoc`, `execution-plan`, `task-section`, `execution-log`, `standard`, `architecture`, `vision`, `guide`, `user-story`, `decision`, `open-question`, `redirect`, `design`, `audit`, `audit-report`, `journal`, `plan`, `analysis`, `research`, `capability` | `document.type` |
| `status` | See table §2a (depends on `type`); alien spellings — §2b | `document.status` |
| `owner` | A string (team or role) | `document.owner` |
| `last_updated` | `YYYY-MM-DD` | `document.last_updated` |
| `source_of_truth` | `true` / `false` *(or a nested dict for `execution-plan` — see §7)* | `document.source_of_truth` |

The source of truth for the `type` list — the enum `DocumentType` (`cod_doc/domain/entities.py`);
the table above must match it value for value. Eight types
(`design`, `audit`, `audit-report`, `journal`, `plan`, `analysis`, `research`,
`capability`) were added in ADO-015: they already lived in the corpora, but import silently
turned them into `module-spec`.

## 2a. Allowed `status` per `type`

Each `type` defines its own subset of `status`. An incompatible pair (e.g. `type: execution-plan` + `status: active`) → error.

| `type` | Allowed `status` | Terminal |
|--------|---------------------|----------|
| `module-spec`, `module-subdoc`, `standard`, `architecture`, `vision`, `guide`, `redirect` | `draft` → `review` → `active` → `deprecated` | `deprecated` |
| `execution-plan`, `task-section`, `execution-log` | `pending` → `in-progress` → `done` (opt. `blocked`, `cancelled`) | `done` / `cancelled` |
| `user-story` | `draft` → `accepted` → `delivered` → `archived` | `archived` |
| `audit-report`, `audit` | `active` (a live audit **and** a closed one — in frontmatter it is written `resolved`, see §2b) → `deprecated` (superseded; in frontmatter `superseded`) | `deprecated` |
| `design`, `analysis`, `research`, `capability`, `decision`, `open-question` | `draft` → `review` → `active` → `deprecated` | `deprecated` |
| `plan` | `pending` → `in-progress` → `done` (opt. `blocked`, `cancelled`) | `done` / `cancelled` |
| `journal` | `active` — a journal is not "completed", it is either kept or `deprecated` | `deprecated` |

## 2b. Alien statuses on import

Exactly four `status` values live in the DB: `draft`, `review`, `active`,
`deprecated`. Alien corpora write differently (`living`, `final`, `done`, `accepted`,
`archived`, `superseded`, …). Import does not throw them away or pretend to
understand: the table `_ALIEN_STATUS_ALIASES` (`cod_doc/services/import_service.py`)
translates known spellings, and **every such replacement goes into
`ImportReport.warnings`** with `reason: alias`. A spelling not in the table
yields `draft` and `reason: unknown`.

| Alien spelling | Canonical `status` |
|---|---|
| `living`, `final`, `done`, `complete`, `completed`, `resolved`, `accepted`, `delivered`, `published`, `current`, `stable`, `in-progress` | `active` |
| `proposed`, `pending`, `wip`, `todo` | `draft` |
| `in-review`, `reviewing` | `review` |
| `archived`, `resolved`, `superseded`, `obsolete`, `rejected`, `cancelled` | `deprecated` |

A side effect worth knowing: `final`, `done` and `resolved` become `active`,
so FM-005 (`stale-doc`) starts counting the age of historical documents.
This is a conscious choice — a "completed" document is not the same as one
decommissioned from operation.

## 3. Conditionally mandatory

- `last_reviewed` — for `type` ∈ {`module-spec`, `architecture`, `standard`}.
- `created` — for any, but COD-DOC will fill it in automatically on creation.
- If `source_of_truth: false`:
  - `canonical_source` — one of the known `doc_key`.
  - `scope` — the reason for existence (`legacy-redirect`, `derived-analysis-redirect`, `domain-appendix-redirect`).
  - `audience` — a non-empty array.
  - `related_code` — an array (may be empty `[]`).

## 4. Recommended fields

| Field | Purpose |
|------|------------|
| `tags` | Array of strings; maps to `tag` + `document_tag` |
| `project` | Project slug |
| `audience` | `[contributors, agents, product, ...]` |
| `related_code` | Array of paths |
| `implemented_in` | Dict (`backend: [...]`, `tests: [...]`) — for module-spec |
| `depends_on` | Array of links to modules — for module-spec |
| `api_navigation` | Dict — for module-spec |
| `schema` | Dict — for module-spec |
| `task_plan` | Path to the module's plan |

## 5. Fields reserved by COD-DOC

These fields COD-DOC sets automatically and may overwrite on export:

| Field | Purpose |
|------|-----------|
| `doc_key` | Identifier in the DB (`modules/M1-auth/overview`) |
| `projection_hash` | Hash of the last export |
| `cod_doc_generated` | `true` if the document was generated entirely from a template |
| `revision` | ID of the last revision |

The author must not edit them manually; on a conflict, the DB wins.

## 6. Validation

The `DocService.validate_frontmatter(doc)` service runs:

1. On every write-path action.
2. On the `cod-doc audit` command.
3. On the git pre-commit hook (installed via `cod-doc hooks install`).

Rules:

- `FM-001` Unknown `type` value → error. On the **import** path, this is not an error, but
  a warning: a value outside the enum falls back to a fallback in the DB and goes into
  `ImportReport.warnings` (`reason: unknown`), and **the file is not rewritten**
  — that is the responsibility of `_raw_matches_db` (ADO-010). Importing an alien corpus
  has no right to either fail the run or edit the alien markdown.
- `FM-002` `status=active` with an empty `owner` → error.
- `FM-003` `source_of_truth: false` without `canonical_source` → error (for `execution-plan` see §7 — the dict variant is excluded).
- `FM-004` `last_updated` in the future → warning.
- `FM-005` `last_updated` older than 180 days for `status=active` → warning (`stale-doc`).
- `FM-006` Incompatible `type`/`status` pair (see §2a) → error.
- `FM-007` *(reserved, see [sensitive-data.md](sensitive-data.md))* Missing `sensitivity` for documents with `type` ∈ `{module-spec, architecture, standard}` → warning. Implemented in task COD-025.

## 7. Relationship with the task-plan ecosystem

Task-plan uses a narrow subset and overrides some values:

- `status` in execution-plan: `pending` / `in-progress` / `done` (not `draft`/`active`).
- `source_of_truth` in execution-plan: a nested dict indicating the sources of each aspect (vision, architecture, data_model, …) instead of a boolean. Example:
  ```yaml
  source_of_truth:
    vision: docs/system/VISION.md
    architecture: docs/system/ARCHITECTURE.md
  ```
  On write-path validation, FM-003 (the `canonical_source` requirement when `false`) does not apply — the presence of the dict is equivalent to "the plan has sources".
- `owner` is not required (the owner is Task Steward by convention).

Details: [task-plan.md](task-plan.md) and Restate `tools/task-plan-ecosystem.md §3`.

## 8. Examples

### 8.1 Canonical module spec

```yaml
---
type: module-spec
module_id: M1-auth
module_name: Authentication
status: active
owner: backend-team
source_of_truth: true
version: "2.0"
created: 2026-02-14
last_updated: 2026-04-19
last_reviewed: 2026-04-19
implemented_in:
  backend: [restate-api/src/auth/]
  tests: [restate-api/src/auth/__tests__/]
depends_on: []
api_navigation:
  paths: [/api/v1/auth]
  schemas: [LoginRequest, LoginResponse]
task_plan: modules/M1-auth/M1-auth-task-plan
tags: [module, spec, auth]
---
```

### 8.2 Legacy redirect

```yaml
---
type: redirect
status: deprecated
source_of_truth: false
canonical_source: modules/M1-auth/overview
scope: legacy-redirect
owner: backend-team
last_updated: 2026-04-19
audience: [contributors, agents]
related_code: []
---
```

### 8.3 User story

```yaml
---
type: user-story
status: accepted
owner: product
source_of_truth: true
story_id: US-014
persona: Agency Owner
priority: high
tags: [agency, onboarding]
---
```
