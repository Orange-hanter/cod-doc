---
type: guide
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-07-29
last_updated: 2026-07-29
audience: [contributors, agents]
related_docs:
  - HANDBOOK.md
  - cod-doc-guide.md
  - system/roadmap/ROADMAP.md
  - system/audit/2026-07-29-state-of-the-project.md
---

# Adoption Playbook — how to start using COD-DOC on your projects

> **Who.** The owner of repositories who already has cod-doc built, but has
> not set it up on any working project.
> **How it differs from [cod-doc-guide.md](cod-doc-guide.md).** That one is a tutorial
> "documentation from scratch" on a made-up example. This one is about **existing**
> repositories with already accumulated markdown.

## 0. First of all: fix the config

Right now `~/.cod-doc/config.yaml` is the result of a test run:

```yaml
model: test/model            # ← not a real model
api_key: sk-test-key         # ← not a real key
projects:
- name: integration-test
  path: /private/var/folders/.../pytest-57/.../my-repo   # ← a temporary pytest folder, it does not exist
```

While this is the case — `cod-doc project list` shows garbage, and `cod-doc agent run`
will not start. The minimum:

```bash
cod-doc project remove integration-test
```

and edit `model` / `api_key` for your OpenRouter (`base_url` already points
to it). **AI functions are optional:** import, plans, tasks, drift, search,
the Web UI and the entire MCP layer work without a key. The key is only needed for
`agent run`, `link suggest` and the semantic L3 in `context_get`.

## 1. Choosing a pilot: do not start with your favorite

Three criteria by which a project is fit for piloting:

1. **≥ 10 markdown documents** — otherwise cod-doc gives nothing beyond `grep`.
2. **A live backlog** — there is something to track. A project that is "finished and lying around" will show nothing.
3. **You are not afraid.** A pilot is a place where you will stumble.

By these criteria your repositories fall into four archetypes.
Each is a separate scenario below.

| Archetype | Project | md | What it tests |
|---|---|---|---|
| A. Engineering, doc-heavy | `_my/Mushrooms Shuchin` | 56 | core: import + plan + open questions |
| B. Code + docs | `_my/yana-reconciliation` | 40 | code-refs, commit-links, repo-index |
| C. Pure docs | `_my/UrukhaiMark` | 33 | drift, links, ADR |
| D. Big migration | `_my/Restate` | 641 | scaling limit |

**Recommendation: start with A, take B second.** A gives the maximum return per
unit of effort, B tests the untested layer (repo-index). C is too
light to find anything. D is not a pilot, see §6.

---

## 2. Scenario A — an engineering project with accumulated documentation

**Pilot: `_my/Mushrooms Shuchin`** (ICS: PLC, HMI, Modbus; 56 md, 40 commits).

### Why it is ideal

The project already has exactly the structure that cod-doc formalizes, just
assembled by hand:

- `Документация/01_Функциональная_спецификация.md` … `21_План_доработки_HMI.md`
  — **a numbered corpus of specs** ⇒ these are Documents.
- `Документация/03_Открытые_вопросы.md` — **open questions** ⇒ these are tasks
  and `decisions-and-questions`.
- `Документация/09_Аудит_проекта_2026-07-08.md`, `07_Аудит_и_улучшения.md`
  — **audits** ⇒ exactly the cadence of the `audit-cadence` skill.
- `16_План_доработки_прошивки_ШУ-ЗМ.md`, `21_План_доработки_HMI.md`
  — **plans** ⇒ these are `plan_create` + tasks.
- `02_Карта_сигналов_и_кросс-ссылки.md` — **cross-links by hand** ⇒ exactly
  what the link system does.
- `Дефекты_исходных_документов.md` — you already keep a **drift log** by hand.

Plus `AGENTS.md` and `CLAUDE.md` in the root — you already work there with agents.

### Steps

```bash
cd "/Users/dakh/Git/_my/Mushrooms Shuchin"

cod-doc project add . --name shuchin
cod-doc project init shuchin

# Reconnaissance. Look carefully at the "Архив" catalog — it will get into the import.
cod-doc import docs shuchin --dry-run
```

**Decision on `Архив/`:** cut it off with the `--exclude` flag (SYM-004) and make sure
via `--dry-run` that it is no longer in the list. Otherwise archived documents will
pop up in `search` and in `context_get` on a par with the live ones.

```bash
cod-doc import docs shuchin --exclude 'Архив*' --dry-run
cod-doc import docs shuchin --exclude 'Архив*'
cod-doc reindex files -p shuchin
cod-doc doc drift -p shuchin --all      # expect 0 discrepancies
cod-doc audit -p shuchin                # frontmatter: there will be findings, they are advisory
```

