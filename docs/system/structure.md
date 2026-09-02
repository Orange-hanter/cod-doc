# Structure snapshots, obligations and scenario drift

cod-doc is the **consumer** of the shared structure protocol. ai-reviewer
produces `structure_facts.v1` and `structure_assessment.v1`; this service
stores snapshots, exports documented obligations, computes **structure
drift** (not DB↔Markdown projection drift) and returns pinned context for
plans and fixes.

JSON is canonical. Generated Markdown projections are `source_of_truth: false`
and never overwrite specification documents.

## Storage

- `code_structure_snapshot` — immutable header + zlib payload + sha256
- `structure_assessment` — separate record keyed by facts fingerprint +
  obligations revision + coverage/thresholds hashes
- scoped `code_boundary` / `code_entity` / `code_contract` / `code_edge`
  materialized after ingest
- `doc_code_claim`, `structure_finding`, `structure_waiver`

`repo_index` remains a fallback lookup, not a second source of truth.
Snapshots are not `ai_review` findings.

## Trust

`signed_ci` and `trusted_local` may become `latest_main` / `latest_pr`.
`untrusted` artifacts are stored for inspection only: they do not publish
current pointers and do not create findings or tasks.

`latest` without `branch_ref`, `pr_number` or `head_sha` is rejected.

## CLI

```text
cod-doc obligation export -p <project> --json
cod-doc ingest structure -p <project> --facts facts.json [--assessment assessment.json]
cod-doc structure latest|get|diff|drift|entities|contracts|scenarios|triage
cod-doc structure link-suggest|link-confirm
cod-doc structure waive|waivers|replay
cod-doc ctx structure -p <project> --head-sha <sha> --scope <entity>
```

## MCP

`structure_context` requires `head_sha` or `snapshot_fingerprint`. There is
no implicit latest for plan/fix workflows.
