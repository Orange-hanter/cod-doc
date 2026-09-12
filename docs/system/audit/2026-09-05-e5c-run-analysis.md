---
type: audit-report
scope: e5c-openrouter-run
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-05
last_updated: 2026-09-05
audience: [contributors, agents]
related_docs:
  - ../releases/2026-08-30-sprint-m4.md
  - 2026-09-02-sprint-m4-proof-of-value.md
  - ../roadmap/sprint-m5-trustworthy-gate.md
---

# Audit — review of the combat E5-C run on OpenRouter (ADO-065)

## 1. TL;DR

The run `20260830T133715-39618f` is reviewed from the recorded data; no new runs
were made.

- **The tester ate not 60%, but 67.8% wall-time** (826.0 s out of 1219 s). The share stated in the release note is understated.
- **The cause of the skew is not "a slow model", but the number of turns.** The tester made
  22 turns and 23 tool calls against 10 and 10 of the executor; of them at least 8 turns
  went to recovery after three consecutive failed `Edit`
  (`old_string not found`) and one GNU-ism in the shell (`cat -A` on macOS).
  At uniform turns this is ≈300 s, i.e. **a quarter of the whole run was spent
  on the mechanics of the tools, not on the work**.
- **The cost of the qwen role cannot be measured** — the kimi shoulder writes neither cost nor
  tokens (finding F3). The only machine figures about money in the run are
  the reviewer's $0.2570491, decomposed across four generations.
- **The main expense item in both measurements is cache-write, not model output.**
  For the reviewer 57.6% of his bill is a one-time dump of a 51 605-token prompt into a
  5-minute cache, which is immediately discarded.
- **Recommendation:** return the tester to `anthropic/claude-sonnet-5`. The price of the
  decision is **no more than $0.51 per task** (possibly zero), the gain is
  **≈553 s (9 minutes) off the critical path**. A more precise calculation is impossible until
  F3 is closed.

## 2. Data sources

Everything below is read from the artifacts of the `stand-e11b` stand
(ZAIrgRush repository, `experiments/stand-e11b/`, branch `e11b`, stand commit
`a5812f3`, `swarm_sha = a2d9c2f`). The paths are absolute because the stand lives outside
cod-doc.

| Source | What we take |
|---|---|
| `…/stand-e11b/.swarm/metrics.jsonl`, lines 27–32 | run phases: `wall_s`, `dur_s`, `cost_usd` |
| `…/stand-e11b/.swarm/metrics.jsonl`, lines 1–26 | the base run `20260823T124541-48b215` (claude on all roles, the same stand) |
| `…/stand-e11b/.swarm/helper-metrics.jsonl`, line 5 | the commit-message helper, `dur_s = 43.5`, `gemma4:31b` |
| `…/stand-e11b/.swarm/log/run.jsonl`, lines 37–46 | the run timeline from `state_written` to `memory_reflect` |
| `…/stand-e11b/.swarm/log/s5dc-i1-a1-review.json` | the review result: `duration_api_ms = 97869`, `total_cost_usd = 0.25704910` |
| `…/stand-e11b/.swarm/log/s5dc-i1-a1-review-stream.jsonl` | `ttft_ms` and `usage.cost` of each of the 4 generations |
| `…/stand-e11b/.swarm/log/s5dc-tester.jsonl` | 47 lines: 22 assistant turns, 23 tool calls, 0 token records |
| `…/stand-e11b/.swarm/log/s5dc-i1-executor.jsonl` | 22 lines: 10 turns, 10 tool calls, 0 token records |
| `/Users/dakh/Git/_my/ZAIrgRush/experiments/findings.jsonl`, line 330 | `spend $0.98 out of $5` and findings F1–F4 (a textual entry, not a machine measurement) |

## 3. Table: role → cost → latency → share of wall-time

The run wall-time is **1219.0 s** (`13:37:15Z` run_id start → `13:57:34Z`
last `state_written`, `run.jsonl:37` and `:44`).

| Role | Engine / model | Cost | Latency | Share of wall-time |
|---|---|---|---|---|
| **tester** | kimi CLI → `or-qwen` (`qwen/qwen3.8-27b`) | **not logged (F3)** | 826.0 s | **67.8%** |
| **executor** (`implement`, 1 iteration) | kimi CLI → `or-qwen` | **not logged (F3)** | 241.1 s | 19.8% |
| **reviewer** | claude CLI → `anthropic/claude-sonnet-5` (OpenRouter) | **$0.2570491** | 104.4 s (of which API 97.9 s) | 8.6% |
| commit-message helper | ollama `gemma4:31b`, locally | $0 (local model) | 43.5 s | 3.6% |
| gate ×2 + scope + I/O | — | $0 | 4.0 s | 0.3% |
| **Run total** | | **$0.98 by the OpenRouter key delta** | **1219.0 s** | 100% |

