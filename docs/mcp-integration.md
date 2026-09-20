# COD-DOC: интеграция с LLM и Copilot

> Как подключить cod-doc к VS Code Copilot, Claude Desktop, Claude Code
> и другим LLM-системам для работы с документацией проектов.

---

## Обзор интерфейсов

cod-doc предоставляет 4 слоя доступа:

| Слой | Для кого | Когда использовать |
|------|----------|-------------------|
| CLI | Разработчик в терминале | Ручная работа, скрипты, CI/CD |
| TUI | Разработчик интерактивно | Первичная настройка, wizard |
| REST API | Внешние системы | Dashboards, CI, веб-интерфейсы |
| **MCP** | **LLM-клиенты** | **Copilot, Claude, агенты** |

MCP (Model Context Protocol) — стандартный протокол для подключения LLM
к внешним инструментам. cod-doc реализует MCP server с **144 инструментами**
(точная цифра валидируется тестом `tests/test_mcp_integration_doc.py`),
сгруппированных в 4 профиля.

---

## Agent profile — 6-tool curator surface (RFC 25 §3.2, по умолчанию)

> **RFC 25 (2026-09-15), своп выполнен планом `doc-curator-2026-09`**
> (CUR-007 — `ctx_search` с lazy reindex, CUR-008 — перекрой allowlist,
> CUR-016 — `ctx_docs` → `curator_next`: doc card вместо голого листинга).
> Дефолтный AI-агент — куратор документации и поиска, не исполнитель
> задач. `agent_capabilities()` отдаёт `role: "doc-curator"` и
> `forbidden: ["agent_pick", "task_checkout", "task_complete"]`.
> Coding-агент ходит на демон профиля `standard` (`:8801`), а не на
> `:8802` — профиль выбирается портом, не флагом клиента.

| Тул | Что делает |
|-----|------------|
| `agent_capabilities()` | L0 entry-point: server version, `role: "doc-curator"`, `forbidden`, доступные skills, валидные TaskStatus, default_project, `next_action_hint` → `ctx_drift` → `ctx_search`. <4KB. |
| `curator_next(project, limit?)` | **Doc card** (CUR-016): `{card{drift, links, master, findings}, priority[{kind, ref, reason, suggested_action}], navigation{applicable_skills (с ТЕЛАМИ), next_actions, success_criteria}, meta{generated_at, truncated, counts}}`. Порядок очереди: `missing` → `edited_in_place` → `LINK-BROKEN` → hash `BROKEN` → hash `STALE` → `stale_export` → finding. Read-only, идемпотентен. |
| `ctx_search(project, query, scope?, limit?)` | FTS-поиск по doc/adr/story/task: `{query, total, by_kind: {doc, adr, story, task: [...]}}`. Lazy reindex пустого индекса (CUR-007). |
| `ctx_drift(project)` | Дрейф markdown ↔ БД по всему проекту: `edited_in_place` / `stale_export` / `missing` (`= doc_drift_all`). |
| `context_get(project, target_kind, target_id, depth?)` | Snowball-пакет (L0/L1/L2) под token budget. |
| `agent_report(project, task_id, kind, message, agent_id?, payload?)` | Dispatcher. `kind ∈ {progress, blocker, approval_request, needs_context}` — эскалация человеку. |

### Жизненный цикл куратора (canonical)

```text
1. agent_capabilities()                    # роль, forbidden, skills, hint
2. curator_next(project)                   # doc card: очередь «за что браться»
3. ctx_drift(project) | ctx_search(project, query) | context_get(...)
   ↓ (починить документацию: import, hashes, links, body)
4a. self_check, зафиксировать в БД (doc import / hash update)   # готово
4b. agent_report(kind='approval_request', ...)                  # нужна политика человека
```

`agent_pick` / `agent_get` / `agent_complete` / `agent_release` (cycle-5
task-centric surface, таблица ниже) остаются зарегистрированы, но не входят
в allowlist профиля `agent` — видны только на `standard`/`full`, для
coding-агента.

### Task-centric тулы (standard/full — не agent)