About `audit`: your documents were written without cod-doc frontmatter, so
FM-* findings will be there. They do **not** block — do not rush to fix them en masse.
Fix them on the next edit of the document.

### The first value — in 20 minutes

Move `03_Открытые_вопросы.md` into tracked tasks:

```bash
cod-doc plan ready -p shuchin      # empty — there is no plan yet
```

Create a plan for the current work front (via MCP or Python — CLI
`plan create` is not in the current release, see §7):

```
plan_create(project="shuchin", scope="pnr-2026-08")
plan_section_create(project="shuchin", plan_scope="pnr-2026-08", title="Открытые вопросы")
```

Then one task per question — `task_create` with `acceptance`. After that:

```bash
cod-doc plan ready -p shuchin        # the work queue
cod-doc search "Modbus" -p shuchin   # FTS over the whole corpus
```

**What you get right away:** `03_Открытые_вопросы.md` stops being a wall of text
that you have to reread in full. Questions become nodes with dependencies,
and `plan ready` answers "what can be done right now".

### What to expect that is bad

- Russian file names → `doc_key` of the form `Документация/03_Открытые_вопросы`.
  It works, but in the CLI you will have to quote. This is the first candidate for the friction log.
- Numbered prefixes (`00_`…`21_`) cod-doc does not understand as order —
  for it they are just part of the key.

---

## 3. Scenario B — code + documentation

**Pilot: `_my/yana-reconciliation`** (Python application; `app/`, `tests/`,
`docs/`, 40 md).

Tests the layer that has never worked on cod-doc itself (finding F2
of the audit 2026-07-29): **repo-index, code-refs, commit-links**.

```bash
cd /Users/dakh/Git/_my/yana-reconciliation
cod-doc project add . --name yana
cod-doc project init yana
cod-doc import docs yana --dry-run
```

Decide separately on `cache/`, `uploads/`, `data/`, `reports/` — if there
are `.txt` dumps there, they will fall under the import (the extension matches, the meaning —
does not). Move them out or empty them for the duration of the import.

```bash
cod-doc import docs yana
cod-doc reindex files -p yana      # ← the key step of this scenario
```

`reindex` builds `repo_file` / `repo_symbol` / `repo_import` taking into account
`.gitignore`. After it, search by symbols works, not only by prose.

What is worth checking here and recording in the friction log:

- `docs/OPEN_QUESTIONS.md` and `docs/CORRESPONDENCE_LOG.md` — entities that
  have direct analogs in cod-doc (open questions, activity log).
  How conveniently do they migrate?
- Code-refs of the form `[label](app/module.py)` in documents — do they resolve
  after `link backfill`?
- `cod-doc search "<class name>" -p yana --scope doc` — does it find
  documentation about the code?

---

## 4. Scenario C — a pure docs project

**Pilot: `_my/UrukhaiMark`** (33 md, everything in `docs/`, 4 commits).

The easiest entry — 10 minutes, and good as a **demo**, not as a pilot:
there is no live backlog here, so friction will hardly surface.

```bash
cd /Users/dakh/Git/_my/UrukhaiMark
cod-doc project add . --name urukhai
cod-doc project init urukhai
cod-doc import docs urukhai
cod-doc serve                       # → http://localhost:8765/p/urukhai
```

Here it is worth trying what is secondary in other scenarios:

- **ADR.** The project has `docs/architecture.md` and `docs/open-questions.md`,
  but no recorded decisions. `cod-doc adr new` + `cod-doc adr graph`
  give a supersede-DAG in Mermaid.
- **The drift cycle in full.** Edit a document on disk bypassing cod-doc →
  `cod-doc doc drift -p urukhai --all` will show `edited_in_place` → restore
  via `doc import`. This is the cycle cod-doc exists for.
- **Routine.** Set up `doc_drift` on a schedule and make sure the
  auto-check lives outside cod-doc (this is item C-7 of the roadmap).

---

## 5. The daily cycle after onboarding

The minimum that pays off the onboarding:

```bash
cod-doc plan ready -p <slug>          # what can be done now
cod-doc search "<term>" -p <slug>   # find without grep across 50 files
cod-doc doc drift -p <slug> --all     # have the docs not diverged?
```

Via MCP (Claude Code / Claude Desktop) — instead of manual chaining:

```
agent_capabilities()                  # L0: who am I, what skills
agent_pick(project="<slug>", agent_id="claude")   # task + context + skills in one call
… work …
agent_complete(task_id=..., agent_id="claude")
```

Connecting MCP — `.mcp.json` in the root of the working project:

```json
{
  "mcpServers": {
    "cod-doc": {
      "type": "stdio",
      "command": "/Users/dakh/Git/_my/cod-doc/.venv/bin/cod-doc-mcp",
      "args": ["--profile", "agent"]
    }
  }
}
```

