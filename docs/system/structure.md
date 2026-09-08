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

## Partitions

A snapshot answers only for its own `provenance.scope`. Findings carry that
scope and are reconciled **within it**, so a partial snapshot never closes a
finding it did not look at.

`truncated` means the producer could not fit even one partition under
`MAX_ENTITIES` / `MAX_EDGES`. Such a snapshot may not close findings: absence
from the incoming drift is indistinguishable from "never reached". The answer
is more partitions, not larger limits — the limits are payload guards, and
raising them only moves the cliff.

Partition the **root** graph, never rescan a subdirectory: graphify records
`source_file` relative to its scan root, so a subtree scan yields paths the
producer rejects as escaping `--repo-root` (observed: every node skipped,
`entities=0`). A node belongs to the first matching prefix; an edge belongs to
its source's partition, so the split is lossless.

```bash
python scripts/split_graph.py .graphify/graphify-out/graph.json parts/ \
    --prefix cod_doc/services --prefix cod_doc/cli --prefix tests

node pr-review-structure.mjs --repo-root . \
    --graph parts/cod_doc-services/graph.json --scope cod_doc/services \
    --facts-out out/services/structure-facts.json

cod-doc ingest structure -p <project> \
    --facts out/services/structure-facts.json --assessment out/services/structure-assessment.json \
    --facts out/cli/structure-facts.json      --assessment out/cli/structure-assessment.json
```

Repeated `--facts` ingests one generation in a single transaction; pass either
no `--assessment` or exactly one per `--facts`. Every partition of a generation
shares one `headSha`, and retention is counted per `(branch, scope)` so
partitions do not evict each other.

Current pointers (`latest_main` / `latest_pr` / `latest_branch`) are still keyed
by branch and PR only, so within one generation the last partition ingested wins
the pointer. That affects retrieval — `structure latest`, `ctx structure` — not
reconciliation, which is scoped. Per-partition pointers and a `structure_context`
that unions partitions are the follow-up.

On cod-doc itself the whole repository is 10 750 nodes / 26 846 edges and
truncates at 5 000 / 15 000; split across 13 prefixes every partition fits, and
findings close normally.

## CLI

```text
cod-doc obligation export -p <project> --json
cod-doc ingest structure -p <project> --facts facts.json [--assessment assessment.json]
cod-doc ingest structure -p <project> --facts a.json --facts b.json   # поколение партиций
cod-doc structure latest|get|diff|drift|entities|contracts|scenarios|triage
cod-doc structure link-suggest|link-confirm
cod-doc structure waive|waivers|replay
cod-doc ctx structure -p <project> --head-sha <sha> --scope <entity>
```

## MCP

`structure_context` requires `head_sha` or `snapshot_fingerprint`. There is
no implicit latest for plan/fix workflows.