| Тул | Что делает |
|-----|------------|
| `agent_pick(project, agent_id, plan_scope?)` | Атомарно: ready-set → checkout → assemble **task card** = `{task, context{plan, story, related_docs, siblings, affected_files, recent_history}, navigation{applicable_skills (с ТЕЛАМИ), next_actions, success_criteria, legal_status_transitions}}`. Идемпотентен. |
| `agent_get(project, task_id, what, ref?)` | Opt-in deep fetch. `what ∈ {full_doc_body, related_task, story_full, plan_export}`. |
| `agent_complete(project, task_id, agent_id, commit_sha?, summary?)` | Guarded done + release lock в одной транзакции. |
| `agent_release(project, task_id, agent_id, reason?)` | Drop lock без done; status → todo. |

Их жизненный цикл (coding-агент на `standard`/`full`) не изменился:

```text
1. agent_capabilities()              # кто я / какой профиль
2. agent_pick(project, agent_id)     # task + context + navigation card
   ↓ (выполнить работу)
3a. agent_complete(...)              # успех
3b. agent_report(kind='blocker',..)  # застрял
3c. agent_release(reason=...)        # отказ без done
```

### Запуск под agent-профилем

```bash
cod-doc-mcp                              # agent (default) — 6 curator tools
cod-doc-mcp --profile minimal            # 21 cold-start tools
cod-doc-mcp --profile standard           # 140 CRUD tools (без legacy)
cod-doc-mcp --profile full               # все 144 (включая legacy)
COD_DOC_PROFILE=full cod-doc-mcp         # через env
# CLI equivalent (ADO-079): same catalog filter
cod-doc mcp --profile standard
```

Всё перечисленное — запуск stdio. У постоянного демона профиль задаётся не
флагом клиента, а портом: `:8801` отдаёт `standard`, `:8802` — `agent`
(см. «Как cod-doc подаётся клиентам»). `cod-doc mcp` применяет
`--profile` / `COD_DOC_PROFILE` (по умолчанию `agent`) так же, как
`cod-doc-mcp` — это давно не нефильтрованный каталог.

Не подключайте клиентов через `docker exec … cod-doc mcp`: том контейнера —
другая БД, не та, что лежит в чекауте.

Про абсолютный путь к бинарю: это требование stdio-эпохи, когда процесс
резолвил проект от своего cwd. Демон так не делает — проект приходит
аргументом `project`, а его разрешение идёт только через реестр
`~/.cod-doc/config.yaml`. Проект, которого нет в реестре, демону недоступен,
даже если `.cod-doc/state.db` лежит рядом с чекаутом: `cod-doc project add`.

### Migration guide (cycle-3/4 → cycle-5 curator, RFC 25 §3.2)

Если ваша интеграция уже зовёт `task_checkout` / `context_get` /
`task_complete` напрямую — она продолжит работать под `--profile standard`
или `--profile full`. Никаких deprecation на самих CRUD-тулах нет.

Для **curator-интеграций** (документация/поиск, дефолтный профиль `agent`):

| Cycle-3/4 паттерн (6 calls) | Curator-эквивалент |
|---|---|
| `capabilities()` → `skill_list()` → `skill_get('orchestrator')` → `list_projects()` → ... | `agent_capabilities()` |
| `cod-doc ctx drift` / прямой `doc_drift_all` | `ctx_drift(project)` |
| `cod-doc ctx search` / прямой `search_service.search` | `ctx_search(project, query)` |
| `task_set_blocker()` + `task_update_status(blocked)` + `activity_emit()` | `agent_report(kind='blocker', message=...)` |

Для **coding-агента** (исполнение задач; профиль `standard`/`full`, не
`agent`) паттерн не изменился:

| Cycle-3/4 паттерн (6 calls) | Cycle-5 эквивалент (3 calls) |
|---|---|
| `task_next_ready()` → `task_checkout()` → `context_get('task',id)` → `skill_get('task-standard')` | `agent_pick(project, agent_id)` — только `standard`/`full` |
| `task_complete()` → `task_release()` → `activity_emit()` | `agent_complete(project, task_id, agent_id)` — только `standard`/`full` |

---

## Как cod-doc подаётся клиентам

Сервер — **один постоянный HTTP-демон на машину**, а не субпроцесс на сессию
(ADO-171). Раньше каждый клиент порождал свой stdio-процесс: на одной машине
их набиралось восемь, из двух разных сборок, а плагин и проектный `.mcp.json`
подключались одновременно и удваивали каталог.

