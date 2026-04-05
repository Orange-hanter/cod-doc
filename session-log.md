# COD-DOC Session Log

> Дата: 2025-04-05  
> Формат: asciinema-style текстовый лог  
> Цель: исправление TUI wizard, добавление debug-логирования, верификация

---

## 1. Диагностика: Static.Focus crash

**Проблема:** Wizard TUI падал при запуске из-за несовместимости с текущей версией Textual.

```
$ cd /Users/dakh/Git/cod-doc
$ .venv/bin/cod-doc wizard
Traceback (most recent call last):
  ...
  File "cod_doc/tui/screens/dashboard.py", line ...
    @on(Static.Focus)
AttributeError: type object 'Static' has no attribute 'Focus'
```

**Исправление:** `Static.Focus` → `Static.focus` (строчная `f`) + обобщённая сигнатура обработчика.

```diff
- @on(Static.Focus)
- def _on_card_focus(self, event: Static.Focus) -> None:
+ @on(Static.focus)
+ def _on_card_focus(self, event) -> None:
+     if isinstance(getattr(event, 'control', None), ProjectCard):
```

---

## 2. Добавление debug-логирования в TUI

### 2.1 app.py — CodDocApp

```
$ grep -n "debug" cod_doc/tui/app.py
# Добавлен параметр debug_log_file в конструктор
# Метод _configure_tui_debug_logger() создаёт FileHandler для namespace cod_doc.tui
# Debug-события: mount, screen selection
```

### 2.2 wizard.py — WizardScreen

```
# Debug-логи на каждом шаге:
#   mount, step transitions, validation failures, save actions, finish
```

### 2.3 dashboard.py — DashboardScreen

```
# Debug-логи: mount, reload projects, select project
```

### 2.4 agent_run.py — AgentRunScreen

```
# Debug-логи: mount, button presses, start/stop agent, events, errors, finish
# Исправлено: переменная log -> rich_log в методе _log() чтобы не затенять модульный logger
```

---

## 3. CLI: --debug-log-file и --text fallback

### 3.1 Добавлены опции

```
$ .venv/bin/cod-doc wizard --help
Usage: cod-doc wizard [OPTIONS]

  Запустить мастер настройки.

Options:
  --debug-log-file TEXT  Путь к файлу debug-лога wizard
  --text                 Запустить текстовый wizard без TUI
  --help                 Show this message and exit.
```

### 3.2 Авто-fallback при краше TUI

```python
# cli.py — wizard command
try:
    app.run()
except Exception:
    log.exception("Wizard launch failed")
    console.print("[yellow]Переключаюсь на текстовый wizard.[/yellow]")
    _run_text_wizard(cfg)
```

---

## 4. Верификация: тесты

```
$ cd /Users/dakh/Git/cod-doc
$ .venv/bin/python -m pytest tests/ -q --tb=short
.................................................  [100%]
49 passed in 1.35s
```

---

## 5. Верификация: text wizard

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
Настройка через обычный терминал без TUI.

OpenRouter API key: LLM model [anthropic/claude-sonnet-4-6]: Base URL [https://openrouter.ai/api/v1]:
Path to first project [/Users/dakh/Git/cod-doc]: Project name: Path to MASTER.md [MASTER.md]:
12:14:35 DEBUG    cli: Text wizard saved API config
12:14:35 DEBUG    cli [project=test-wizard-proj]: Text wizard initialized project
✅ Настройка завершена. Проект 'test-wizard-proj' добавлен.
```

### Валидация ошибки (несуществующий каталог)

```
$ .venv/bin/cod-doc --log-level DEBUG wizard --text <<EOF
test-key-123
anthropic/claude-sonnet-4-6
https://openrouter.ai/api/v1
/tmp/nonexistent-dir
test-proj
MASTER.md
EOF

Error: Директория не найдена: /private/tmp/nonexistent-dir
EXIT: 1
```

---

## 6. Верификация: TUI wizard

```
$ .venv/bin/cod-doc wizard --debug-log-file /tmp/wizard-debug.log
# TUI запустился корректно (alternate buffer)

$ cat /tmp/wizard-debug.log
2026-04-05 15:14:42,649 DEBUG cod_doc.tui.app: TUI debug logging enabled
```

---

## 7. Верификация: project list и cleanup

```
$ .venv/bin/cod-doc project list
┌──────────────────┬────────────────────────────┬───────────┬─────────┬─────────────┐
│ Имя              │ Путь                       │ MASTER.md │ Статус  │ Задачи      │
├──────────────────┼────────────────────────────┼───────────┼─────────┼─────────────┤
│ integration-test │ /private/var/folders/...    │ ✅        │ 🟢 idle │ 🟡1 🟢0 🔴0 │
│ test-wizard-proj │ /private/tmp/test-wiz...   │ ✅        │ 🟢 idle │ 🟡0 🟢0 🔴0 │
└──────────────────┴────────────────────────────┴───────────┴─────────┴─────────────┘

$ .venv/bin/cod-doc project remove test-wizard-proj
Проект 'test-wizard-proj' удалён из реестра.
```

---

## 8. Верификация: import agent_run после fix

```
$ .venv/bin/python -c "from cod_doc.tui.screens.agent_run import AgentRunScreen; print('OK')"
agent_run import OK
```

---

## 9. Исправление: log shadowing в agent_run.py

**Проблема:** Метод `_log()` объявлял локальную переменную `log` (RichLog widget), затеняя модульную переменную `log` (Logger).

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

## Итог

| Задача | Статус |
|--------|--------|
| Fix Static.Focus → Static.focus | ✅ |
| Debug logging: app, wizard, dashboard, agent_run | ✅ |
| CLI --debug-log-file option | ✅ |
| CLI --text wizard fallback | ✅ |
| Auto-fallback TUI → text | ✅ |
| Fix log shadowing in agent_run.py | ✅ |
| Text wizard: работает | ✅ |
| TUI wizard: запускается | ✅ |
| 49/49 тестов: pass | ✅ |
