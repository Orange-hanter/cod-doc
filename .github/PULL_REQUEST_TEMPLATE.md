## 📝 Diff Summary
<!-- A brief description of the changes (1–3 sentences) -->


## 🎯 Why
<!-- Motivation: a link to a task (PCA-XXX / COD-XXX), an RFC (proposals/NN-…), or a business reason -->


## 🧪 How to verify
<!-- Steps for the reviewer: commands, expected output -->


## ⚠️ Risks
<!-- What can go wrong? What is covered by tests? -->


## 🤖 Model used
<!-- AI model / author. For example: claude-sonnet-4-6 | claude-opus-4-7 | human-authored | gpt-... -->


## 🔍 Validation Block
- [ ] Link hashes verified (`python tools/hash_calc.py update MASTER.md`)
- [ ] Self-check JSON attached (see below)
- [ ] Changelog in `MASTER.md` updated
- [ ] No fabricated artifacts — all links point to real files
- [ ] Section statuses are up to date (`🟡 DRAFT` / `🟢 VERIFIED` / `🔴 STALE`)

## 🧩 Affected sections
<!-- List the changed sections of MASTER.md -->
- 

## ✅ Definition of Done
<!-- See AGENTS.md §11 -->
- [ ] The behavior matches the acceptance criterion of the task or RFC
- [ ] `ruff`, `mypy`, `pytest` are green locally
- [ ] Contracts are synchronized (model ↔ migration ↔ MCP ↔ docs)
- [ ] If the change is visible in the UI — a screenshot / description is attached
- [ ] Activity events are emitted on write operations (if a new MCP-write-tool)
- [ ] Closing the task in the DB via `task_complete` or `task_update_status`
- [ ] If a plan section is closed — an audit-report in `docs/system/audit/`

## 📎 Self-Check JSON
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

## 💬 Instructions for the reviewer
- `✅ APPROVE` → merge into `main`, the `post-merge` hook runs automatically
- `⚠️ REQUEST CHANGES` → the agent parses the comments and pushes fixes to the same branch