| Демон | Адрес | Профиль | Тулов |
|---|---|---|---|
| `com.cod-doc.mcp` | `http://127.0.0.1:8801/mcp` | `standard` | 140 |
| `com.cod-doc.mcp-agent` | `http://127.0.0.1:8802/mcp` | `agent` | 6 |

Установка, апгрейд и управление — `deploy/launchd/cod-doc-services.sh`
(`upgrade | install | restart | status | version | rollback | uninstall | render`;
тем же скриптом живёт и веб-сервис `com.cod-doc.web`), подробности —
[`deploy/launchd/README.md`](../deploy/launchd/README.md). Демон работает
поверх пиннованной non-editable сборки в `~/.cod-doc/runtime`: editable-инстал
рабочего дерева означал, что любая правка или незавершённый ребейз мгновенно
уезжают во все харнессы машины сразу.

Два следствия, ломающих привычки stdio-эпохи:

- **Профиль выбирается портом, а не флагом клиента.** `apply_profile`
  удаляет записи из уже зарегистрированного каталога, поэтому один процесс
  отдаёт ровно один профиль. `--profile` в клиентский конфиг больше не пишется.
- **`project` обязателен в каждом DB-туле.** Под stdio процесс был равен
  сессии, и `set_default_project` хранил дефолт в памяти процесса. У общего
  демона этого равенства нет — `stateless_http` создаёт транспорт на запрос,
  но состояние модуля общее. Поэтому дефолта нет вовсе: `set_default_project`
  отказывает, а вызов без `project` возвращает внятную ошибку вместо тихой
  работы не с той БД. `capabilities()` показывает это в
  `session.shared_server`. См. `cod_doc/mcp/tools/_workspace.py`.

---

## Регистрация: одна на харнесс, не на проект

Блок одинаковый везде:

```json
{ "type": "http", "url": "http://127.0.0.1:8801/mcp" }
```

| Харнесс | Область | Как |
|---|---|---|
| Claude Code | user scope | `claude mcp add --transport http cod-doc http://127.0.0.1:8801/mcp -s user` |
| Cursor | глобально | блок выше в `~/.cursor/mcp.json` |
| Claude Desktop | глобально | Settings → Developer → Edit Config |
| VS Code Copilot | по проекту | `.vscode/mcp.json`, корневой ключ `servers`, а не `mcpServers` |

Проектный `.mcp.json` **в дополнение** к глобальному конфигу — это дубль:
клиент подключится дважды и каталог удвоится. Ровно так и возникли 224 тула
вместо 112 до ADO-171. Проверка, что регистрация одна:

```bash
claude mcp list        # ожидается ровно одна строка cod-doc, транспорт HTTP
```

### Что можно спрашивать

После подключения доступна вся MCP-поверхность. Примеры:

- «Покажи статус проекта weather-cli»
- «Какие задачи не закрыты?»
- «Есть ли устаревшие ссылки в MASTER.md?»
- «Добавь задачу: написать документацию для модуля auth»
- «Найди в документации всё про обработку ошибок»

Клиент сам выбирает инструменты и вызывает их — но слаг проекта нужно
называть явно, иначе тул вернёт ошибку о том, что `project` обязателен.

### Дополнение: copilot-instructions.md

Для лучшей работы Copilot создайте `.github/copilot-instructions.md`:

```markdown
## Документация проекта

Этот проект документирован через cod-doc.
- Навигатор документации: MASTER.md (читай его первым)
- Структура: specs/ (требования), arch/ (архитектура), docs/ (прочее)
- Поиск по докам — MCP-тул `ctx_search`; слаг проекта передавай явно
- Перед изменением доков — проверь дрейф через `doc_drift`
```

Это даёт Copilot контекст об организации документации даже без MCP.

---

## stdio — когда он всё-таки нужен

Демон покрывает обычную работу, но stdio остался и не изменился: процесс на
сессию, дефолтный проект через `set_default_project` работает как раньше.
Он нужен, когда демона нет — в контейнере, на чужой машине, в одноразовом
окружении CI:

```json
{
  "mcpServers": {
    "cod-doc": {
      "type": "stdio",
      "command": "cod-doc-mcp",
      "args": ["--profile", "standard"]
    }
  }
}
```

Через контейнер:

```json
{ "type": "stdio", "command": "docker", "args": ["exec", "-i", "cod-doc", "cod-doc", "mcp"] }
```