The `agent` profile gives 6 tools instead of 103 — exactly what is needed in a working
session. `standard` / `full` — for admin scenarios and debugging.

---

## 6. Restate — a separate conversation, not a pilot

`_my/Restate`: **641 markdown, 922 commits**, `Docs/standards/` with nine
standards, `Docs/obsidian/` with a nested structure. This is the very project
for whose manual stack replacement cod-doc was conceived
([VISION.md §1](system/VISION.md)).

**Do not take it first.** Reasons:

- `import docs` by default is limited to `--max-files 1000` — it will fit, but
  in one chunk and without splitting into modules.
- `Docs/_archive/` and `Docs/api/_archive/` will have to be cut off manually:
  `--exclude '*/_archive'` (SYM-004) — the walker itself does not know them.
- The promised in VISION command `cod-doc import restate <path> --docs … --plans …
  --standards …` **does not exist**. The real importer (`services/restate_importer.py`,
  despite the name) is universal: only `import docs` and
  `import legacy-tasks` from `.cod-doc/tasks.yaml`. Parsing someone else's markdown plans
  into tasks is not automated.

The right order: first M1/M2 of the roadmap on pilots A and B, then Restate
as a separate migration plan — with a decision on what to port and what to leave in
Obsidian. Otherwise the very first attempt will give 641 documents without structure and an aversion
to the tool.

---

## 7. Known rough edges (as of 2026-07-29)

An honest list — so you do not stumble and decide it is broken:

| What | Status | Workaround |
|---|---|---|
| **`cod-doc doc export` on an old DB rewrites frontmatter** | 🟡 ADO-010 closed 2026-08-25; residual risk ADO-022 for DBs older than migration `0025_projection_fidelity` | `doc backfill-projection` before the first export — see below |
| ~~`cod-doc plan create` is not in the CLI~~ | ✅ `cod-doc plan create` / `plan section-create` / `plan sections` (same service as MCP `plan_create`) | — |
| ~~`import docs` without an exclusions flag~~ | ✅ closed by SYM-004: `--exclude` (a repeatable glob from the repo root) + `--dry-run` prints the final list | — |
| Markdown plans are not parsed into tasks | no automation | `task_create` by hand; the `plan-to-tasks` skill |
| `capabilities/project-bootstrap.md` describes `project new` | the doc is ahead of the CLI (`add` + `init`) | follow this playbook, not the capability |
| 66 web routes are not in the capability §3 table | advisory drift, D-1 of the roadmap | does not affect operation |
| Global config with test values | §0 of this document | fix before the first pilot |

### 🟡 About `doc export` — in more detail

The content corruption from [F7 of the audit](system/audit/2026-07-29-state-of-the-project.md)
(the preamble was glued to the first heading, H1 was lost, `type: capability`
was replaced with `module-spec`) was closed in ADO-010 2026-08-25: import → export
is byte-identical on the entire `docs/` corpus.

**Residual risk for pilots — ADO-022.** If the project DB was set up before
migration `0025_projection_fidelity`, the documents do not have the original file
form recorded, and export re-serializes the frontmatter (reorders keys, adds a made-up
`type/status/owner` block to a file without frontmatter) and adds an `# H1`
that was not in the source. That is exactly how 107 files out of 121 were once rewritten.

`doc export` itself refuses to write such a file — the message will contain
`frontmatter_raw` and `backfill`. Before the first export on such a DB run:

```bash
cod-doc doc backfill-projection --project <slug> --dry-run   # look
cod-doc doc backfill-projection --project <slug>             # fix
```

It restores the form from the files on disk and does not touch the metadata changed
in the DB. `--force-write` on this error is not a workaround, it is the corruption itself.

If the DB → markdown direction is not yet needed for you, work in the
"**files are the source, the DB is the index**" mode: `import docs` on onboarding,
`doc import <file>` after manual edits. Your markdown is not touched at all
in this case — the import only reads.

The `stale_export` state in `doc drift` in this mode is **normal** and
does not need fixing: it only means "the projection has never been exported".
The alarming state is `edited_in_place` (the DB is behind the file), it is cured
by `doc import`.

## 8. How to tell if it worked

After two weeks of pilots answer three questions:

1. Did you open `plan ready` instead of trying to remember what to do?
2. Did `doc drift` catch a divergence earlier than you yourself?
3. Did you search via `cod-doc search` instead of `grep`?

**Three "yes"** — the tool has taken root, you can move on to M3 of the roadmap (features).
**Three "no"** — it is not the tool that has taken root, but the habit; you need to figure out what
exactly was in the way (the friction log, C-5), and not build the next feature on top.

The formalized onboarding procedure — the skill
[`project-onboarding`](../cod_doc/skills/project-onboarding/SKILL.md);
the "project is set up" criteria are there too.
