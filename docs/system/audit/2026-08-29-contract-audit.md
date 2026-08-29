---
type: audit-report
scope: contract-audit
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-29
last_updated: 2026-08-29
related_docs:
  - ../ARCHITECTURE.md
  - ../DATA_MODEL.md
  - ../standards/code-quality.md
  - ../../../AGENTS.md
audience: [contributors, agents]
---

# Audit — Контракты кодовой базы (ADO-034)

> **Контекст.** Исследование по решению владельца 2026-08-29: нарушения
> контрактов, плохие/устаревшие/отсутствующие контракты. Только анализ —
> код не менялся. Охват: `services/`, `mcp/`, `infra/`, `api/`, `core/`,
> `domain/`. Метод: детерминированные проверки (pyan3 call-граф 7143 ребра,
> runtime-дамп 111 MCP-тулов, sigdiff сигнатур), 4 explore-аудита по модулям,
> пилот ai-reviewer (offline `--diff`). Каждая находка верифицирована чтением
> кода оркестратором.

## TL;DR

Кодовая база в целом следует задекларированным контрактам, но аудит вскрыл
**3 критических нарушения** (remote-перезапись LLM-ключа через
`PATCH /api/config`; потеря `frontmatter_raw`/`title_in_body` при round-trip
через `DocumentRepository`; legacy `/api/projects/*` write-эндпоинты через
YAML-путь), **18 major** (статус-машина обходится через `complete()` и
`task_update_status`; ~10 write-сервисов без activity events; перевёрнутая
зависимость services→mcp; SQL в MCP-слое; мёртвая `audit_log`;
рассинхрон DATA_MODEL.md и документации профилей) и **12 minor**.
Итого 21 finding → 21 задача в БД (ADO-035…ADO-055, section D плана
`adoption-2026-08`). Код не изменён; все находки верифицированы чтением.

ai-reviewer пилот состоялся: offline `--diff` работает, жюри дало ~40
находок, после верификации 3 новые подтверждённые (C4/M19/M20) и
низкий FP-шум на архитектурных нарушениях.

## 1. Инвентария контрактов (источники истины)

