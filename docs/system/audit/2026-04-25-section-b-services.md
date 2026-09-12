---
type: audit-report
scope: cod_doc/services (Section B — RevisionService, DocService, TaskService)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-25
last_updated: 2026-04-25
audit_target_revision: COD-015 + COD-010 + COD-011 (commit d605731)
related_docs:
  - ../DATA_MODEL.md
  - ../roadmap/cod-doc-task-plan.md
  - 2026-04-25-section-a-data-core.md
---

# Section B (Services) — Implementation Audit

> Audit of the three implemented services: RevisionService (COD-015), DocService (COD-010), TaskService (COD-011) + TaskRepository.
> Severity: **high** — functional bug or dead code; **medium** — API inconsistency, future breakage; **low** — typing, test coverage, style.

## Summary

| Severity | Count | Fixed | Deferred |
|----------|------:|------:|---------:|
| high     | 2 | 2 ✅ | 0 |
| medium   | 3 | 2 ✅ | 1 (ME-3) |
| low      | 8 | 7 ✅ | 1 (LO-3) |
| **total** | **13** | **11 ✅** | **2** |

129/129 tests pass. All functional bugs and dead code are eliminated. Deferred: ME-3 (diff_format discriminator — design decision before COD-022), LO-3 (sort order — resolved in COD-020).

---

## 1. High

### SB-HI-1. ✅ Dead import `datetime` in `task_repo.py`

[cod_doc/infra/repositories/task_repo.py:5](../../../cod_doc/infra/repositories/task_repo.py) — `from datetime import datetime` is never used in this module. Mypy strict / ruff flag it as `F401`.

**Fix:** remove the line.

### SB-HI-2. ✅ `_TASK_ID_RE` — dead regex in `task_service.py`

[cod_doc/services/task_service.py:40](../../../cod_doc/services/task_service.py) — `_TASK_ID_RE = re.compile(...)` is declared but never called. It was intended to validate the `task_id` format, but validation was delegated to COD-020 and the regex was forgotten. Ruff: `F841`.

**Fix:** delete the constant; if the format is needed — add it to COD-020.

---

## 2. Medium

### SB-ME-1. ✅ `_NO_PARENT_CHECK` — private sentinel used in three modules

`revision_service._NO_PARENT_CHECK` is declared with a leading underscore (private convention), but is imported directly in [doc_service.py:207](../../../cod_doc/services/doc_service.py) and [task_service.py:202](../../../cod_doc/services/task_service.py):

```python
expected_parent_revision_id: str | None | object = rev._NO_PARENT_CHECK
```

This breaks the convention and breaks if `revision_service` renames or removes the sentinel. Mypy strict does not catch cross-module access to `_`-names.

**Fix:** rename to `NO_PARENT_CHECK` (drop the underscore) and document it as part of the service's public API.

### SB-ME-2. ✅ `update_status` does not support `expected_parent_revision_id`

`DocService.patch_section` passes `expected_parent_revision_id` to RevisionService — concurrent writes are protected. `TaskService.update_status` does not. An agent that reads a task and changes its status cannot declare the expected revision head; concurrent updates are not detected.

`TaskService.complete` has the parameter — i.e. this is an intentional decision for complete, but not for update_status. This is a non-obvious asymmetry: two write-paths for the same `status` field have different guarantees.

**Fix:** add `expected_parent_revision_id` to `update_status` with the same default `_NO_PARENT_CHECK`. Test: concurrent `update_status` → `RevisionConflictError`.

### SB-ME-3. ⏸ Inconsistent diff format: unified-diff vs JSON-patch without a discriminator

| Method | Diff format |
|-------|-------------|
| `doc_service.create` | unified-diff (possibly empty) |
| `doc_service.add_section` | unified-diff `--- /dev/null → +++ section:...` |
| `doc_service.patch_section` | unified-diff |
| `doc_service.rename` | JSON `{"op":"rename",...}` |
| `task_service.create` | JSON `{"op":"create",...}` |
| `task_service.update_status` | JSON `{"op":"status",...}` |
| `task_service.complete` | JSON `{"op":"complete",...}` |

`RevisionService.list_for_entity` returns `Revision.diff: str` — the reader does not know whether the string is a unified-diff or JSON. Future `cod-doc log` / RevisionService.revert / UI will have to guess the format by `entity_kind` or by attempting `json.loads`.

**Fix (two options):**
1. **Lightweight:** add a `diff_format: Literal["unified", "json-patch"]` field to the `revision` table (migration 0007) and populate it on write.
2. **Lighter:** fix a convention in `revision_service.write` — always JSON-patch, with a special `"lines"` key for text changes — and update DocService.

The choice is to be fixed in DATA_MODEL §3.5. The current state is technical debt for COD-022 (revert) and COD-032 (MCP display).

---

## 3. Low

### SB-LO-1. `kwargs: dict` in `TaskRepository._to_model` — bare dict

[cod_doc/infra/repositories/task_repo.py:37](../../../cod_doc/infra/repositories/task_repo.py) — `kwargs: dict = {...}`. Mypy strict: "Missing type parameters for generic type 'dict'". → `dict[str, Any]`.

### SB-LO-2. Deferred `from sqlalchemy import text` inside a function

