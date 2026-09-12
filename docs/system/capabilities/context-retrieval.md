---
type: capability
scope: context-retrieval
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../ARCHITECTURE.md
  - ../DATA_MODEL.md
---

# Capability — Concentrated Context Retrieval

> Getting a "minimally sufficient" context of the project on request. A replacement for the manual "read the entire Docs/obsidian/Modules/…".

## 1. Why

An agent starting a session in Restate spends a huge budget reading large files (`Architecture.md` 17K, `SYSTEM_OVERVIEW.md` 73K). This is a known pain (see `AGENT_START.md`, Snowball Protocol). COD-DOC solves it **structurally**:

- A document in the DB is a set of sections, each with an anchor, title, body and context (tags, links, story-ties).
- A context request returns JSON + quoted fragments, picked for a given token budget.
- No "read everything and throw out 90%".

Conceptual affinity: the Restate `MASTER.md` `context_depth: L0/L1/L2` is formalized in the service.

## 2. Depth levels

| Level | What is included | When |
|-------|--------------|-------|
| `L0` | MASTER + explicit target document (metadata only) | Session start, high-level overview |
| `L1` | L0 + body of the target + direct links (module spec, open task-plan, ≤ 3 open questions, ≤ 3 user stories) | Work inside one module |
| `L2` | L1 + `depends_on`-chains, cross-module dependencies, neighboring standards | Deep work on boundaries |
| `L3` | L2 + semantic-search top-k over the whole corpus | Only on explicit request; expensive |

> L3 fail-open: if the embedder is not configured or unavailable, `related.semantic`
> comes back empty, rather than failing the response. The check is `cod-doc embed status`
> (resolve without network) and `cod-doc embed probe` (a live call); the embedding
> provider is configured independently of the LLM, see HANDBOOK §10.4.

## 3. The `context.get` contract

```json
{
  "target": {
    "kind": "module" | "document" | "task" | "story" | "plan",
    "id":   "M1-auth" | "modules/M1-auth/overview" | "AUTH-025" | "US-014" | "M1-auth-module"
  },
  "depth": "L0" | "L1" | "L2" | "L3",
  "token_budget": 8000,
  "formats": ["json", "markdown"]
}
```

Response:

```json
{
  "target_summary": { "title": "...", "status": "...", "owner": "..." },
  "core": {
    "master_excerpt": "...",
    "target_body": "...",
    "sections": [{ "anchor": "data-model", "heading": "...", "excerpt": "..." }]
  },
  "related": {
    "documents": [{ "doc_key": "...", "title": "...", "why": "spec" }],
    "tasks":     [{ "task_id": "AUTH-025", "status": "pending", "why": "open" }],
    "stories":   [{ "story_id": "US-014", "why": "linked" }],
    "dependencies": [...]
  },
  "hints": {
    "next_best_reads": ["..."],
    "open_questions": ["..."]
  },
  "meta": {
    "depth": "L1",
    "tokens_used": 6412,
    "truncated": false,
    "generated_at": "2026-04-19T..."
  }
}
```

## 4. The assembly algorithm (L1)

1. Resolve the target.
2. Pull the target body + frontmatter.
3. If the target is a `module` or `document`:
   - include plan-progress: Progress Overview (from `plan_totals`), Next Batch (from `ready_tasks`).
   - include open questions for the module (document with type=`guide`, tag=`open-questions`).
   - include ≤ 3 user stories, linked via `story_link`.
4. If the target is a `task`:
   - include the section body + the top 2 depends_on tasks + 2 reverse-dependents.
5. Assemble the response, compress-stages:
   - a. Full body of the target-sections (not trimmed).
   - b. For related documents — the `summary` field or the first 600 characters.
   - c. If the budget is exceeded — sections of related ones are sorted by tag-match, removed from the tail.
6. The `truncated: true` flag is set if something was trimmed.

## 5. Semantic search (L3)

Used only on explicit request.

- Embeddings are stored per-section (OpenAI/Anthropic-compatible or local-BGE, the choice is in the config).
- The index is updated asynchronously on the `RevisionCommitted` event.
- The query returns top-k sections (default k=5) with distances and excerpts.
- Does not hit the LLM-provider "on the fly" — everything is local via `sqlite-vss` / `pgvector`.

The idea is borrowed from the Restate `tools/lightrag`, but without an external service — the built-in index preserves integrity.

## 6. Surfaces

| Surface | Command |
|-------------|---------|
| CLI | `cod-doc context get --target module:M1-auth --depth L1 --budget 8000` |
| MCP | `context.get(...)` — the main interface for agents |
| REST | `GET /api/v1/context?target=...&depth=...` |

## 7. A result suitable for an LLM-prompt

A separate command `cod-doc context prompt --target module:M1-auth --depth L1`:

- Returns a ready markdown, where sections are marked with headings `# Target`, `# Related Docs`, `# Task Progress`.
- The format is stable → the agent knows how to parse it.

## 8. Caching

- The `context.get` response is cached by `(target, depth, content_hash)`. Cache invalidation — when a new revision appears on any included object.
- TTL can be disabled in the config (`cache.context.ttl`), in the embedded profile the cache is by default on-disk sqlite KV.

## 9. Integration with the Snowball Protocol (cod-doc legacy)

The current `MASTER.md.j2` from cod-doc describes the L0-L2 levels declaratively. In the new design:

- MASTER still exists and is rendered from the DB.
- But its "context_depth" no longer needs to be read manually — the agent calls `context.get(depth=...)`, and the response already matches the declarative protocol.
- The `MASTER.meta.context_depth` field remains — for compatibility and as a reflection of what is currently loaded into the last session.

## 10. Example of use by an agent

Request: "add a rate-limit on logout in M1 AUTH".

```
1. context.get(target=module:M1-auth, depth=L1)
   → target_body: overview; sections: api-specification, auth-lifecycle;
     related.tasks: 3 open (AUTH-022 open, AUTH-024 pending, AUTH-050 tests);
     related.stories: US-014.
2. task.create(plan=M1-auth-module, title="Implement: logout rate-limit",
               type=feature, depends_on=[AUTH-022])
3. doc.patch_section(doc=modules/M1-auth/overview, anchor=api-specification,
                     new_body=... rate-limit mention...)
```

All — without reading 70K bytes and without a manual choice of "what is relevant".

## 11. Guarantees

- The response does not include content from other projects, unless the target is cross-project.
- The response does not include links to private sections (`audience: [internal]`) if the call is `mcp:<external-client>`.
- Size bound: if `truncated: true`, `meta.missing_hints[]` suggests which slots were dropped; the agent can request them with an explicit follow-up.
