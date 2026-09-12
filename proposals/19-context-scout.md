# 19 — Context-Scout: "smart grep" via cod-doc MCP

> Category: 🟡 Adaptation · Risk: low · Dependencies: doc_search (FTS5), OBI-040 (FTS5), model_catalog

## Context: "where do we…?"

A vibecoder constantly asks the project questions that `grep` answers poorly:

- "Where do we compute food cost?" → `grep -r "food_cost" cod_doc/` shows 30 lines, does not explain which one is current.
- "Why did we switch from YAML to SQLite for the DB?" → you need ADR-001 context, not git log.
- "Which tasks block the release?" → `task_list_blocked` exists, but without grouping by "why blocked".

Existing cod-doc tools can already search by parts:
- `doc_search` — FTS5 over `docs/**`.
- `task_search` (via `task_list` + filters) — over tasks.
- `adr_list` / `adr_get` — over ADRs.
- OBI-040 — unified FTS5 (repos + docs + tasks).

**Missing:** a single entry point that **aggregates** results from all these sources and returns a **human-friendly summary**.

## Current state of cod-doc

- FTS5 indexes: `docs` (`search_service`), `tasks`, `repo_index` (OBI-040), `code_ref`.
- MCP: `doc_search` (via `doc_list` + filters), `task_list`, `task_summary`, `adr_list`.
- `tool_search`, `tool_describe` — search over the MCP-tools themselves.
- **Missing:** an aggregator that asks all 4 sources once and returns a ranked summary.

## Proposal

Create `cod_doc/services/context_scout.py` + CLI command `cod-doc scout`:

### 4.1. Pipeline

```
scout(query="where is food cost computed", project="mozarella", limit=10)
  ↓
  1. doc_search(query)              → top 5 docs (FTS5 ranked)
  2. task_search(query)             → top 5 tasks (status filter opt.)
  3. adr_search(query)              → top 3 ADRs
  4. code_ref_search(query)         → top 5 files (via OBI-040)
  ↓
  5. LLM-rerank (opt.): "here are 18 candidates, pick top-5, give a 1-paragraph answer"
  ↓
  6. Return:
     {
       "answer": "...",                          # LLM synthesis
       "evidence": [
         {"type": "doc",   "id": "...", "title": "...", "snippet": "..."},
         {"type": "task",  "id": "COD-456", "status": "in_progress", "title": "..."},
         {"type": "adr",   "id": "ADR-003", "status": "ACCEPTED", "title": "..."},
         {"type": "file",  "path": "cod_doc/services/food_cost.py", "refs": 3},
       ],
       "cost": {"input_tokens": N, "output_tokens": M, "model": "..."}
     }
```

### 4.2. CLI

```bash
$ cod-doc scout "where is food cost computed" --project=mozarella

📍 Answer (claude-sonnet-4-6, 1.2s):
   Food cost is computed in `cod_doc/services/food_cost.py`, method `calculate_dish_cost`.
   Uses the `recipes` + `warehouse_stock` tables. Related task COD-456 (in_progress),
   docs: docs/system/DATA_MODEL.md#recipes. ADR-005 defines
   that food cost must account for depletion from QuickResto.

📂 Evidence (5):
   1. 📄 docs/system/DATA_MODEL.md (rank 0.92)
      "...table `recipes` is the canonical source for ingredient-level cost..."
   2. 🔧 COD-456 (in_progress, 3 days)
      "Add depletion-aware food cost calculation"
   3. 📐 ADR-005 (ACCEPTED)
      "QuickResto depletion flow → recipes → food_cost"
   4. 🗂️ cod_doc/services/food_cost.py (3 references)
   5. 📄 docs/handbook/mozarella/restaurant-ops.md (rank 0.71)
      "...food cost is computed daily at 23:00..."

💰 Cost: $0.0012 (1240 in / 380 out)

$ cod-doc scout "why SQLite and not Postgres" --no-llm  # fast mode, no LLM-rerank
📂 Evidence (8):
   1. 📐 ADR-001 (ACCEPTED) "Single-tenant SQLite as canonical state"
   2. 📄 docs/system/ARCHITECTURE.md#storage
   ...
```

### 4.3. MCP-tool

```
scout(query, project?, limit=10, use_llm=true, model?) -> ScoutResult
```

### 4.4. Options

| Flag | Behavior |
|---|---|
| `--no-llm` | No LLM-rerank, pure FTS5 → ranked list |
| `--type=doc,task` | Filter by evidence types (default all 4) |
| `--status=in_progress` | Only tasks in this status |
| `--json` | Machine-readable output (for pipelines) |
| `--since=7d` | Only recent entities |

## Effect

| Metric | Before | After |
|---|---|---|
| Time to "where is it in the code" | 5-15 min (grep + manual walk) | 5-15 sec (`scout` + 1 screen) |
| Context for an LLM-agent | 1-2K tokens on manual collection | 1 call, structured response |
| Onboarding: "tell me about the project" | a day (read MASTER.md) | 5 min (10 queries to scout) |

## Dependencies

| Component | Needed for |
|---|---|
| `doc_search` (FTS5) | search over docs |
| `task_list` / `task_search` | search over tasks |
| `adr_list` | search over ADRs |
| OBI-040 (`01ba3ab`) | unified FTS5 (repos + docs + tasks) |
| `model_catalog` (COD-059) | LLM choice for rerank |
| agent_pick (`30fffed`) | pattern for project context resolution |

## Structure

```
cod_doc/services/
├── context_scout.py              # core: aggregate, rerank, format
├── context_scout_prompts.py      # LLM prompts
cod_doc/mcp/tools/
└── context_scout_tools.py        # MCP surface
cod_doc/cli/
└── scout.py                      # CLI: cod-doc scout
tests/services/
└── test_context_scout.py
```

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| LLM-rerank hallucinates (mentions a non-existent task) | Evidence-validation: every `evidence.id` is checked in DB before return. If the LLM referenced a non-existent one — fallback to FTS5-only with a warning. |
| Latency (4 parallel searches + LLM) | Parallel FTS5 (asyncio.gather); LLM-cache on (query, project, 1h TTL). |
| Privacy: LLM sees task titles | `privacy=true` tasks are filtered out of evidence (like in proposal 18). |
| Different FTS5 indexes use different tokenizers | Query normalization in scout: lowercase + trim + Unicode NFKC + min length 2 chars. |

## Acceptance criteria

1. `cod-doc scout "..."` works end-to-end, prints Answer + Evidence + Cost.
2. `--no-llm` works without LLM, latency < 1 sec on 100K docs.
3. LLM-rerank evidence passes validation: all ids exist in DB.
4. `--json` mode returns machine-readable JSON for pipelines.
5. Cost tracking: real token count (input/output) and cost in USD.
6. Privacy: a task with `privacy=true` is absent from evidence.

## Alternatives

- **`ripgrep + fzf + manual walk`** — what we do now, does not scale.
- **Embeddings-based semantic search** — more expensive, requires an embedding index, does not use the existing FTS5.
- **Just embed `grep` into CLI** — ignores cod-doc structure, does not use ADR/task links.

## Sources

- Cursor `@codebase` — the pattern "ask the project a question, get ranked evidence".
- Sourcegraph Code Search — UX reference.
- Paperclip [`skill: paperclip-converting-plans-to-tasks`](https://github.com/paperclipai/paperclip) — a narrow skill for one task (our scout = the reverse skill: task → where in code).
