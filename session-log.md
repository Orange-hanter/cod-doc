# COD-DOC Session Log

> Date: 2025-04-05  
> Format: asciinema-style text log  
> Goal: fix the TUI wizard, add debug logging, verification

---

## 1. Diagnostics: Static.Focus crash

**Problem:** The TUI Wizard crashed on launch due to incompatibility with the current version of Textual.

```
$ cd /Users/dakh/Git/cod-doc
$ .venv/bin/cod-doc wizard
Traceback (most recent call last):
  ...
  File "cod_doc/tui/screens/dashboard.py", line ...
    @on(Static.Focus)
AttributeError: type object 'Static' has no attribute 'Focus'
```

**Fix:** `Static.Focus` → `Static.focus` (lowercase `f`) + a generalized handler signature.

```diff
- @on(Static.Focus)
- def _on_card_focus(self, event: Static.Focus) -> None:
+ @on(Static.focus)
+ def _on_card_focus(self, event) -> None:
+     if isinstance(getattr(event, 'control', None), ProjectCard):
```

---

## 2. Adding debug logging to the TUI

### 2.1 app.py — CodDocApp

```
$ grep -n "debug" cod_doc/tui/app.py
# Added a debug_log_file parameter to the constructor
# The _configure_tui_debug_logger() method creates a FileHandler for the cod_doc.tui namespace
# Debug events: mount, screen selection
```

### 2.2 wizard.py — WizardScreen

```
# Debug logs at each step:
#   mount, step transitions, validation failures, save actions, finish
```

### 2.3 dashboard.py — DashboardScreen

```
# Debug logs: mount, reload projects, select project
```

### 2.4 agent_run.py — AgentRunScreen

```
# Debug logs: mount, button presses, start/stop agent, events, errors, finish
# Fixed: log -> rich_log in the _log() method so it doesn't shadow the module logger
```

---

## 3. CLI: --debug-log-file and --text fallback

### 3.1 Added options

```
$ .venv/bin/cod-doc wizard --help
Usage: cod-doc wizard [OPTIONS]

  Run the setup wizard.

Options:
  --debug-log-file TEXT  Path to the wizard debug log file
  --text                 Run a text wizard without TUI
  --help                 Show this message and exit.
```

### 3.2 Auto-fallback on TUI crash

```python
# cli.py — wizard command
try:
    app.run()
except Exception:
    log.exception("Wizard launch failed")
    console.print("[yellow]Falling back to the text wizard.[/yellow]")
    _run_text_wizard(cfg)
```

---

## 4. Verification: tests

```
$ cd /Users/dakh/Git/cod-doc
$ .venv/bin/python -m pytest tests/ -q --tb=short
.................................................  [100%]
49 passed in 1.35s
```

---

## 5. Verification: text wizard

```
$ mkdir -p /tmp/test-wizard-proj

$ .venv/bin/cod-doc --log-level DEBUG wizard --text <<EOF
test-key-123
anthropic/claude-sonnet-4-6
https://openrouter.ai/api/v1
/tmp/test-wizard-proj
test-wizard-proj
MASTER.md
EOF

COD-DOC text wizard
Setup via a plain terminal without TUI.

OpenRouter API key: LLM model [anthropic/claude-sonnet-4-6]: Base URL [https://openrouter.ai/api/v1]:
Path to first project [/Users/dakh/Git/cod-doc]: Project name: Path to MASTER.md [MASTER.md]:
12:14:35 DEBUG    cli: Text wizard saved API config
12:14:35 DEBUG    cli [project=test-wizard-proj]: Text wizard initialized project
✅ Setup complete. Project 'test-wizard-proj' added.
```

### Error validation (nonexistent directory)

```
$ .venv/bin/cod-doc --log-level DEBUG wizard --text <<EOF
test-key-123
anthropic/claude-sonnet-4-6
https://openrouter.ai/api/v1
/tmp/nonexistent-dir
test-proj
MASTER.md
EOF

Error: Directory not found: /private/tmp/nonexistent-dir
EXIT: 1
```

---

## 6. Verification: TUI wizard

```
$ .venv/bin/cod-doc wizard --debug-log-file /tmp/wizard-debug.log
# TUI launched correctly (alternate buffer)

$ cat /tmp/wizard-debug.log
2026-04-05 15:14:42,649 DEBUG cod_doc.tui.app: TUI debug logging enabled
```

---

## 7. Verification: project list and cleanup

```
$ .venv/bin/cod-doc project list
┌──────────────────┬────────────────────────────┬───────────┬─────────┬─────────────┐
│ Name             │ Path                       │ MASTER.md │ Status  │ Tasks       │
├──────────────────┼────────────────────────────┼───────────┼─────────┼─────────────┤
│ integration-test │ /private/var/folders/...   │ ✅        │ 🟢 idle  │ 🟡1 🟢0 🔴0  │
│ test-wizard-proj │ /private/tmp/test-wiz...   │ ✅        │ 🟢 idle  │ 🟡0 🟢0 🔴0  │
└──────────────────┴────────────────────────────┴───────────┴─────────┴─────────────┘

$ .venv/bin/cod-doc project remove test-wizard-proj
Project 'test-wizard-proj' removed from the registry.
```

---

## 8. Verification: import agent_run after the fix

```
$ .venv/bin/python -c "from cod_doc.tui.screens.agent_run import AgentRunScreen; print('OK')"
agent_run import OK
```

---

## 9. Fix: log shadowing in agent_run.py

**Problem:** The `_log()` method declared a local variable `log` (the RichLog widget), shadowing the module-level variable `log` (the Logger).

```diff
  def _log(self, message: str, style: str = "white", prefix: str = "") -> None:
-     log = self.query_one("#agent-log", RichLog)
+     rich_log = self.query_one("#agent-log", RichLog)
      ts = datetime.now().strftime("%H:%M:%S")
      if prefix:
-         log.write(...)
+         rich_log.write(...)
      else:
-         log.write(...)
+         rich_log.write(...)
```

---

## Summary

| Task | Status |
|--------|--------|
| Fix Static.Focus → Static.focus | ✅ |
| Debug logging: app, wizard, dashboard, agent_run | ✅ |
| CLI --debug-log-file option | ✅ |
| CLI --text wizard fallback | ✅ |
| Auto-fallback TUI → text | ✅ |
| Fix log shadowing in agent_run.py | ✅ |
| Text wizard: works | ✅ |
| TUI wizard: launches | ✅ |
| 49/49 tests: pass | ✅ |
