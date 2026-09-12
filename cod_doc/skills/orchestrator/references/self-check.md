# Reference — self_check block

> Loaded additionally on completing any task.

## 1. Mandatory format

At the end of a task the agent must output a JSON block:

```json
{
  "self_check": {
    "links_verified": true,
    "hashes_match": true,
    "no_hallucinations": true,
    "context_depth": "L1",
    "missing_info": []
  }
}
```

## 2. Fields

| Field | Type | Semantics |
|------|-----|-----------|
| `links_verified` | bool | All references mentioned in the result passed through `link_verify` or `check_stale_refs`. |
| `hashes_match` | bool | Hashes of hybrid references agree (via `verify_hash`). `false` is allowed with an honest STALE flag. |
| `no_hallucinations` | bool | The content relies only on read docs and tool results. `false` — explicitly mark, do not commit. |
| `context_depth` | str | `L0` or `L1` — the actually loaded level (see the `context_get` contract). Do not inflate. L2/L3 are reserved and currently unsupported. |
| `missing_info` | list[str] | A list of things that were missing (for a follow-up task), or `[]`. |

## 3. When `false`

- `links_verified=false` — allowed if the task did not touch references.
- `hashes_match=false` — must be accompanied by an explanation in
  `missing_info`.
- `no_hallucinations=false` — **not allowed** for commit-ready tasks.
  If there is no confidence — move the task to `in_review` with an
  escalation.
- `context_depth="L2"` / `"L3"` — currently invalid: `context_get`
  declares only L0 and L1. If you really need an extended dependency
  analysis — assemble it with a sequence of L1 calls and note in
  `missing_info` that L2 is missing.

## 4. Related

- FM-002/FM-003 escalations — see skill [validation](../../validation/SKILL.md)
  (when such a skill is created, see PCA-002).
- Audit-cadence (closing a phase → audit-report; opening → kickoff brief) —
  see skill [audit-cadence](../../audit-cadence/SKILL.md) (PCA-002).
