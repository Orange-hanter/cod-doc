# 16 — AI-Pair-Hacker: cod-doc in the vibecoder's loop

> Category: 🔵 Architecture · Risk: medium · Dependencies: 06 atomic-checkout, 09 activity-log, OBI (code-ref)

## Context: vibecoder pain

Vibecoding (Claude Code / OpenCode / Cursor) radically accelerates writing code, but creates systemic pain:

- **Documentation drift.** Code outpaces docs. A week later the author does not remember why they changed `task_service.update_status` and which edge cases they covered.
- **Lost context.** "Where do we compute food cost?" — a question that `grep` answers poorly, and `git log --all -S "food cost"` — even worse.
- **No post-hoc audit.** "What did the agent do in run X?" — currently unanswerable without a manual `git log` review.

Existing cod-doc proposals already close **part** of the problem:
- `09-activity-log` writes every mutation to `activity_service.emit(...)`.
- `06-atomic-checkout` protects from races in UI/CLI/MCP.
- OBI (code-ref parser, `22f7e45`, `01ba3ab`) links commits ↔ files ↔ entities.

**Not closed:** no one **links** a specific vibecoder edit to a specific task / document in cod-doc in real time.

## Current state of cod-doc

- The `MCP-server` exposes 119 tools, including `task_*`, `doc_*`, `plan_*`, `activity_*`, `commit_link_*` (`OBI-010/011`).
- `activity_service` emits events on every mutation (proposal 09 / PCA-912).
- `commit_link_service` (OBI-010) already knows how to link commits ↔ tasks.
- `task_checkout` (PCA-200) — atomic task acquisition.
- **Missing:** a plugin / wrapper that would call cod-doc **from** the vibecoder loop.

## Proposal

Create a **separate sub-project** `cod-doc-pair/` (or a module inside `cod_doc/agent/`) — a lightweight Python client + CLI that integrates with vibecoder tools and cod-doc MCP:

### 4.1. Pre-edit hook: "is this documented?"

Before `git commit` or before a big `Edit` the plugin asks cod-doc:
```
cod-doc pre-edit --project=mozarella --files=cod_doc/services/food_cost.py
  → returns: relevant tasks (status: in_progress), related docs, recent activity events
  → MCP-tools: task_search, doc_search, activity_list, commit_link_service.search_by_files
```

If an active task is found — the plugin offers the developer:
1. "This edit continues task COD-123 (in_progress). Continue?"
2. "This edit is not related to any open task. Create a new one?"

### 4.2. Post-commit hook: auto-documentation

After `git commit` the plugin:
1. Parses the commit message → looks for `COD-XXX` / `PCA-XXX` / `(#PR)`.
2. Via `commit_link_service` links SHA ↔ task.
3. If the commit touches `docs/**` — does not touch (docs are updated explicitly).
4. If only code is touched — generates a **draft** update of the related `task_doc` (via `task_doc_put`) and marks `proposed: true`.

### 4.3. Skill `cod-doc/pair-hacker/SKILL.md`

Activates when a vibecoder-agent (OpenCode / Claude Code) works in a project with active cod-doc. Contains:
- When to do `agent_pick` (before starting work).
- When to do `task_checkout` (before edit).
- When to do `agent_report` (if stuck).
- When to do `agent_complete` (after commit).
- The `commit_link` format (tag-pattern in commit message).

### 4.4. CLI command

```bash
cod-doc pair-hook install       # installs git hooks (pre-commit, post-commit, commit-msg)
cod-doc pair-hook status        # shows on which tasks the agent is currently working
cod-doc pair-hook sync          # pulls activity_log for the day → offers task_doc updates
cod-doc pair-hook checkout TASK-123  # atomic acquisition + notification of other agents
```

## Effect

| Metric | Before | After |
|---|---|---|
| Docs vs code drift | 2-3 weeks | <1 day (post-commit hook) |
| Time to "what did agent X do?" | 30+ minutes | 1 minute (`activity_for_run` + `commit_link_service`) |
| Onboarding a new vibecoder to a project | a day | 30 minutes (reads `task_summary` + related `task_doc`) |

## Structure

```
cod-doc-pair/                     # standalone Python package
├── pyproject.toml
├── src/cod_doc_pair/
│   ├── cli.py                    # click CLI (install/status/sync/checkout)
│   ├── mcp_client.py             # thin async MCP-client to cod-doc server
│   ├── hooks/
│   │   ├── pre_commit.py         # check in_progress tasks related to files
│   │   ├── post_commit.py        # commit_link + auto task_doc proposal
│   │   └── commit_msg.py         # tag-pattern validator (COD-XXX, PCA-XXX)
│   ├── integrations/
│   │   ├── opencode_hook.py      # adapter to OpenCode CLI hook API
│   │   ├── claude_code_hook.py   # adapter to Claude Code settings.json hooks
│   │   └── cursor_rule.py        # Cursor rules (.cursorrules)
│   └── skill_md/                 # → copied to cod_doc/skills/pair-hacker/SKILL.md
└── tests/
```

## Dependencies

| Proposal | Needed for |
|---|---|
| `06-atomic-checkout` (PCA-200) | race-protection between a pair of vibecoders in the same project |
| `09-activity-log` (PCA-912) | post-commit hook writes events |
| `OBI-010/011` (commit_link) | link SHA ↔ task |
| Cycle-5 agent profile (AGT-001..007) | `--profile minimal` surface for a quick start |

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| Post-commit hook creates noise in `task_doc` (offers irrelevant updates) | `proposed: true` + human-in-the-loop; PR review before merge |
| Git hooks slow down commit | Only async operations; pre-commit ≤ 200ms timeout |
| Different vibecoder tools have different hook APIs | `integrations/` module — one adapter per tool, common core logic |
| User works in a project without cod-doc init | `cod-doc pair-hook install` refuses with a clear error |

## Acceptance criteria (for the RFC task)

1. `cod-doc-pair` installs via `pip install cod-doc-pair` separately from cod-doc.
2. `cod-doc pair-hook install` installs 3 git hooks (pre-commit, post-commit, commit-msg).
3. After `git commit` with `COD-123` in the message — `commit_link_service` shows the link within 1 sec.
4. Pre-commit hook with an `in_progress` task on the stack — blocks commit, requiring ack.
5. Integration with OpenCode / Claude Code — via 1 settings file.
6. `SKILL.md pair-hacker` is auto-loaded by the agent on `agent_pick`.

## Roadmap

- **Phase 1 (1-2 weeks):** core + git hooks + commit_link.
- **Phase 2 (1 week):** OpenCode + Claude Code adapters.
- **Phase 3 (ongoing):** auto task_doc proposal (LLM), Cursor rules, IntelliJ plugin.

## Alternatives we did **not** choose

- **Doc-gen from code (Sphinx/MkStrings):** does not solve "what did agent X do" and does not link with tasks.
- **Just require the vibecoder to write docs:** does not work in practice (verified).
- **An AI agent reading git log manually:** works, but spends 1-2K tokens on each "what was here" — the snowball protocol already does better.

## Sources

- Real workflow: danil@Mozarella + lurkers-dev (3+ projects under vibecoding).
- Paperclip [`skills/`](https://github.com/paperclipai/paperclip/tree/master/skills) — the "trigger-activated skill" pattern.
- Cursor `.cursorrules`, Claude Code `settings.json` hooks — both support custom pre/post-action scripts.
