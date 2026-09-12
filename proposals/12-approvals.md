# 12 — First-class Approvals

> Category: 🔵 Architecture · Risk: medium · Dependencies: 04, 05, 08

## Context: like paperclip

```
POST /api/companies/:id/approvals
{
  "type": "request_board_approval",
  "requestedByAgentId": "...",
  "issueIds": ["..."],
  "payload": {
    "title": "Approve monthly hosting spend",
    "summary": "Estimated cost is $42/month for provider X.",
    "recommendedAction": "Approve provider X and continue setup.",
    "risks": ["Costs may increase with usage."]
  }
}
```

Approval — a **first-class entity**:
- References specific issues, puts them in `in_review`.
- On resolve (approve/deny) — wakes the requesting agent with `PAPERCLIP_APPROVAL_ID` + `_STATUS`.
- Visible in UI as a separate entity, not a comment.
- Has full audit: who requested, who decided, when, payload.

The skill explicitly teaches:
> *"If the plan needs explicit approval before implementation... create a `request_confirmation` issue-thread interaction... update the source issue to `in_review`... Wait for acceptance before creating implementation subtasks."*

## Current state of cod-doc

From project memory:
- **FM-002, FM-003** (structural validation) — escalate, but via ad-hoc messages / manual stop.
- **FM-004, FM-005** (advisory) — added as issues / comments.

Missing:
- a structured "pending decision" entity,
- auto-pause of a task until a decision,
- auto-wake after a decision,
- audit of decisions.

## Proposal

### 1. `Approval` entity

```python
@dataclass
class Approval:
    id: str                             # uuid7
    type: ApprovalType                  # 'plan_review' | 'risky_action' | 'fm_escalation' | 'budget' | 'manual'
    requested_by: str                   # 'orchestrator-run-X' | 'human:<id>'
    requested_at: datetime
    status: ApprovalStatus              # 'pending' | 'approved' | 'denied' | 'cancelled' | 'expired'
    resolved_by: str | None             # human:<id>
    resolved_at: datetime | None
    payload: ApprovalPayload            # title, summary, recommendedAction, risks, links
    linked_task_ids: list[str]
    linked_doc_revision_ids: list[str]  # for approve plan@rev-abc
    expires_at: datetime | None
    decision_comment: str | None
    run_id: str | None                  # from 04
```

`ApprovalType`:
- `plan_review` — review of a task-doc plan (see [05](05-issue-documents.md))
- `risky_action` — the orchestrator asks for confirmation before an operation
- `fm_escalation` — structural validation requires a manual decision (FM-002/003)
- `budget` — cost threshold exceeded
- `manual` — the operator created it manually

### 2. Automation

- On creating an approval with `linked_task_ids` — all these tasks → `in_review` (see [08](08-status-taxonomy.md)).
- On resolve approval — the corresponding wake (see [03](03-wake-payload.md)) with `reason='approval_resolved'`, the payload contains the decision and the comment.
- On expiry — wake the requester, payload `reason='approval_expired'`.

### 3. MCP-tools

- `approval_request(type, payload, linked_task_ids?, linked_doc_revisions?, expires_in?)` → `approval_id`
- `approval_list(status?, type?, since?)` 
- `approval_get(approval_id)` 
- `approval_resolve(approval_id, decision: 'approve'|'deny', comment?)`
- `approval_cancel(approval_id, reason)`

### 4. UI

- **Inbox for the operator:** all `pending` approvals on the home page — a priority list.
- **Approval card:** payload, linked tasks, approve/deny buttons, a field for a comment.
- **On the task card:** a badge "pending approval: …" with a deep-link.

### 5. Binding to existing cod-doc validation

| Case                          | Current behavior                         | With the proposal                                  |
| ----------------------------- | ---------------------------------------- | -------------------------------------------------- |
| FM-002 (MASTER schema broken) | Raise, manual intervention              | `approval_request('fm_escalation', ...)`, task → `in_review` |
| FM-003 (structural conflict)  | Raise                                    | Same                                               |
| FM-004/005 (advisory)         | Issue in audit-report, does not block    | No approvals — leave as is                        |
| Drift in a critical doc       | A task is created in the queue            | Optional: for docs with `confidence_required=high` create an approval before autofix |
| Risky bulk operation          | None now — the agent just does it        | `approval_request('risky_action', ...)` before the action |