[cod_doc/services/doc_service.py:149](../../../cod_doc/services/doc_service.py) — `from sqlalchemy import text` inside the body of `render_body()`. There is no circular-import justification — `sqlalchemy` is a third-party. It should be a module-level import.

### SB-LO-3. `list_for_plan` sorts by the string `task_id` — lexicographically

[cod_doc/infra/repositories/task_repo.py:66](../../../cod_doc/infra/repositories/task_repo.py) — `ORDER BY section_id, task_id`. The string `task_id` sorts lexicographically: `P-10` < `P-2` < `P-20`. Correct **only** with zero-padding (001, 002, ..., 010). Manual IDs without padding break the order.

**Fix:** add a regexp-based numeric sort when serving via PlanService, or fix the zero-padding invariant in COD-020 validation.

### SB-LO-4. `_add_project` / `_run_alembic_upgrade` is duplicated in 4 test files

`tests/services/test_revision_service.py`, `test_doc_service.py`, `test_task_service.py` and `tests/infra/` — all contain the same `_add_project`. After Section B it makes sense to move it to `tests/conftest.py` or `tests/fixtures.py`.

### SB-LO-5. `_new_doc` in `test_doc_service.py` — no return type annotation

Historical `tests/services/test_doc_service.py:57` — a helper function without `-> Document`. Mypy strict: `no-untyped-def`; later the file was split into focused service tests.

### SB-LO-6. Double-complete is not tested

`TaskService.complete` on a task that already has `status=done` will not raise an exception (all blockers are already done; the status is overwritten idempotently). `completed_at` will be overwritten with a new time, which is probably undesirable. The behavior is not documented and not covered by a test.

**Fix:** either `complete` on `status=done` → no-op (return the current state without a revision), or → `TaskAlreadyDoneError`. Fix in a test.

### SB-LO-7. `add_section` with a duplicate anchor is not tested

A duplicate anchor in the same document will raise `IntegrityError` (DB UNIQUE `uq_section_document_anchor`). The service layer does not intercept and convert it to `SectionAlreadyExistsError` — a raw SQLAlchemy exception will fly out. Test + add interception.

### SB-LO-8. `affected_files` in `task_service.create` — only `kind='source'`

[cod_doc/services/task_service.py:136-143](../../../cod_doc/services/task_service.py) — all files are marked as `kind='source'`. `AffectedFileKind` supports `test|migration|config`, but you cannot programmatically create a task with them. It is enough to accept `list[str | tuple[str, AffectedFileKind]]`.

---

## 4. Test coverage summary

| Service | Tests | Gaps |
|---------|------:|------|
| RevisionService | 9 | — main paths are covered |
| DocService | 15 | add_section duplicate-anchor; empty-preamble render |
| TaskService | 14 | double-complete; `update_status` concurrency; task_id UNIQUE violation |
| TaskRepository | — | no unit tests directly (indirectly through TaskService) |

---

## 5. Correctness confirmation

- ✅ RevisionService: chain `parent_revision_id`, sentinel `_NO_PARENT_CHECK`, ULID timestamp sync — all correct.
- ✅ DocService: `_create_diff("")` → `""` — empty unified-diff (valid NOT NULL); `render_body` reads through the view; `patch_section` flush-before-revision — correct order; rename diff from pre-mutation values — ✓.
- ✅ TaskService: dep-check only `kind='blocks'`; `session.get(TaskModel, dep.to_task_id)` — by PK, which is correct; CASCADE on dep when deleting a task does not allow `dep_task is None` — the defensive check is harmless.
- ✅ TaskRepository: `_to_model` does not add `completed_at` if None — ORM default None applies. ✓
- ⚠️ `doc_service.create` writes a revision with `diff=""` if preamble="" — technically valid, but semantically empty. Not a bug, but the revision loses informativeness.

## 6. Prioritized fix order

The order is recommended to be done before or together with COD-012 (PlanService):

| Priority | ID | Status | Note |
|-----------|------|--------|------------|
| 1 | SB-HI-1 | ✅ fixed | `from datetime import datetime` removed |
| 2 | SB-HI-2 | ✅ fixed | `_TASK_ID_RE` removed |
| 3 | SB-ME-1 | ✅ fixed | `NO_PARENT_CHECK` (public), `_NO_PARENT_CHECK` — alias |
| 4 | SB-LO-1 | ✅ fixed | `dict[str, Any]` in `_to_model` |
| 5 | SB-LO-2 | ✅ fixed | `from sqlalchemy import text` — module-level |
| 6 | SB-LO-5 | ✅ fixed | `_new_doc` return type annotation |
| 7 | SB-LO-6 | ✅ fixed | `TaskAlreadyDoneError` guard + test |
| 8 | SB-ME-2 | ✅ fixed | `expected_parent_revision_id` in `update_status` + test |
| 9 | SB-LO-7 | ✅ fixed | savepoint + `SectionAlreadyExistsError` + test |
| 10 | SB-ME-3 | ⏸ deferred | Design decision for COD-022 |
| 11 | SB-LO-3 | ⏸ deferred | Resolve in COD-020 (zero-padding invariant) |
| 12 | SB-LO-4 | low | conftest — after Section B |
| 13 | SB-LO-8 | low | affected_files kind — as needed |
