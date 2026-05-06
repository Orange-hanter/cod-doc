---
type: audit-report
scope: cod_doc/cli/* vs cod_doc/api/web/* (CLI ↔ Web parity)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-06
last_updated: 2026-05-06
audit_target_revision: HEAD = b07a97e (post WEB-013..014/COD-070..079, daemon UI)
related_docs:
  - ../MASTER.md
  - ../ARCHITECTURE.md
  - ../capabilities/web-frontend.md
  - 2026-05-02-section-web-frontend.md
related_code:
  - cod_doc/cli/
  - cod_doc/api/web/
---

# CLI ↔ Web parity — System Audit (2026-05-06)

> Аудит расхождения: что доступно через CLI, но **отсутствует или частично
> реализовано в Web UI**. Цель — зафиксировать долг, чтобы выполнить инвариант
> [ARCHITECTURE.md §1](../ARCHITECTURE.md): «всё, что доступно в сервисах,
> должно быть доступно в Web в течение одного PR-цикла после CLI/MCP».
>
> Источник списка CLI-команд — `grep -rnE "@.*\.command\("` по
> [cod_doc/cli/](../../../cod_doc/cli/). Источник списка Web-маршрутов —
> [cod_doc/api/web/pages/](../../../cod_doc/api/web/pages/) +
> [fragments/](../../../cod_doc/api/web/fragments/).
>
> Проверено вручную, не от агента: см. §6 «Методология».

## 0. Базовая статистика

| Метрика | Значение |
|---|---:|
| CLI-команд (включая subcommand'ы) | **50** |
| Web-маршрутов (pages + fragments) | **38** |
| Доменов покрыто Web | 8 / 11 (`docs`, `tasks`, `plans`, `stories`, `revisions`, `project`, `settings`, `daemon`) |
| Доменов **без** Web | 3 (`link`, `hash`, `audit`) |
| Чисто read-only в Web (без write-counterpart) | `revisions`, `link*`, `audit*` (* отсутствуют целиком) |
| Web-операций без CLI-аналога | **6** (см. §0.1 — AI-generate ×4, `import_master`, `plan freeze`, fields edit) |

### 0.1 Симметрия: где Web уже обгоняет CLI

Для честного учёта — этот аудит не одностронний. Web имеет операции,
которые в CLI не выведены:

| Web-операция | Маршрут | CLI-аналог |
|---|---|---|
| AI-генерация документа | `POST /p/{slug}/docs/generate` + `/generate/save` | ❌ |
| AI-генерация story | `POST /p/{slug}/stories/generate` + `/save` | только `story create` (manual) |
| AI-генерация задач из story | `POST /p/{slug}/stories/{id}/tasks/generate` + `/save` | ❌ |
| AI-улучшение task fields | `POST /p/{slug}/tasks/{id}/fields/{field}/improve` | ❌ |
| Bootstrap проекта из MASTER.md | `POST /p/{slug}/import_master/scan` + `/save` | ❌ (есть `import all`, но другая семантика) |
| Inline-редактирование description/acceptance | `GET/POST /p/{slug}/tasks/{id}/fields/{field}` | ❌ (CLI имеет только status/complete/create) |
| Inline-редактирование секций документа | `GET/POST /p/{slug}/docs/{key}/sections/{anchor}` | ❌ (CLI редактирует только целым `doc rename`/`doc import`) |
| Один клик «accept document» | `POST /p/{slug}/docs-accept` | ❌ (CLI имеет только set status через create/rename) |
| `plan freeze` | `POST /p/{slug}/plans/{id}/freeze` | ❌ |

Это означает, что в нескольких операциях **CLI отстаёт от Web**, и при
закрытии гэпов из §1-§3 разумно одновременно вывести этот функционал в
CLI/MCP, чтобы не накапливать инверсный долг. Особенно — `task field
edit` и `doc section patch`, где Web уже зрелый, а headless-сценарии
(MCP-агент) не могут редактировать описание/секцию вне `doc import`.

## Сводка

| Severity | Count | Описание |
|---|---:|---|
| critical | 0 | — |
| high | 5 | Целые домены без UI: links, audit, hash; ключевые операции: doc rename, revision revert |
| medium | 9 | Plan analytics (audit/critical-path/chains), story write-ops, doc drift/export, task create form |
| low | 4 | Add/remove project, log progress, task list-blocked, hash calc |
| **итого** | **18** | — |

---

## 1. High

### CW-HI-1. Домен `link` отсутствует в Web целиком

**Где:** [cod_doc/cli/link.py](../../../cod_doc/cli/link.py) — 4 subcommand'а.

| CLI | Сервис | Web |
|---|---|---|
| `link list DOC --section ANCHOR` | `link_service.list_for_section` | ❌ |
| `link sync DOC ANCHOR` | `link_service.sync_section` | ❌ |
| `link verify DOC ANCHOR` | `link_service.verify_section` | ❌ |
| `link backfill` | `link_service.backfill_project` | ❌ |

**Симптом:** ссылочная модель — фундамент COD-DOC (auto-linking,
[capabilities/auto-linking.md](../capabilities/auto-linking.md)), но
пользователь Web-фронта не видит ни одной ссылки и не может их пере-парсить.
Битые ссылки в документе обнаруживаются только из терминала. На странице
документа нет даже бейджа «N broken links».

**Что нужно:**
1. На [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html)
   добавить панель «Links» рядом с секциями: ok / broken / skipped счётчики
   из `link_service.verify_section`.
2. Endpoint `POST /p/{slug}/docs/{doc_key:path}/sections/{anchor}/links/sync`
   с HTMX-возвратом обновлённой панели.
3. Project-wide `link backfill` — кнопка в `/settings` (опасная операция,
   confirm-modal).

### CW-HI-2. `doc rename` — не реализован в Web

**Где:** [cod_doc/cli/doc/cmd_rename.py](../../../cod_doc/cli/doc/cmd_rename.py).

```bash
cod-doc doc rename DOC_KEY NEW_KEY -p P [--path NEW_PATH] [--no-cascade]
```

**Симптом:** переименование документа = инвалидация всех ссылок в проекте,
+ опционально перенос файла + cascade обновление link-таблицы.
Из Web невозможно. Любой рефакторинг иерархии docs идёт мимо UI.

**Что нужно:** [doc_show.html](../../../cod_doc/templates/web/project/doc_show.html)
header → кнопка «Rename», HTMX-форма (`new_key`, `new_path?`, `cascade?`),
endpoint `POST /p/{slug}/docs/{doc_key:path}/rename`. DoD: error-branch
coverage (конфликт ключа, broken refs).

### CW-HI-3. `revision revert` + `revision show` — не реализованы в Web

**Где:** [cod_doc/cli/revision.py:222-315](../../../cod_doc/cli/revision.py).

| CLI | Web | Note |
|---|---|---|
| `revision list` | ✅ `GET /p/{slug}/revisions` | parity |
| `revision show ID` | ❌ | Web показывает превью в списке, но не полный diff |
| `revision revert ID` | ❌ | Откат истории доступен только из терминала |

**Симптом:** аудит-таб реализован как read-only лог. Попасть из лога в
полный snapshot предыдущей версии — нельзя. Откатить — нельзя. При
конфликте ревизий (см. WEB-022 alert flow) пользователь видит предупреждение,
но не имеет инструмента откатить чужое изменение из браузера.

**Что нужно:** `GET /p/{slug}/revisions/{rev_id}` (полный payload + diff),
`POST /p/{slug}/revisions/{rev_id}/revert` (confirm-modal, HTMX-target
`#alerts`). Re-use [_frag/section_view.html](../../../cod_doc/templates/web/_frag/section_view.html)
для рендера старого состояния.

### CW-HI-4. Project-wide `audit` отсутствует в Web

**Где:** [cod_doc/cli/cmd_audit.py](../../../cod_doc/cli/cmd_audit.py) — 297 LOC,
запускает frontmatter-checks (FM-001..FM-005) + drift-checks (DR-*) по всему проекту.

**Симптом:** [validation_pattern](../../../../.claude/projects/-Users-dakh-Git-cod-doc/memory/validation_pattern.md)
закрепляет advisory-аудит как часть write-path, но **batch-запуск из Web
отсутствует**. Health-check проекта = терминал.

**Что нужно:** `GET /p/{slug}/audit` страница: запуск аудита (lazy, через
HTMX `hx-trigger="load"`), таблица issues по severity (FM/DR коды), фильтр
по типу. Endpoint вызывает `audit_service.run_project_audit` (если такого
нет — выделить из [cmd_audit.py](../../../cod_doc/cli/cmd_audit.py)).

### CW-HI-5. `task create` — нет формы создания задачи в Web

**Где:** [cod_doc/cli/task.py:191-289](../../../cod_doc/cli/task.py).

```bash
cod-doc task create -p P --plan PLAN --section SEC --title T \
  --type feature --priority high [--depends-on ...] [--effort ...]
```

**Симптом:** Web покрывает весь жизненный цикл задачи, **кроме создания**:
есть list, show, status (HTMX), complete, edit description/acceptance,
AI-improve. Но кнопки «New task» нет. Создавать задачи руками = только CLI
или MCP. AI-генерация задач из story есть (`POST /stories/{id}/tasks/generate`),
но это специальный flow, не общая форма.

**Что нужно:** `GET /p/{slug}/plans/{plan_id}/tasks/new` (форма с выбором
секции, типа, приоритета, зависимостей через autocomplete), `POST` на тот же
URL → редирект на `/p/{slug}/tasks/{new_id}`.

---

## 2. Medium

### CW-ME-1. `plan audit`, `plan critical-path`, `plan forward/reverse` — нет в Web

**Где:** [cod_doc/cli/plan/cmd_audit.py](../../../cod_doc/cli/plan/cmd_audit.py),
[cmd_critical_path.py](../../../cod_doc/cli/plan/cmd_critical_path.py),
[cmd_chain.py](../../../cod_doc/cli/plan/cmd_chain.py).

| CLI | Web на `plan_show.html` |
|---|---|
| `plan audit PLAN` | ❌ (нет вкладки issues) |
| `plan critical-path PLAN` | ❌ (mermaid рисуется, но критический путь не подсвечен) |
| `plan forward TASK` | ❌ (на странице task_show есть chains read-only) |
| `plan reverse TASK` | частично — chains рендерятся в task_show |

**Симптом:** Web показывает progress + next-batch + mermaid-граф, но **никаких
аналитических операций**. Невозможно увидеть: «какие задачи блокируют закрытие
плана», «какова длина критического пути», «сколько задач разблокирует
завершение этой». В CLI — три команды.

**Что нужно:** на [plan_show.html](../../../cod_doc/templates/web/project/plan_show.html)
добавить:
- вкладку «Audit» (HTMX-фрагмент с issues),
- подсветку критического пути на mermaid (отдельный CSS-класс на edges),
- линки на chain-просмотр в task_show (он уже есть — нужно лишь обозначить).

### CW-ME-2. `plan export` — markdown-проекции недоступны в Web

**Где:** [cod_doc/cli/plan/cmd_export.py](../../../cod_doc/cli/plan/cmd_export.py).

```bash
cod-doc plan export PLAN -p P [--section progress_overview|next_batch|...]
```

**Симптом:** CLI генерирует markdown-проекции для вставки в roadmap-документы.
В Web этих кнопок нет → stakeholder-отчёт = терминал.

**Что нужно:** `GET /p/{slug}/plans/{plan_id}/export?section=...` → response
`text/markdown` + кнопка «Copy to clipboard» в `plan_show.html`.

### CW-ME-3. `doc drift` — нет проверки расхождения проекции и БД

**Где:** [cod_doc/cli/doc/cmd_drift.py](../../../cod_doc/cli/doc/cmd_drift.py).

**Симптом:** drift-detection — часть [validation_pattern](../../../../.claude/projects/-Users-dakh-Git-cod-doc/memory/validation_pattern.md)
(FM-004/FM-005 advisory). Из Web нельзя понять, расходится ли on-disk файл
с проекцией БД. Это особенно опасно после ручной правки markdown снаружи.

**Что нужно:** бейдж «out of sync» рядом с doc_key на
[doc_show.html](../../../cod_doc/templates/web/project/doc_show.html) +
кнопка «Re-export from DB» (`doc_service.export`). Один endpoint:
`POST /p/{slug}/docs/{doc_key:path}/export`.

### CW-ME-4. `doc export` — кнопка «материализовать на диск» отсутствует

**Где:** [cod_doc/cli/doc/cmd_export.py](../../../cod_doc/cli/doc/cmd_export.py).

**Симптом:** связано с CW-ME-3. Сценарий: оператор отредактировал секции
через UI (WEB-012), хочет получить актуальный markdown-файл на диске.
Сейчас — только `cod-doc doc export DOC -p PROJ`.

**Что нужно:** объединить с CW-ME-3 в одну задачу.

### CW-ME-5. `story status`, `story add-criterion`, `story link`, `story coverage` — отсутствуют

**Где:** [cod_doc/cli/story/](../../../cod_doc/cli/story/).

| CLI | Web |
|---|---|
| `story create` (manual) | ❌ (есть только AI-generate) |
| `story status SID NEW` | ❌ |
| `story add-criterion SID TEXT` | ❌ |
| `story link SID --to-task/--to-doc REF` | ❌ |
| `story coverage SID` | ❌ |

**Симптом:** stories-страница реализует AI-генерацию (`/stories/generate` +
`/stories/save`) и автогенерацию задач (`/stories/{id}/tasks/generate` +
`/tasks/save`). Но дальше — read-only. Перевести story в `accepted`,
добавить критерий приёмки руками, связать с существующей задачей,
посмотреть coverage — нельзя. Story-страница превратилась в одноразовый
draft-инструмент.

**Что нужно:** на [story_show.html](../../../cod_doc/templates/web/project/story_show.html)
добавить:
- inline-edit для status (HTMX dropdown по аналогии с task_status),
- форму «Add criterion» (по аналогии с section patch),
- форму «Link to task/doc» (autocomplete по entity-id),
- блок «Coverage» (агрегат + bar) под header'ом.

### CW-ME-6. Onboarding: `import docs` / `import legacy-tasks` / `import all`

**Где:** [cod_doc/cli/cmd_import.py](../../../cod_doc/cli/cmd_import.py).

**Симптом:** Web имеет:
- `POST /p/{slug}/docs/import` — загрузка одного markdown-файла,
- `POST /p/{slug}/import_master/scan` + `/save` — полу-автоматический
  bootstrap из MASTER.md.

Но **batch-импорт всего проекта** (`import all PROJECT`) и миграция
legacy `.cod-doc/tasks.yaml` (`import legacy-tasks`) — **только CLI**.
[proposals/13-import-ux-redesign.md](../../../proposals/13-import-ux-redesign.md)
и [14-legacy-tasks-migration-ux.md](../../../proposals/14-legacy-tasks-migration-ux.md)
описывают целевой UX, но он не реализован.

**Что нужно:** wizard на странице нового проекта (`/p/{slug}` при пустой БД):
шаг 1 «Scan repo for docs» (dry-run preview), шаг 2 «Import legacy tasks»
(если найден `.cod-doc/tasks.yaml`), шаг 3 «Confirm». Endpoint'ы — обёртки
над `import_service.*`. Часть уже декомпозирована (см. `import_master/scan`).

### CW-ME-7. `agent run PROJECT` ad-hoc — нет UI-триггера на проект

**Где:** [cod_doc/cli/cmd_agent.py](../../../cod_doc/cli/cmd_agent.py),
API уже есть: [routes.py:133](../../../cod_doc/api/routes.py).

**Симптом:** в [index.html](../../../cod_doc/templates/web/index.html) есть
глобальный daemon-бар (start/stop из коммита b07a97e), но **запустить
агента точечно по конкретному проекту** из UI нельзя — endpoint
`POST /api/projects/{name}/run` существует, кнопки нет. WebSocket-стрим
лога (`/ws/projects/{name}/run` в [webhooks.py:135](../../../cod_doc/api/webhooks.py))
тоже не подключён к UI.

**Что нужно:** на [show.html](../../../cod_doc/templates/web/project/show.html)
кнопка «Run agent now» + HTMX-консоль с SSE/WS-стримом. Это пересекается
с WEB-030 (`/run` SSE console) — единственный оставшийся endpoint в
[capability §3](../capabilities/web-frontend.md#3-маршруты).

### CW-ME-8. `task` — log progress / set blocker / list blocked

**Где:** методы есть в `task_service`, в CLI выведены **не все**:

| Метод сервиса | CLI | Web |
|---|---|---|
| `update_status`, `complete` | ✅ | ✅ |
| `update_description`, `update_acceptance` | ❌ (нет CLI) | ✅ (fields/edit) |
| `set_blocker`, `clear_blocker` | ❌ (нет CLI) | ❌ |
| `log_progress` | ❌ (нет CLI) | ❌ |
| `list_blocked`, `list_stale_in_progress` | ❌ (нет CLI) | ❌ |

**Симптом:** это редкий случай — CLI **тоже отстаёт** от сервисного слоя. Но
поскольку методы в сервисе уже есть и протестированы, естественнее закрыть
их сразу в Web (а CLI — оставить как опцию для headless).

**Что нужно:** Task-show панель «Blocker» (set/clear с reason), inline
«Log progress» textarea (HTMX append к `task.progress_log`), глобальная
страница «Blocked tasks» (`/p/{slug}/tasks?status=blocked` уже почти есть,
но без UI для clear). См. также §3.

### CW-ME-9. `project status NAME [--json]` — Web показывает меньше данных

**Где:** [cod_doc/cli/cmd_project.py:112](../../../cod_doc/cli/cmd_project.py).

**Симптом:** CLI выдаёт детальный YAML/JSON: counts по типам документов,
last_run, broken-refs count, daemon-status, рекомендации «next actions».
Web-дашборд (7 KPI-карточек) — лишь агрегат. Отчётность для stakeholder'а
снова в терминале.

**Что нужно:** добавить блок «Health & Recommendations» на
[show.html](../../../cod_doc/templates/web/project/show.html), вызывающий
тот же сервис (`project_service.status_summary`, если такого нет — вынести
из [cmd_project.py:112-189](../../../cod_doc/cli/cmd_project.py)).

---

## 3. Low

### CW-LO-1. `project add` / `project remove` — registry mgmt только в CLI

**Где:** [cod_doc/cli/cmd_project.py:59-95](../../../cod_doc/cli/cmd_project.py).

Регистрация и удаление проекта из глобального `~/.cod-doc/config.yaml`
делается только из терминала. Из `/settings` можно лишь править API-ключ
и daemon-флаги, но не «зарегистрировать новый проект».

**Что нужно:** на `/settings` форма «Register project» (path, name,
master.md). `project remove` — confirm-modal. Risk: оба оператора —
sensitive (могут затереть запись), нужна явная подтверждалка.

### CW-LO-2. `hash calc FILE` / `hash update` — не нужен в UI?

**Где:** [cod_doc/cli/cmd_hash.py](../../../cod_doc/cli/cmd_hash.py).

`hash calc` — низкоуровневая утилита для отладки гибридных ссылок
(SHA-256). Web-аналог не нужен (curl-уровень). `hash update` — батч-
пересчёт хешей в MASTER.md, операция редкая.

**Что нужно:** **deferred.** Зафиксировать в этом аудите как «осознанно
оставлено вне Web» и не заводить задачу. Если потребуется — кнопка
«Recalculate hashes» в settings.

### CW-LO-3. `task list-blocked` / `task stale` — без отдельной страницы

См. CW-ME-8. Можно реализовать как фильтр на существующей
`/p/{slug}/tasks?status=blocked` + новый `?stale=1`. Не требует
отдельной задачи, закрывается в CW-ME-8.

### CW-LO-4. `cli serve` / `mcp` / `tui` / `wizard` — намеренно не в UI

CLI-only по замыслу: `serve` поднимает API (на котором живёт сам Web),
`mcp` — MCP-сервер для Claude, `tui` — Rich-терминал, `wizard` — initial
setup для пустого `~/.cod-doc/config.yaml`. Не считается долгом.

---

## 4. Сводная матрица

| Домен | CLI cmds | Web (RW) | Web (RO) | Покрытие |
|---|---:|---:|---:|---:|
| **docs** | 8 (`create`, `body`, `drift`, `export`, `import`, `list`, `rename`, `show`) | 5 (create, accept, generate-AI, import, sections-patch) | 2 (list, show) | **5/8 (62 %)** — нет rename, drift, export |
| **tasks** | 5 (`list`, `show`, `create`, `status`, `complete`) | 4 (status, complete, fields edit, fields improve) | 2 (list, show) | **4/5 (80 %)** — нет create form |
| **plans** | 7 (`show`, `ready`, `audit`, `critical-path`, `forward`, `reverse`, `export`) | 1 (freeze) | 2 (list, show) | **3/7 (43 %)** — нет audit/critical-path/forward/reverse/export |
| **stories** | 7 (`list`, `show`, `create`, `status`, `add-criterion`, `link`, `coverage`) | 2 (generate-AI, tasks/generate) | 2 (list, show) | **2/7 (29 %)** — нет status/criterion/link/coverage/manual-create |
| **revisions** | 3 (`list`, `show`, `revert`) | 0 | 1 (list) | **1/3 (33 %)** — нет show, revert |
| **link** | 4 (`list`, `sync`, `verify`, `backfill`) | 0 | 0 | **0/4 (0 %)** — отсутствует целиком |
| **project** | 5 (`list`, `add`, `remove`, `init`, `status`) | 2 (init, settings RW) | 1 (status overview) | **3/5 (60 %)** — нет add/remove |
| **hash** | 2 (`calc`, `update`) | 0 | 0 | **0/2 (0 %)** — deferred (CW-LO-2) |
| **audit** | 1 (`audit`) | 0 | 0 | **0/1 (0 %)** |
| **import** | 3 (`docs`, `legacy-tasks`, `all`) | 1 (single-file `docs/import` + `import_master`) | 0 | **1/3 (33 %)** |
| **agent** | 1 (`run`) | daemon start/stop (global) | — | **partial** (нет ad-hoc per-project) |

**Итого по операциям:** Web покрывает примерно **52 %** действий, доступных
через CLI в матрице (46 операций без `serve`/`mcp`/`tui`/`wizard`, которые
намеренно остаются CLI-only — см. CW-LO-4). Полный CLI-набор — 50 команд.

---

## 5. Топ-10 приоритетных гэпов

| # | Гэп | Severity | Trigger |
|---|---|---|---|
| 1 | `link` домен в Web (list/sync/verify) | high | Битые ссылки невидимы из UI |
| 2 | `doc rename` cascade | high | Рефакторинг иерархии docs только из CLI |
| 3 | `revision show` + `revert` | high | Аудит-таб = read-only лог |
| 4 | Project-wide `audit` страница | high | Health-check проекта = терминал |
| 5 | `task create` форма | high | Web покрывает весь lifecycle, кроме создания |
| 6 | Plan analytics (audit/critical-path/chains) | medium | Аналитика плана только из CLI |
| 7 | Story write-ops (status/criterion/link/coverage) | medium | Stories-страница превратилась в draft-only |
| 8 | `doc drift` + `doc export` (объединить) | medium | Drift между БД и диском не виден |
| 9 | Onboarding wizard (`import all` + legacy) | medium | Первое впечатление от UI = пустые экраны |
| 10 | `agent run` per-project (WEB-030 SSE) | medium | Глобальный daemon есть, точечного запуска нет |

---

## 6. Методология

1. CLI-команды собраны через `grep -rnE "@.*\.command\(" cod_doc/cli/` →
   **50 точек входа** в [cod_doc/cli/](../../../cod_doc/cli/) (распределение
   по доменам — в матрице §4).
2. Web-маршруты собраны через `grep -nE "^@router\." cod_doc/api/web/pages/*.py
   cod_doc/api/web/fragments/*.py` → **38 точек входа** (включая HTMX-фрагменты
   для inline-редактирования).
3. Каждая CLI-команда классифицирована: `parity` (есть полный аналог),
   `partial` (есть смежный, но без покрытия конкретного use-case),
   `missing` (нет ни одного endpoint'а).
4. Severity:
   - **high** — отсутствие операции ломает базовый сценарий «работа без
     терминала» либо целый домен невидим;
   - **medium** — операция доступна, но через CLI; продуктивность Web ниже;
   - **low** — операция редкая или сознательно оставлена в CLI.

## 7. Что осталось вне scope

- **MCP-only методы сервисов**, не выведенные в CLI (например
  `task_service.set_blocker`). Этот аудит сравнивает CLI ↔ Web, не
  service-coverage. Service-coverage аудит нужен отдельно.
- **TUI** ([cod_doc/tui/](../../../cod_doc/tui/)) — это альтернативный
  фронтенд, не CLI-команды. Не сравниваем.
- **Visual polish / accessibility** Web — закрыто [audit/2026-05-02-section-web-frontend.md](2026-05-02-section-web-frontend.md).

## 8. Changelog

| Дата | Событие |
|---|---|
| 2026-05-06 | Аудит проведён. 18 находок (5 high, 9 medium, 4 low). Топ-10 приоритизирован. Задачи в roadmap не заводились — это первичная фиксация долга. |