Профиль сервера по умолчанию — `agent` (6 curator-тулов: `agent_capabilities`,
`curator_next`, `ctx_search`, `ctx_drift`, `context_get`, `agent_report`), а не
`standard`, поэтому coding-агенту под stdio его указывают явно
(`--profile standard`). Поднять HTTP-эндпоинт вручную, без launchd:

```bash
cod-doc mcp --transport streamable-http --host 127.0.0.1 --port 8801
```

Наружу порт не выставлять: аутентификации у MCP-эндпоинта нет, единственный
контроль — bind на loopback.

---

## REST API (без MCP)

Для систем, не поддерживающих MCP:

```bash
cod-doc serve  # → http://localhost:8765
```

Доступные endpoints:
```
GET  /api/config
GET  /api/projects
POST /api/projects
GET  /api/projects/{name}/status
GET  /api/projects/{name}/tasks
POST /api/projects/{name}/tasks
WS   /ws/projects/{name}/run     # запуск агента через WebSocket
```

### Пример: GitHub Actions

```yaml
- name: Check documentation freshness
  run: |
    cod-doc serve &
    sleep 2
    STATUS=$(curl -s http://localhost:8765/api/projects/myproject/status)
    STALE=$(echo $STATUS | jq '.stale_refs')
    if [ "$STALE" -gt 0 ]; then
      echo "::warning::Documentation has $STALE stale references"
    fi
```

---

## Только MASTER.md (без сервера)

Даже без запущенного MCP-сервера, MASTER.md полезен для LLM:

1. **Copilot instructions** → указать "читай MASTER.md первым"
2. **Контекстное окно** → скопировать MASTER.md в чат с любым LLM
3. **@workspace в Copilot** → Copilot найдёт MASTER.md через поиск по файлам

MASTER.md v0.2 спроектирован как двухслойный:
- Верхняя часть — таблицы, диаграммы, чеклист (понятно человеку)
- `<details>` блок — метаданные, хэши, протоколы (понятно LLM)

LLM может разобрать MASTER.md и выстроить карту проекта даже без MCP.

---

## Каталог MCP-инструментов

> Источник истины — `tools/list` MCP-клиента и `skill_list` для гайдов.
> Эта таблица — навигатор «что в каком семействе» на текущий релиз.
> Числа сверяются с реальным каталогом через
> [`tests/test_mcp_integration_doc.py`](../tests/test_mcp_integration_doc.py).