Sum check: 826.0 + 241.1 + 104.4 + 43.5 = 1215.0 s; the remainder 4.0 s is
two gate runs, a scope check and a state write.

### 3.1. Reviewer by generations (the only machine money of the run)

From `s5dc-i1-a1-review-stream.jsonl`, the `event.usage.cost` fields in `message_delta`:

| # | ttft | cache-write | cache-read | output (of which thinking) | Cost |
|---|---|---|---|---|---|
| 1 | 5.325 s | 51 605 | 0 | 1 060 (967) | $0.1396165 |
| 2 | 4.738 s | 1 978 | 51 605 | 4 059 (2 840) | $0.0558600 |
| 3 | 1.733 s | 4 232 | 53 583 | 1 217 (0) | $0.0334706 |
| 4 | 3.577 s | 1 394 | 57 815 | 1 305 (35) | $0.0281020 |
| **Σ** | | **59 209** | **163 003** | **7 641 (3 842)** | **$0.2570491** |

The sum over the generations matches `total_cost_usd` in `s5dc-i1-a1-review.json` and
`cost_usd` in `metrics.jsonl:32` to the last digit.

**Unit prices derived from these four records** (`is_byok: false`, i.e.
this is OpenRouter's own billing, not a CLI estimate):

| | OpenRouter (`anthropic/claude-sonnet-5`, 2026-08-30) | First-party claude (the same stand, 2026-08-23) |
|---|---|---|
| cache-write | **$2.50 / 1M** | **$4.00 / 1M** |
| cache-read | $0.20 / 1M | $0.20 / 1M |
| output | $10.00 / 1M | $10.00 / 1M |

The 08-23 prices are derived by solving a system of 14 records `metrics.jsonl:1–26` —
they converge on all fourteen. The conclusion to keep in mind when
planning the budget: **the Anthropic-skin OpenRouter is cheaper than first-party exactly on
cache-write, by 37.5%; cache read and output cost the same.**

## 4. Reconciliation of the declared numbers with the source

| Declared (ADO-065 contract / release note) | Fact by artifacts | Verdict |
|---|---|---|
| reviewer sonnet-5: 4 generations, $0.2570 | 4 generations, $0.25704910 | ✅ matches exactly |
| review wall 104.4 s | `metrics.jsonl:32` `dur_s = 104.4` | ✅ |
| tester (or-qwen) wall 826.0 s | `metrics.jsonl:28` `wall_s = 826.0` | ✅ |
| tester ≈ **60%** of the whole wall-time | **67.8%** of 1219 s (70.5%, if counting only model phases) | ❌ understated by 8 pp |
| reviewer latency **1.5–4.7 s** | ttft of generations **1.733–5.325 s**; the session's total `ttft_ms` 18.586 s | ❌ does not match either the lower or the upper bound |
| spent $0.98 out of $5 | `findings.jsonl:330` — a textual entry about the OpenRouter key usage delta | ⚠️ confirmed only by prose, no machine record |
| smoke claude $0.1978, 79k cache-write | **no artifact on disk** (`/usr/bin/grep -r` over ZAIrgRush and cod-doc) | ⚠️ not verifiable; but 79 120 × $2.50/1M = $0.1978 — the figure is arithmetically consistent with the unit price measured in the same run |
| executor+tester ≈ $0.52 by delta | is reproduced **only** as the remainder 0.98 − 0.2570 − 0.1978 = $0.5252 | ⚠️ a derived value, not a measurement; not decomposable by roles (F3) |
| F3 = "per-generation id kimi is not logged" | in the source F3 = "kimi executor does not write cost/tokens to metrics.jsonl" | ❌ the contract formulation diverged from `findings.jsonl:330` |

Reconciliation result: **everything that was taken by machine matched; everything declared about the
qwen shoulder's money is a reconstruction.** The only two numbers that diverged in essence
are the tester's share (understated) and the reviewer's latency spread.

## 5. Why the tester ate 67.8% of the time

### 5.1. The mechanism

The point is not that "qwen is slow by itself". The decomposition:

1. **The role is turn-heavy by construction.** An independent tester writes tests without
   seeing the implementation, and is obliged to prove itself that they catch wrong implementations.
   In the log this is 5 separate runs of heredoc-python: the canonical formatter,
   then mutants (`','.join` without quoting and others) — `s5dc-tester.jsonl`,
   turns #32, #34, #42, #44. This is not an expense, but the very meaning of the role: exactly this
   check caught **a typo of the tester itself** (in the field `['a,"b,c"']`
   an extra `]` slipped in, turn #34).
2. **The failure of an exact match in `Edit`.** Three `Edit` in a row returned
   `old_string not found` (results on lines 15, 19, 27 of the log), although
   `od -c` showed bytes matching `old_string` (turn #22). The model went into
   diagnostics: `cat -A` → **`cat: illegal option -- A`** (BSD cat on
   macOS, line 21) → `od -c` → a repeat `Read` → two re-entries on another
   anchor. The result — the file is rewritten in full via `Write` (turn #38).
3. **The arithmetic of turns.** 22 assistant turns / 23 tool calls of the tester
   against 10 / 10 of the executor. Average turn: **37.5 s** for the tester against
   **24.1 s** for the executor (826.0/22 and 241.1/10). The tester's turns are heavier and
   because the context grows faster: 35 288 characters of tool results
   against 13 671 of the executor.
4. **Loss estimate.** Turns #14–#29 (8 turns, 9 tool calls) produced
   nothing but the bypass of the `Edit` failure. At uniform turns this is ≈300 s —
   **36% of the tester phase and 25% of the whole run**.

An important caveat: to decompose 826 s into "the model's network latency" and "the local
time of the tools" is **impossible** — the kimi log writes neither turn timestamps
nor usage. This is exactly what the real review ran into, and the main argument
in favor of F3.

### 5.2. What to do about it

| Measure | Effect | Who |
|---|---|---|
| Return the tester to `anthropic/claude-sonnet-5` | −553 s to the phase (826 → ≈273 s by the average of four claude runs of the same stand) | ZAIrgRush model config |
| Close F3 (usage + turn timestamps in the kimi shoulder) | without this the next such review will again run into an estimate "by average turn" | ADO-071 |
| Remove from the tester the duty to edit a file already written by it | 3 `Edit` failures out of 4 calls; the policy "write the file in full via `Write`" eliminates the class | ZAIrgRush, out of scope of ADO-065 |
| Run the tester and executor in parallel | both roles work from a spec; in this run the executor already found the ready file of tests and declared a deviation (`run.jsonl:39`). Removes 826 s from the critical path entirely | an architectural decision of ZAIrgRush, **out of scope**, requires a separate RFC |

What **not** to do: run a new run to "see if it is faster". The answer to the question "why it was so" is already read from these logs;
a new run costs money and gives another data point, not an explanation.

## 6. Recommendation on the OpenRouter shoulder config — with the price of the decision

### 6.1. Bases for comparison

| Configuration | Run | Wall of model phases per task | Money per task |
|---|---|---|---|
| claude on all roles (first-party) | `20260823T124541-48b215`, 4 tasks | 493 s (tester 273.4 + executor 87.7 + review 88.1×1.5 attempts) | $1.16 (average over s1ch/s2tn/s3nm/s4cli: 1.2828 / 1.2176 / 0.9220 / 1.2173) |
| qwen (tester+executor) + sonnet (reviewer) via OpenRouter | `20260830T133715-39618f`, 1 task | 1171.5 s | $0.98 per run, of which $0.2570 reviewer |

Caveat: the tasks are not identical (s1ch…s4cli grew the suite 57→89 tests, s5dc —
89→119), but this is the same stand, the same class of tasks and the same loop.

### 6.2. The decision and its price

**Tester → `anthropic/claude-sonnet-5` (OpenRouter).**

- What we buy: **−553 s** per task (826.0 → 273.4 s by the average claude phase
  of the same stand).
- What we pay: the token profile of the claude tester, recalculated at the unit prices
  of OpenRouter (§3.1), gives **$0.51 per phase** (instead of $0.584 by first-party).
  The cost of the qwen tester is unknown, but **bounded above by $0.525** —
  this is the whole remainder of the run on both qwen shoulders. So **the price of the decision lies in
  the range from $0 to +$0.51 per task** and will become exact only after F3.
- Why exactly this role: the model latency is multiplied by the number of turns, and
  the turn-density of the tester role is 2.2 times higher than the executor's. A slow
  model on a turn-heavy role is the worst of all placements.

**Executor → leave on `qwen/qwen3.8-27b`.**

- This is what E5-C was set up for: the documentation block reached the prompt, the implementation
  literally repeated the convention from cod-doc, the task's trap did not fire, the task
  is closed in one iteration (`findings.jsonl:330`, `tasks.json` — `iterations: 1`).
- The cost: 241.1 s against 87.7 s of claude, i.e. **+153 s** — on a role with
  10 turns this is tolerable.

**Reviewer → leave `anthropic/claude-sonnet-5`, but remove the cold cache tax.**

- 57.6% of the reviewer's bill ($0.1480 out of $0.2571) is the dump of 59 209 tokens into
  a 5-minute ephemeral cache, which does not survive the phase. In the base run
  08-23 six reviewer calls dumped 150 873 cache tokens, each time anew
  (`metrics.jsonl`, `cache_write` lines 6, 12, 13, 19, 25, 26).
- Two measurable levers: **(a)** a 1-hour cache instead of 5-minute —
  `ephemeral_1h_input_tokens` in all generations of this run is zero, i.e.
  the lever was never tried; the saving is plausible on multi-task
  runs, but **is not derived from this data**, it must be measured. **(b)** trim
  the reviewer's prompt: 51 605 tokens for the first generation for a diff of a module of a hundred
  lines; every 10k tokens removed = **−$0.025 per review** by the measured price of
  cache-write.
- To assert the saving from (a) without a measurement is impossible — this is exactly the trap because of which
  ADO-065 was needed at all.

**Expected profile after the config edit:** ≈618 s of model phases instead of 1171.5
(−47%) with the change of the bill per task within ±$0.5.

The recommendation is filed as task **ADO-074** (blocked_by ADO-071: until the cost of
the qwen shoulder is logged, the price of the decision cannot be fixed by a number).

## 7. The fate of findings F1–F5

The source of F1–F4 is `experiments/findings.jsonl:330` (entry `E5-C`,
`2026-08-30T14:05:00`); F5 is the release note `releases/2026-08-30-sprint-m4.md:60`.
None remains "mentioned in the release note".

| # | Formulation of the source | Decision |
|---|---|---|
| **F1** | `cod_doc_bin` is not in `KNOWN_CONFIG_KEYS` — a warning, but the key is read by `docctx` | **To work → ADO-073** (low). The key is working, the warning lies; this is exactly the kind of noise because of which warnings stop being read. |
| **F2** | `project='zairgrush'` is hardcoded in `promptbuilder.docs_block:198` — a `doc_context_project` key is needed | **To work → ADO-072** (medium). A direct blocker of the verdict "we scale": until the project is hardcoded, the loop cannot be run on any other cod-doc project. |
| **F3** | the kimi shoulder does not write cost/tokens to `metrics.jsonl`; spend was measured by the OpenRouter key delta | **To work → ADO-071** (high). This is the only reason why table §3 contains dashes, and §6 — a range instead of a price. The priority is higher than the rest: without it the next review will be the same reconstruction. |
| **F4** | `total_budget_usd` is counted from the stand's history ($4.64 of past runs ate the $5 ceiling) | **Closed without a task.** The figure is confirmed: the sum of `cost_usd` of the 08-23 run = **$4.6397** (14 records `metrics.jsonl:1–26`). But the run was not interrupted, and this is fixed by a launch parameter — the finding itself recommends so ("on the old stand, raise with a margin"). Changing the budget semantics is a design decision of ZAIrgRush, outside the scope of ADO-065. **Reopen if at least one run actually breaks on the ceiling.** |
| **F5** | qwen reasoning is not saved in stream-json | **Closed without a task, as subordinate to F3.** Verified on this review: to explain 826 s, turn timestamps and usage were needed, not the text of the reasoning. The claude shoulder writes thinking tokens (3 842 per review) and they were never needed. Storing qwen reasoning bloats the logs (the tester's log is already 86 KB) for the sake of diagnostics for which there has been no demand yet. **Reopen if the question "why the model chose this" arises, and not "how much it cost".** |

## 8. What this review could not prove

An honest list, so that the next session does not take a reconstruction for a measurement:

1. **The cost of the tester and executor separately.** There is no machine
   record; $0.5252 is the subtraction remainder, and it rests on the number $0.1978, for which
   there is no artifact.
2. **The logs of the claude smoke run.** `/usr/bin/grep -rl "2026-08-30"` over all of
   ZAIrgRush gives six files, none of which is smoke. The figure is consistent
   arithmetically (79 120 tokens × $2.50/1M), but not confirmed.
3. **The decomposition of 826 s into network and local tools.** The estimate "≈300 s for
   the bypass of the `Edit` failure" is built on the assumption of uniform turns.
4. **The benefit of a 1-hour cache.** `ephemeral_1h_input_tokens = 0` in all
   generations — the lever was not enabled, the saving is not measured.

Items 1 and 3 are closed by task ADO-071 (F3). Item 4 — only by a measurement.