| Инвариант | Источник | Способ проверки |
|---|---|---|
| Слоистость presentation → services → domain ← infra | ARCHITECTURE.md §1/§6 | pyan3 граф, рёбра между слоями |
| «В MCP нельзя SQL напрямую» | ARCHITECTURE.md §6 | grep `select(` в mcp/tools |
| Activity event на каждой мутации | AGENTS.md §5.6, proposal 09 | grep emit × write-сервисы |
| Атомарный checkout todo→in_progress | proposal 06, AGENTS.md §5.3 | grep enforce_checkout / via_checkout |
| Validate transitions | AGENTS.md §5.5, task_status_machine.py | прочтение write-путей статуса |
| Run-id на всех мутациях | proposal 04, AGENTS.md §5.4 | grep run_scope по слоям |
| Echo-without-persist | AGENTS.md §5.8 | sigdiff MCP×service×БД |
| Модель ↔ миграции ↔ enum ↔ docs | AGENTS.md §6, DATA_MODEL.md | сверка enum/миграций/доков |
| Профили MCP | AGENTS.md §5.9, profiles.py | runtime counts |
| Hash-verified docs | AGENTS.md §5.1 | grep update_master_hashes |
| Error model + маппинг | ARCHITECTURE.md §10 | чтение surface-кода |
| Optimistic locking parent_revision_id | ARCHITECTURE.md §11 | grep expected_parent_revision_id |
| AuditLog для write-операций | ARCHITECTURE.md §9, DATA_MODEL §3.13 | grep AuditLogModel( |
| Bearer-гейт /api/v1 (отложен до 1-го удалённого вызывающего) | ARCHITECTURE.md §9, RFC 22 §3.3 | чтение server.py |

## 2. Findings

### Critical

- **C1. [нарушение] `PATCH /api/config` перезаписывает LLM-ключ без
  loopback-гварды.** `cod_doc/api/routes.py:49-55` — `setattr(cfg, field,
  value); cfg.save()` без `ensure_loopback_client`, тогда как веб-форма
  `POST /settings` её имеет (`api/web/pages/_helpers.py:17-22`). При
  `COD_DOC_BIND=0.0.0.0` ключ читается/меняется удалённо. Асимметрия
  контракта bind-hygiene (SYM-003): гейт есть на одной поверхности и нет
  на другой.
- **C2. [нарушение] `DocumentRepository` round-trip теряет
  `frontmatter_raw` / `title_in_body` / `content_sha256_head`.**
  `infra/repositories/document_repo.py:23-66` не маппит поля, добавленные
  миграцией 0025 (ADO-010) и PCA-928; `domain/entities.py:224-241` их не
  содержит. Любое обновление документа через репозиторий занулит
  verbatim-frontmatter → риск повторной порчи проекций, которую ADO-010
  закрывал.
- **C3. [нарушение] Legacy `POST/PATCH /api/projects/{name}/tasks` пишет
  через YAML-путь и ломается на мигрированных проектах.**
  `api/routes.py:144-158` → `core/project.py` `add_task` (`RuntimeError`
  после архивации tasks.yaml). Мимо сервисов: без Revision, без activity
  event, без статус-машины. При этом v1-доки декларируют legacy «замороженным»
  (`api/v1/__init__.py:11-15`).

### Major

- **M1. [устаревший] Default MCP-профиль — `agent`, документация говорит
  `standard`.** `mcp/server.py:122` (`default=os.environ.get("COD_DOC_PROFILE",
  "agent")`) vs AGENTS.md §5.9 и `mcp/profiles.py:11` («standard (current
  default)»). Числа тоже устарели: minimal ~18→**20**, standard ~80→**107**,
  full ~102→**111** (замер runtime-дампом 2026-08-29).
- **M2. [нарушение] `task_service.complete()` не вызывает
  `validate_transition`.** `services/task_service.py:516-550` — переход в
  `done` из любого статуса (например `backlog`/`cancelled` → done) в обход
  ALLOWED_TRANSITIONS; используется MCP `task_complete`, `agent_complete` и
  web-кнопкой (`api/web/fragments/tasks_status.py:115-121`).
- **M3. [нарушение] `todo → in_progress` доступен в обход `task_checkout`.**
  `task_status_machine.py:95` — `enforce_checkout=False` по умолчанию и ни
  одного вызова с `True` в репо; `task_update_status` MCP-docstring
  декларирует MUST через checkout, но не реализует; web-фрагмент
  (`api/web/fragments/tasks_status.py:52-59`) шлёт `strict=False,
  via_checkout=False`. AGENTS.md §5.3 не enforced.
- **M4. [нарушение] Десятки write-путей без activity events.** Сервисный
  слой без единого emit: `adr_service` (16 write-сайтов), `approval_service`,
  `task_doc_service`, `story_service/crud`+`links`, `comment_service`,
  `checkout_service`, `commit_link_service`, `link_service/resolver`,
  `repo_index_service`, `run_context`; `doc_service` — 1 emit на 10
  write-сайтов. MCP-слой без emit: `plan_create`, `story_*`, `routine_*`,
  `adr_*`, `link_sync`, `revision_revert`. Количественное выражение
  известного gap'а PCA-912.
- **M5. [нарушение] Ошибки emit'а глотаются.** `task_service.py:405-420`,
  `574-589` — `except Exception: pass` вокруг `activity_service.emit`:
  audit-запись best-effort, сбой невидим.
- **M6. [нарушение] Слоистость: services → mcp импорт.**
  `services/agent_service.py:135,276` импортирует `task_to_dict` из
  `cod_doc.mcp.tools._db` — стрелка зависимости перевёрнута.
- **M7. [нарушение] SQL напрямую в MCP-слое.** 7 модулей `mcp/tools/`
  (`link_tools`, `plan_tools`, `revision_tools`, `task_tools`, `_db`,
  `task_doc_tools`, `run_tools`) импортируют `sqlalchemy.select` и строят
  запросы к моделям — против прямого запрета ARCHITECTURE.md §6.
- **M8. [нарушение] `audit_log` — мёртвая таблица.** Ни одного writer'а в
  `services/`, `mcp/`, `api/` (grep `AuditLogModel(` — только определение
  модели `infra/models/revisions.py:48`); читает только `run_tools`.
  ARCHITECTURE.md §9 / DATA_MODEL §3.13 декларируют её как аудит
  write-операций.
- **M9. [плохой контракт] run-id «на всех мутациях» не реализован вне
  оркестратора.** `run_scope` открывается только агентным раннером; API/CLI
  мутации идут с `run_id=NULL` (разрешено `run_context.py:7-8`). Контракт
  proposal 04 противоречит собственному допущению NULL — непроверяем.
- **M10. [устаревший] DATA_MODEL.md не соответствует схеме и домену.**
  Task.status описан как 3-state (`DATA_MODEL.md:223`) vs 7-state proposal
  08; view `ready_tasks` без `d.kind='blocks'` (`:475-488`) vs все миграции
  с фильтром; `EntityKind` без `task_doc`/`adr` (`:172`).
- **M11. [устаревший] Legacy `TaskStatus` в `core/project.py:23-28`**
  (5 значений) vs канонический 7-state; `next_pending_task` (:225-230)
  молча игнорирует `todo`-задачи (ловит ValueError).
- **M12. [нарушение] Миграции ставят `server_default=current_timestamp()`**
  на timestamp-поля (миграции 0010–0014, 0018) против DATA_MODEL §5
  (источник времени — Python `_utcnow`, без server_default).
- **M13. [нарушение] FTS5-миграция SQLite-only.** `0023_fts5_index.py:33-45`
  бросает `NotImplementedError` на Postgres → `alembic upgrade head`
  невозможен на server-профиле, хотя ARCHITECTURE §4.1 декларирует общую
  схему.
- **M14. [отсутствующий] Домен не видит checkout-lock и projection-hash
  метаданные.** `Task` dataclass без `checked_out_by/at`
  (`models/plans.py:114-119` vs `entities.py:295-313`);
  `Document` без `content_sha256_head` (см. C2).
- **M15. [отсутствующий] Нет единого write-path wrapper'а.** Каждый сервис
  вручную комбинирует `rev.write` + emit + run_id; гарантии «мутация =
  revision + activity + run-tag» нет — держится на дисциплине.
- **M16. [нарушение] Web-мутации задач без optimistic locking.**
  `tasks_status.py`, `tasks_fields.py` не передают
  `expected_parent_revision_id` (поддержан в `task_service`), тогда как
  редактор секций передаёт. Частичное применение контракта §11.
- **M17. [отсутствующий] Жизненный цикл документа не валидируется.**
  `doc_accept`/web принимают любой `DocumentStatus`
  (`api/web/pages/docs.py:62-68`, `doc_service.py:377-413`) — аналог
  статус-машины для документов отсутствует.
- **M18. [плохой] `actor_kind` эвристикой.** `author.startswith("agent")`
  (`task_service.py:412` и др.) — формат author нигде не зафиксирован;
  `mcp:…`, `orchestrator-run-…`, `human:…` классифицируются неверно.

### Minor

- **m1.** Raw SQL в services в обход repositories (`doc_service.py:229`,
  `plan_service/reads.py:93-104`, `search_service.py:281`,
  `routine_service.py:329`).
- **m2.** Свои же ROADMAP/sprint-документы используют типы, которых нет в
  `DocumentType` (`roadmap-index`, `sprint-plan`) → coercion + warnings при
  импорте (`projection_service/_frontmatter.py:99-110`).
- **m3.** AGENTS.md §5.1 (update_master_hashes) живёт только в legacy
  agent-тулах (`agent/tools.py:125`); MASTER.md содержит legacy
  `doc:*`-ссылки YAML-эпохи.
- **m4.** `api/web/pages/routines.py:26` использует приватный
  `routine_service._cron_next_fire` — инкапсуляция.
- **m5.** Несогласованные fallback'и активного профиля: `agent_tools` —
  `"agent"`, `context_tools` — `"full"`, `server.py` — `"full"`.
- **m6.** `module.module_id` глобально UNIQUE после shared-hub миграции
  (`models/modules.py:25`) — два проекта не смогут иметь `M1-auth`.
- **m7.** `Section.content_hash` NOT NULL без default — инвариант на
  дисциплине сервиса.
- **m8.** `link_suggestion.from_section_id` без FK.
- **m9.** `EntityKind` без DB CHECK (`models/revisions.py:36`).
- **m10.** Комментарий миграции 0010 путает UUID7/ULID длину.
- **m11.** `task_update_status` не принимает `run_id` — тегирование только
  через contextvar.
- **m12.** ARCHITECTURE.md: status `draft` при `source_of_truth: true`;
  нумерация разделов сбита (10 → 11.1 → 11 → 12.1 → 12).

### Critical (ai-reviewer, верифицировано)

- **C4. [плохой контракт] Export с `audience` перезаписывает канонический
  файл redacted-контентом.** `projection_service/export.py:226-272` —
  `_safe_target(root_path, model.path)` не зависит от audience: redacted body
  пишется в канонический путь, `projection_hash` намеренно не обновляется
  (:271). После такого export'а drift показывает edited_in_place, а повторный
  `doc import` затягивает redacted body в БД — порча канонического контента.
  → **ADO-053**.

## 3. ai-reviewer пилот

Движок `/Users/dakh/Git/_my/ai-reviewer`, offline-режим
`bin/pr-review.mjs --diff <patch> --dry-run`, профиль cod-doc в
`/tmp/ai-review-cod-doc/` (в репо не писался). Два прогона:

- **A — ретроспектива спринта M2** (`git diff cb9178f^..30c43f7`), MoA-жюри
  (deepseek-v4-flash / kimi-k2.7-code / glm-5.2, агрегатор minimax-m3), ~8,5
  мин: 0 critical / 8 major / 7 minor / 5 nit.
- **B — модуль services/** (4 семейства синтетических diff'ов), consensus
  kimi-k2.7-code: ~22 находки.

**Подтверждено нового после верификации (не пересекалось с этапами 2/4):**

- C4 (audience-export, выше).
- **M19. [плохой контракт] `on_finding='create_task'` валидируется, но не
  реализован.** `routine_service.py:52` (VALID_ON_FINDING), `:376`
  (валидация), `:535` (run_now обрабатывает только `update_existing_task` —
  create_task молча no-op). → **ADO-054**.
- **M20. [нарушение] Import update-путь молча теряет секции.**
  `import_service.py:461-488` — двойной вложенный `except Exception: pass`:
  провал `patch_section` → попытка `add_section` → провал → `pass`. Импорт
  «успешен», секция потеряна. Рядом: FTS-upsert (:444, :495) не изолирован
  savepoint — сбой FTS откатит весь импорт. → **ADO-055**.

**Minor (в отчёт, без задач):** `_check_stale_refs` без containment-проверки
пути из MASTER.md (`routine_service.py:183-197`); race при генерации
`task_id` (`task_service.py:91`); frontmatter-поле `title` не применяется при
импорте (`_frontmatter.py:191-204`); `assert d.row_id is not None` снимается
под `-O` (`doc_service.py:591`); противоречия внутри web-frontend.md (метрики
:411, §6/§7 vs :121/:122/:165, приватный `_call_lite_raw` в :119/:138);
docstring `update_status` vs сигнатура (`task_service.py:333`).

**FP-оценка:** низкая на архитектурных нарушениях (прямые импорты infra в
services, ORM-апдейт в `agent_service.release` :594 — подтверждены);
явные FP — «doc_service не эмитит activity» (правило проекта про MCP-слой)
и `PurePath.match **` (pyproject требует 3.13+); «services импортирует infra
репозитории» — легитимный паттерн, severity завышена.

**Выводы для движка ai-reviewer (не задачи cod-doc):** router хардкодит
TS/JS-расширения (для Python нужен `REVIEW_ROUTER=0`); `--dry-run` не пишет
JSON/SARIF (`pipeline.mjs:64`), один export-dir перезаписывает файлы прогонов;
prescan только JS/TS; агрегатор minimax-m3 нестабилен по таймаутам.

## 4. Ограничения метода

- pyan3 — приблизительный статический граф (динамический диспатч,
  декораторы FastAPI дают частичные рёбра); использовался для слоистости,
  не для полноты покрытия.
- pycg отвергнут: unmaintained, падает на Python 3.13/3.14
  (`ImportManagerError`).
- sigdiff — эвристика по именам параметров; все кандидаты echo-without-persist
  требуют ручной верификации (большинство — resolve-параметры вида
  `task_id`/`plan_scope`, не подлежащие персисту).

## 5. Next step

21 задача заведена в plan `adoption-2026-08`, section D (по одной на
critical/major finding; M4+M5+M15 объединены в ADO-040, M7+m1 — в ADO-042,
M10+M11 — в ADO-045):

| Задача | Finding | Приоритет |
|---|---|---|
| ADO-035 | C1 loopback-гварда PATCH /api/config | critical |
| ADO-036 | C2 DocumentRepository round-trip | critical |
| ADO-037 | C3 legacy /api/projects tasks | critical |
| ADO-038 | M2 complete() без validate_transition | high |
| ADO-039 | M3 enforce atomic checkout | high |
| ADO-040 | M4+M5+M15 activity events / write-path | high |
| ADO-041 | M6 services→mcp импорт | high |
| ADO-052 | M1 профили MCP: default + counts | high |
| ADO-053 | C4 audience-export в канонический путь | high |
| ADO-055 | M20 import теряет секции | high |
| ADO-042 | M7+m1 SQL в mcp/tools и services | medium |
| ADO-043 | M8 мёртвая audit_log | medium |
| ADO-044 | M9 run_id для API/CLI | medium |
| ADO-045 | M10+M11 DATA_MODEL + legacy TaskStatus | medium |
| ADO-046 | M12 server_default=current_timestamp | medium |
| ADO-047 | M13 FTS5 SQLite-only | medium |
| ADO-048 | M14 checkout-поля в домене | medium |
| ADO-049 | M16 optimistic locking web | medium |
| ADO-054 | M19 on_finding=create_task | medium |
| ADO-050 | M17 статус-машина DocumentStatus | low |
| ADO-051 | M18 actor_kind эвристика | low |

Minor-находки (m1…m12 + minor из ai-reviewer) — в §2/§3 отчёта, без задач.
Приоритизация и порядок исполнения — на планировании следующего спринта
(M3 по ROADMAP может быть вытеснен этим backlog'ом — решение владельца).