## Implementation plan

1. **Schema + migration.** Tables `approvals`, `approval_history`.
2. **Domain model.** In [cod_doc/core/](cod_doc/core/).
3. **MCP-tools.**
4. **Status-machine integration** ([08](08-status-taxonomy.md)) — `linked_task_ids` auto `in_review` ↔ approval status.
5. **Wake integration** ([03](03-wake-payload.md)) — resolve triggers a wake with the correct payload.
6. **Validation integration.** Refactor FM-002/003 — they call `approval_request` instead of raise. FM-004/005 — no changes.
7. **UI:** approvals inbox + card.

## Risks

- **Approval fatigue.** Too many approval-requests → they get ignored. Solution: types are strictly limited, FM-004/005 do NOT generate approvals, advisory comments stay comments.
- **Stuck approvals.** Solution: `expires_at` is mandatory (default 7 days?) + a routine `approval_stale` (see [07](07-routines.md)) wakes the operator.
- **Parallel conflicting approvals on one task.** Solution: a rule "no more than 1 pending approval per task". Creating a duplicate → first auto-cancel the previous with reason='superseded'.

## Success metrics

- 100% of FM-002/003 escalations are formalized as approvals (not as a manual stop).
- Average time to resolve an approval — an observable metric.
- 0 silently-skipped escalations (all land on a visible inbox).

## Related

- 04 (run-id) — each approval stores the run_id of the requesting operation.
- 05 (issue docs) — an approval can reference a specific `plan` revision (approve plan@rev-abc).
- 08 (status taxonomy) — `in_review` — the main status for tasks with a pending approval.
- 09 (activity log) — events `approval.requested`, `approval.resolved`, `approval.expired`.
- 03 (wake-payload) — resolve of an approval — a typical source of a wake.

## Notes (cod-doc context)

- **FM-004/005 do NOT generate approvals — critical.** The most common cause of "approval fatigue" — turning any advisory check into a mandatory approval. The current layout (002/003 → escalation, 004/005 → advisory) is correct, and preserving it in this proposal is necessary.
- **`expires_at` is mandatory.** A stuck approval is a task in `in_review` forever. For single-user 7 days (like paperclip) is too long; 48 hours with a routine `approval_stale` that wakes the operator before expiry is more realistic.
- **Inbox in UI — not Phase 2, but MVP.** If the approval entity exists but there is no visibility, the operator simply won't see it, and pendings accumulate. Minimum — a pending counter in the navbar and a separate page with the list.
- **Depends on 04+05+08.** Without [08](08-status-taxonomy.md) `in_review` is mixed with `blocked`; without [05](05-issue-documents.md) it is unclear what "approve plan@rev-abc" means; without [04](04-run-id-audit.md) it is unclear which run requested the approval. Do after all three.
- **"No more than 1 pending approval per task".** Without this rule parallel approvals on one task create ambiguity. Auto-cancel of the previous with `reason=superseded` is fine, but this must be explicitly shown in the activity log, otherwise it will look like a bug.

## Open questions

- **Q1.** Default `expires_at` — 48 hours, 7 days, or per-type (e.g. `risky_action` — 24h, `plan_review` — 7 days)?
- **Q2.** If the operator is offline for > expires_at — what to do: auto-deny (safe), auto-cancel with retry-wake (softer), or just `expired` with an explicit re-request from the agent?
- **Q3.** "Partial approve" — can you approve one task from `linked_task_ids`, leaving the rest pending? Or is an approval an atomic unit?
- **Q4.** UI inbox — a separate page or an embedded widget on the home? Is a pending counter in the navbar mandatory at once?
- **Q5.** Notifications — are they needed (email/desktop) on a pending approval, or is it enough that the operator visits the UI themselves?
- **Q6.** An approval payload can contain references to doc revisions — what to do if the revision is deleted/reverted before resolve? Auto-cancel the approval with `reason=base_revision_gone`?
- **Q7.** Is an approval without `linked_task_ids` possible (e.g. a project decision not bound to a task) — allowed or an error?
