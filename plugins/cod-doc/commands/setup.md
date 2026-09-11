---
description: Connect the current repository to cod-doc — project init, registry, documentation import, an MCP check
argument-hint: "[project slug]"
---

Connect the current repository to cod-doc. Slug: `$1`, if not set — suggest
the repository's catalog name and confirm it with the user before writing.

**0. Check the binary.** `cod-doc --version` (or `cod-doc --help`). Not in
PATH — look for `.venv/bin/cod-doc` in the repository; not there either — install
(`pip install -e '.[dev]'` in the cod-doc checkout) and stop, there is nothing to
go on with.

**1. Already connected?** If `.cod-doc/state.db` exists — do not reinitialize
(`project init` overwrites the state). Show what is already there
(`sqlite3 -readonly .cod-doc/state.db "select slug, root_path from project"`),
and go to step 4.

**2. Initialization.**

```
cod-doc project init <slug>          # .cod-doc/, state.db with the schema, a project record
cod-doc project add . -n <slug>      # registration in ~/.cod-doc/config.yaml
```

**3. Documentation import** — first dry, to see the scope:

```
cod-doc import docs -p <slug> --dry-run --limit 0
cod-doc import docs -p <slug> --exclude '*/node_modules' --exclude '*/.venv'
```

Show the import plan to the user and **wait for confirmation** before running
without `--dry-run`: the import writes hundreds of documents to the DB, rolling it back is more expensive
than agreeing on it.

**4. Repository hygiene.** Make sure `.cod-doc/` is in `.gitignore` (the DB is
local state, not a repository artifact), and warn if it is not.

**5. MCP host config.** `cod-doc connect install --client cursor` (or
`claude-code` / `all`), then `cod-doc connect doctor`. The plugin does
not spawn MCP for Cursor; working tools show up as `user-cod-doc`.
If MCP is already up — poke `task_summary` / `agent_capabilities` to
make sure the server sees the same project.

Report the result as a checklist: what was created, how many documents imported, which
slug to use in the `/cod-doc:*` commands next.