| Семейство | Кол-во | Назначение | Ключевые тулы |
|-----------|-------:|------------|---------------|
| **doc.\*** | 12 | DB-backed документы | `doc_list`, `doc_body`, `doc_create`, `doc_rename`, `doc_add_section`, `doc_patch_section`, `doc_export`, `doc_drift`, `doc_drift_all`, `doc_get`, `doc_accept`, `doc_backfill_projection` |
| **task.\*** | 18 | DB-backed задачи (lifecycle) | `task_create`, `task_create_many`, `task_get`, `task_list`, `task_next_ready`, `task_update_status`, `task_update`, `task_move_to_section`, `task_complete`, `task_set_blocker`, `task_find_duplicate`, `task_log_progress`, … |
| **task_doc.\*** | 5 | Артефакты, связанные с задачей | `task_doc_put`, `task_doc_get`, `task_doc_list`, `task_doc_revisions`, `task_doc_revert` |
| **task_checkout / task_release** | 2 | Атомарный захват задачи (PCA-200) | `task_checkout`, `task_release` |
| **plan.\*** | 11 | Планы исполнения и графы зависимостей | `plan_create`, `plan_freeze`, `plan_section_create`, `plan_sections_list`, `plan_ready`, `plan_progress`, `plan_critical_path`, `plan_forward_chain`, `plan_reverse_chain`, `plan_audit`, `plan_export` |
| **story.\*** | 11 | User stories + acceptance criteria + секции | `story_create`, `story_list`, `story_get`, `story_link`, `story_add_criterion`, `story_set_criterion_met`, `story_update_status`, `story_coverage`, `story_section_create`, `story_section_list`, `story_set_section` |
| **link.\*** | 4 | Гибридные ссылки между документами | `link_list`, `link_sync`, `link_verify`, `link_suggest_for_section` |
| **revision.\*** | 3 | История изменений сущностей | `revision_list`, `revision_get`, `revision_revert` |
| **run.\*** | 1 | Инспекция прогонов встроенного оркестратора (ADR-012) | `run_get` |
| **approval.\*** | 5 | Human-in-the-loop одобрения | `approval_request`, `approval_list`, `approval_get`, `approval_resolve`, `approval_cancel` |
| **activity.\*** | 1 | Единый audit-таймлайн | `activity_list` |
| **routine.\*** | 7 | Cron-style health checks | `routine_create`, `routine_list`, `routine_get`, `routine_update_status`, `routine_delete`, `routine_run_now`, `routine_history` |
| **skill.\*** | 2 | Каталог skill-инструкций для агента | `skill_list`, `skill_get` |
| **agent.\* (cycle-5)** | 6 | Cycle-5 task-flow surface. На профиле `agent` (RFC 25 §3.2) в allowlist остались только `agent_capabilities`/`agent_report`, курс задают `ctx_*`/`context_get` из семейства ниже; task-centric `agent_pick`/`agent_get`/`agent_complete`/`agent_release` видны на `standard`/`full` | `agent_capabilities`, `agent_pick`, `agent_get`, `agent_report`, `agent_complete`, `agent_release` |
| **adr.\* (ADR-002)** | 10 | Architecture Decision Records: CRUD + supersede DAG + task links + Mermaid diagrams + deprecate + projection sync | `adr_create`, `adr_get`, `adr_list`, `adr_update`, `adr_sync_body`, `adr_add_diagram`, `adr_supersede`, `adr_deprecate`, `adr_link_task`, `adr_graph` |
| **context / capabilities / session** | 9 | Admin: snowball-сборка контекста, L0 bootstrap, tool discovery + per-tool describe, change-log, safe-call envelope, workspace defaults | `context_get`, `capabilities`, `tool_search`, `tool_describe`, `tools_diff`, `tool_call_safe`, `set_default_project`, `get_default_project`, `clear_default_project` |
| **check_config** | 1 | Самодиагностика сервера | `check_config` |
| **Legacy (YAML агент)** | 3 | Остаток legacy-surface после STB-002 (2026-06-08): resume-вход + context-хелперы. YAML CRUD (проекты/задачи/MASTER/поиск + hash/verify) удалён — БД источник истины. | `run_agent_once`, `get_agent_context`, `clear_agent_context` |
| **finding.\* (RFC 22)** | 4 | Внешние находки (ai-review / ZAIrgRush / routines): triage и промоушен в задачи. Только профили standard/full | `finding_list`, `finding_get`, `finding_promote`, `finding_dismiss` |
| **ctx.\* (RFC 22 / RFC 25 §3.2)** | 4 | Контекст для внешних потребителей: `ctx_docs` = `doc_list`, `ctx_search` = `search_service.search` с lazy reindex пустого FTS-индекса (CUR-007) и необязательным `projects=[слаг, …]` — кросс-проектный поиск в пределах одной (hub) БД, чужой `db_url` = ошибка (CUR-013), `ctx_drift` = `doc_drift_all` (SYM-006D); `ctx_search` и `ctx_drift` входят в дефолтный профиль `agent`, а `ctx_docs` с CUR-016 уступил там место `curator_next`. `ctx_drift_gate` — детерминированный гейт документации по файлам PR с идемпотентным PR-комментарием (SYM-010), только standard/full | `ctx_docs`, `ctx_search`, `ctx_drift`, `ctx_drift_gate` |
| **curator.\* (RFC 25 §3.5)** | 1 | Doc card куратора (CUR-016): дрейф проекции, нерезолвящиеся ссылки, протухшие записи реестра хэшей MASTER.md и открытые findings — одной очередью с готовой командой на каждый пункт. Входит в дефолтный профиль `agent`; зеркало в CLI — `cod-doc ctx next` | `curator_next` |
| **scenario.\* (RFC 24 §9)** | 9 | Сценарии тестирования: авторская половина RFC 24 — что должно быть верно (вид, предусловия, шаги, ожидаемый результат, якорь в capability-документе) и проекция в `docs/system/scenarios/`. Вердикты покрытия сюда не попадают: это доказательства producer'а (STR-002). Только профили standard/full | `scenario_create`, `scenario_get`, `scenario_list`, `scenario_update`, `scenario_retire`, `scenario_set_steps`, `scenario_link`, `scenario_export`, `scenario_coverage` |
| **structure.\*** | 5 | Pinned code-structure snapshots, drift, scenarios and BFS context (not projection drift; not ai_review findings) | `structure_get`, `structure_context`, `structure_drift`, `structure_scenarios`, `structure_diff` |
| **doc_tree.\* / doc_node.\* (ADO-116)** | 8 | Дерево документации как данные: разделы с намерением и порядком, детерминированная раскладка по правилам и Инбокс для того, что правилам не подошло. `doc_tree_classify` по умолчанию `dry_run=true` и не трогает то, что человек разложил руками. Только профили standard/full | `doc_tree_get`, `doc_tree_unplaced`, `doc_tree_init`, `doc_tree_classify`, `doc_set_node`, `doc_node_create`, `doc_node_update`, `doc_node_delete` |
| **doc_node_health.\*** | 2 | Пробелы в наполненности разделов: пусто, ниже `min_docs`, без `intent`, вырожденная типизация корпуса, пачка безымянных индексов. Дерево отвечает «где лежит», это — «чего не написано». `doc_node_health_sync` пишет находки в общую таблицу `finding`, поэтому пробел виден `curator_next` и промоутится в задачу. Детерминированно, без LLM. Только профили standard/full | `doc_node_health_get`, `doc_node_health_sync` |
| **ИТОГО** | **144** | | |

