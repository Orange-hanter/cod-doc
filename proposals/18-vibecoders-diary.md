# 18 — Vibecoder's Diary: activity_log → human-friendly daily doc

> Category: 🟡 Adaptation · Risk: low · Dependencies: 09-activity-log, model_catalog (COD-059)

## Context: "what did I code yesterday?"

A vibecoder works in a flow. By the end of the day in `activity_log` (proposal 09) 30-80 events accumulate: `task_checkout`, `doc_create`, `task_update_status`, `adr_link_task`, `commit_link`, etc. A week later — impossible to recall.

Existing pain:
- **No human-friendly review of the day.** `activity_list` returns raw JSON events. To understand "what did I do today" — you need to aggregate manually.
- **No project changelog.** If mid-sprint you are pulled away for 3 days — you cannot catch up on what happened.
- **No story-telling.** A dataset of 50 events needs a narrative: "yesterday finished OBI-040, found drift in ADR-002, fixed pre-commit".

## Current state of cod-doc

- `activity_service` (`cod_doc/services/activity_service.py`) — emit/list/get, PCA-111.
- `model_catalog` (COD-059) — already lists LLM-models with prices.
- `section_summary_service` — generates section summaries (LLM-pass infra exists).
- MCP-tools: `activity_list`, `activity_for_run`, `activity_get`.
- **Missing:** a scheduled job that turns `activity_list` for a day into a `doc` of type `daily/YYYY-MM-DD.md`.

## Proposal

Create `cod_doc/services/daily_diary.py` + routine `daily_diary_evening`:

### 4.1. Pipeline

```
activity_list(since=yesterday_18:00, until=today_18:00)
  ↓ group by (project, task_id, file_path)
  ↓ filter out: noise events (heartbeats, capability refreshes)
  ↓ LLM-prompt: "here are 50 events, make a human-friendly changelog in English"
  ↓ LLM returns: { sections: [{title, summary, files_touched, tasks_done}], highlights: [...] }
  ↓ doc_create(type='daily', slug='YYYY-MM-DD', body=...)
  ↓ activity_log.emit(event='daily_diary_generated', doc_id=...)
```

### 4.2. Where the diary lives

- **Inside cod-doc**, as a `doc` with the new type `daily` (next to `system`, `adr`, `task_doc`).
- **Web-page** `/p/<project>/diary` — a calendar with clickable days.
- **Opt.:** `cod-doc diary today` CLI — prints today's diary in the terminal (Markdown).

### 4.3. Routine

```python
# cod_doc/services/daily_diary.py
def generate_daily_doc(
    project: str,
    date: date,
    *,
    model: str = "anthropic/claude-sonnet-4-6",  # from model_catalog
) -> Doc:
    """Generate a human-friendly daily doc from activity_log."""
    events = activity_service.list(
        project=project,
        since=datetime.combine(date, time(0, 0)),
        until=datetime.combine(date, time(23, 59)),
    )
    # ... LLM-pass → DocCreate
    return doc

# cod_doc/routines.py (proposal 07)
routine_register(
    name="daily_diary_evening",
    schedule="0 18 * * *",  # every evening at 18:00
    handler="cod_doc.services.daily_diary:generate_for_all_projects",
)
```

### 4.4. Prompt design (the key part)

```yaml
system: |
  You are the Vibecoder's Diary writer. You receive a list of events from cod-doc
  activity_log for one day. Your task is to make a human-friendly
  review in English, 200-400 words.

  Structure:
  - Heading: "📅 YYYY-MM-DD — <project>"
  - A short TL;DR (1-2 sentences)
  - What was finished (per task)
  - What was started but not closed
  - Suspicious patterns (e.g. 5 returns to in_review per day = bottleneck)
  - Quotes from code or PR, if any

  Do not invent. If events are few (≤5) — write "quiet day, fixing small things".

user: |
  Here are the events for {date} for project {project}:
  <events>
  {events_json}
  </events>
```

### 4.5. MCP-tools (cycle-5 surface)

```
diary_generate(project, date?) -> Doc           # one-off launch
diary_list(project, since?, until?) -> list[Doc]
diary_today(project) -> str  # Markdown for CLI
```

## Effect

- **Morning onboarding 1 minute.** Yesterday's diary — on the project home page.
- **Weekly/monthly review** — a series of diaries read in a row restores context.
- **Post-mortem after an incident** — `activity_for_run` + `diary_get` side by side.
- **Vibecoder motivation** — you see your progress, not "oh, nothing done again", but "here are 5 tasks closed, 3 on review".

## Dependencies

| Proposal / component | Needed for |
|---|---|
| `09-activity-log` (PCA-912) | event source |
| `07-routines` (PCA-211) | cron infrastructure |
| `model_catalog` (COD-059) | LLM choice for generation (cost-aware) |
| `section_summary_service` | LLM-pass pattern over docs |

## Structure

```
cod_doc/services/
├── daily_diary.py              # core: generate, list, get
├── daily_diary_prompts.py      # prompt templates
cod_doc/mcp/tools/
└── diary_tools.py              # MCP surface
cod_doc/api/
└── routes_diary.py             # /p/<project>/diary web page
cod_doc/templates/web/
└── diary.html                  # calendar view
tests/services/
└── test_daily_diary.py
```

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| LLM hallucinates (invents events) | The prompt strictly requires "do not invent, quote task SHAs". Validation: every mentioned task_id is checked in DB. |
| Expensive on tokens (50 events × 1K tokens per day × 30 days = 1.5M tokens / month) | model_catalog: Sonnet for default, Haiku for fast days; opt. disable for quiet days (≤5 events). |
| Privacy: sensitive task titles leak to LLM | `privacy` field on task: if True — the event is filtered before LLM. |
| The diary itself becomes a source of drift | the doc is created with type `daily`, **does not** participate in `update_master_hashes`. |
| Style inconsistency between days | One system prompt, one template — style is stable. |

## Acceptance criteria

1. `cod_doc/services/daily_diary.py` exists, covered by unit tests.
2. `routine daily_diary_evening` is registered, creates a doc when there are events.
3. CLI `cod-doc diary today --project=X` prints Markdown to stdout.
4. Web `/p/<project>/diary` shows a calendar with clickable days.
5. Cost-aware: for a project with ≤5 events — the diary is not generated, `daily_diary_skipped_quiet_day` is written to `activity_log`.
6. Privacy: a task with `privacy=true` does not appear in `events_json` sent to the LLM.

## Alternatives

- **Just read `git log --author=...` every day** — works, but loses task, ADR, docs context.
- **Notion / Obsidian daily note by hand** — not automated, after a week you give up.
- **An LLM agent reading `activity_list` ad-hoc on request** — works, but without a scheduled reminder — you forget to ask.

## Sources

- Real pain: when trying to recall "what did I do on May 12" — 30 minutes in git log.
- Paperclip [wake-payload pattern](https://github.com/paperclipai/paperclip) — the pattern "agent gets a compact summary, not raw data".
- Obsidian Daily Note plugin, Notion Daily Journal — UX reference.