Legacy-семейство дублирует часть DB-поверхности (например `add_task` ↔
`task_create`, `list_tasks` ↔ `task_list`) и помечено `DEPRECATED` в
docstring соответствующих тулов. Для новых интеграций — игнорируй legacy
и опирайся на DB-поверхность; будущий профиль `--profile standard` (PCA-951)
скроет legacy полностью.

## MCP Resources

| URI | Тип | Описание |
|-----|-----|---------|
| `cod-doc://config` | Статический | Текущая конфигурация |
| `cod-doc://projects` | Статический | Список проектов |
| `cod-doc://project/{name}/master` | Шаблон | MASTER.md проекта |
| `cod-doc://project/{name}/tasks` | Шаблон | Задачи проекта |

## MCP Prompts

| Промпт | Назначение | Параметры |
|--------|-----------|-----------|
| `doc_review` | Ревью документации | `project_name`, `focus?` |
| `doc_plan` | План документирования | `project_name` |
| `onboard_project` | Онбординг нового проекта | `project_name` |

---

## Сравнение вариантов

| Критерий | MCP (HTTP-демон) | MCP (stdio) | REST API | Только MASTER.md |
|----------|------------------|-------------|----------|-------------------|
| Процессов на машине | 1 на профиль | 1 на клиентскую сессию | 1 | 0 |
| Настройка | Один раз, глобально | В каждом клиенте | Простая | Никакой |
| Copilot Chat | ✅ | ✅ | ❌ | Частично |
| Claude Desktop | ✅ | ✅ | ❌ | Через copy-paste |
| CI/CD | ✅ | ❌ | ✅ | ❌ |
| Кол-во инструментов | 144 | 144 | ~8 | 0 |
| Семантический поиск | ✅ | ✅ | ❌ | ❌ |
| `project` в вызове | обязателен | можно через дефолт | — | — |
| Дефолтный проект | нет (общий процесс) | есть (процесс = сессия) | — | — |

---

## Рекомендации

**По умолчанию:** HTTP-демон, одна регистрация на харнесс. Это единственный
вариант, при котором число процессов не растёт с числом открытых окон, а
каталог тулов не удваивается от случайного второго конфига.

**stdio:** когда демона нет и поднимать его некуда — контейнер, чужая машина,
одноразовое окружение CI.

**REST API:** для систем без поддержки MCP. Проверка свежести документации,
автосоздание задач при обнаружении stale refs.

**Только MASTER.md:** нулевая настройка, Copilot находит файл через
@workspace. Годится для быстрого старта и чужих репозиториев.

## Как я предлагаю учиться дальше

Хороший следующий цикл обучения:

1. Я показываю тебе живой smoke test MCP-клиентом.
2. Потом мы вместе добавляем ещё один tool.
3. Потом ты сам формулируешь, чего не хватает документационному workflow.
4. После этого уже решаем, оставлять ли нативный MCP server или делать bridge поверх REST.
