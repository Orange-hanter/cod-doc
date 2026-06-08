---
type: audit-report
scope: cod_doc/* + docs/system/* (Section C — capability layer + system docs coherence)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-28
last_updated: 2026-05-02
audit_target_revision: HEAD = 37c45b1 (post COD-013/014/020/022/023 + Section B/C done)
related_docs:
  - ../MASTER.md
  - ../roadmap/cod-doc-task-plan.md
  - ../roadmap/audit-followups-task-plan.md
  - 2026-04-25-section-a-data-core.md
  - 2026-04-25-section-b-services.md
  - 2026-05-02-section-web-frontend.md
---

# Section C (Capability Layer) — System Audit

> Глубокий аудит COD-DOC по 4 осям: соответствие кода capability-документам, внутренняя связность пакета `docs/system/`, синхронизация roadmap с реальностью, покрытие тестами и инфраструктурой.
> Состояние ядра (Section A) и сервисов (Section B) — здоровое, оба audit'а закрыты как `resolved`. Этот отчёт фокусируется на capability-слое и обвязке.

## Сводка

| Severity | Count | Fixed inline | New task | Deferred |
|----------|------:|-------------:|---------:|---------:|
| critical | 1 (DocService без FM-валидации) | 1 ✅ | — | 0 |
| high     | 4 (CI отсутствует; sensitive-data без infra; web обходит сервисы; markdown-cascade) | 1 ✅ (web docs hint) | 4 (COD-024, COD-025, WEB-040, COD-014a) | 0 |
| medium   | 6 (frontmatter type/status; MASTER §2; COD-020 статус; FM-007 reserved; capability/CLI gaps; TUI tests) | 5 ✅ | 1 (COD-026) | 0 |
| low      | 5 (audit-followups стейл; примеры в backticks как «битые»; orphan capabilities; api_navigation TBD; LinkService.rename markdown-paths) | 3 ✅ | 1 (COD-014a) | 1 (api_navigation — ждёт COD-051) |
| **итого** | **16** | **10 ✅** | **6 заведено** | **1** |

326 тестов проходят (плюс 3 новых на FM-002/FM-003 в `test_doc_service.py`).

---

## 1. Critical

### SC-CR-1. ✅ DocService.create обходит frontmatter-валидацию

**Где:** [cod_doc/services/doc_service.py:93-137](../../../cod_doc/services/doc_service.py)

**Симптом:** COD-020 заявлен как реализованный, валидация подключена в `TaskService.create` и `StoryService.create`, но **не в `DocService.create`**. Можно создать документ с `status=active` без `owner` (нарушение FM-002), или с `source_of_truth=false` без `canonical_source` (нарушение FM-003).

**Фикс (применён в этой ревизии):**
- Добавлен `_gate_frontmatter()` helper в `doc_service.py` — эскалирует `severity=error` issues из `validation.audit_frontmatter` в `ValidationError`. FM-004/FM-005 (freshness) остаются advisory — они surface'ятся через `cod-doc audit`, не через write-path.
- Подключён в `create()`.
- 3 новых теста в `tests/services/test_doc_service.py`: FM-002 reject, FM-003 reject, draft-без-owner accept.
- Существующие тесты, создававшие `status=ACTIVE` без `owner` (`test_link_service`, `test_web_docs`, `test_story_service`, `_new_doc` helper), приведены в соответствие со спекой.
- Карточка COD-020 в roadmap переведена в `done` с явной пометкой об эскалации FM-002/FM-003.

---

## 2. High

### SC-HI-1. ✅ COD-024 закрыт. CI workflow создан

**Где:** [.github/workflows/ci.yml](../../../.github/workflows/ci.yml) (COD-024 done 2026-04-28).

**Симптом (был):** 326 тестов, mypy strict, ruff не прогонялись на PR. Регрессии ловились только локально.

**Фикс:** workflow с тремя job'ами:
- **pytest** (matrix 3.11 + 3.12) — блокирующий, 329/329 зелёные.
- **ruff** + **mypy** — `continue-on-error: true` (advisory) из-за pre-existing debt: 407 ruff-ошибок, 101 mypy-ошибка. Подняты в новую задачу **COD-024a** (lift advisory gates to blocking). Решение принять долг как видимый-но-непреграждающий, а не блокировать продуктивную работу до полной чистки.
- Concurrency-group отменяет суперседнутые runs.

### SC-HI-2. ✅ → задача COD-025. Sensitive-data: только поле БД, инфраструктуры нет

**Где:** [docs/system/standards/sensitive-data.md](../standards/sensitive-data.md) описывает `SensitivityScanner`, redaction в `ProjectionService.export()`, фильтрацию в `ContextService.get()`, поле `agent.sensitivity_clearance`.

**Симптом:** в коде только enum `Sensitivity` на `Document.sensitivity`. Сам сканер, redaction, clearance-поле и фильтр — отсутствуют. Стандарт «висит» без обязательств.

**Фикс:** заведена задача **COD-025**. Параллельно зарезервирован код `FM-007` в [standards/frontmatter.md §6](../standards/frontmatter.md) под warning «отсутствие `sensitivity` для module-spec/architecture/standard».

### SC-HI-3. ✅ → задача WEB-040. Web layer обходит сервисный слой

**Где:** historical `cod_doc/api/web/db_resolver.py` импортировал `DocumentModel` напрямую из `cod_doc.infra.models`; файл удалён в WEB-040.

**Симптом:** нарушает правило [capabilities/web-frontend.md §7](../capabilities/web-frontend.md): «Web-страница не имеет права обходить сервис».

**Фикс:** заведена задача **WEB-040 (Section E: Architecture Hygiene)** — удалить `db_resolver.py`, перевести `pages.py`/`fragments.py` на `cod_doc.services.*` + DI через `cod_doc.api.deps`. Linter-rule (banned imports) предотвратит регрессию.

### SC-HI-4. ✅ → задача COD-014a. LinkService.rename не каскадит markdown-relative ссылки

**Где:** [cod_doc/services/link_service.py:rename_cascade](../../../cod_doc/services/link_service.py).

**Симптом:** COD-013 в `rename_cascade` намеренно пропускает markdown-relative ссылки `[label](../path.md)` — слишком хрупко без mapping'a путей. Canonical refs `[[doc:KEY]]` обновляются. Это документированный gap, не баг, но он создаёт битые ссылки в проекциях после rename.

**Фикс:** заведена задача **COD-014a** — построить path-mapping и переписывать markdown-relative refs тем же diff-flow.

---

## 3. Medium

### SC-ME-1. ✅ Стандарт frontmatter не определял `type → status`-таблицу

**Где:** [standards/frontmatter.md §2](../standards/frontmatter.md).

**Симптом:** значение `status: resolved` использовалось в audit-отчётах, но не было в списке допустимых. То же для `accepted` (user-story), `superseded` (audit-report).

**Фикс (применён):** добавлен §2a «Допустимые `status` по `type`» — explicit таблица для всех типов, включая `audit-report` (active/resolved/superseded). Добавлены коды FM-006 (несовместимая пара type/status) и FM-007 (reserved sensitivity). DOC-ME-4 переведена в `done`.

### SC-ME-2. ✅ MASTER.md §2 неполный

**Где:** [docs/system/MASTER.md §2](../MASTER.md).

**Симптом:** `standards/sensitive-data.md` и три audit-отчёта не упомянуты в индексе структуры пакета.

**Фикс (применён):** добавлены `sensitive-data.md` в section `standards/`, перечислены все audit-файлы. Расширена таблица §5 с явными статусами audit/roadmap-документов.

### SC-ME-3. ✅ Статус COD-020 не синхронизирован

**Где:** [roadmap/cod-doc-task-plan.md:391](../roadmap/cod-doc-task-plan.md).

**Симптом:** карточка COD-020 показывала `status: pending`, хотя коммит `426b33a` уже реализовал валидацию. Блокировало COD-030/COD-031 (зависят от COD-020).

**Фикс (применён):** переведено в `done` с пометкой об эскалации FM-002/FM-003 в DocService и о неперекрытых правилах (TP-006…TP-011 — section-level cross-checks; FM-006/FM-007 — частично активированы вместе с этим аудитом).

### SC-ME-4. ✅ `source_of_truth` как nested dict не задокументирован в §2

**Где:** [standards/frontmatter.md §2](../standards/frontmatter.md).

**Симптом:** §2 определял поле как boolean, но execution-plan'ы используют nested dict (упоминалось только в §7).

**Фикс (применён):** §2 теперь явно ссылается на §7-вариант для execution-plan; §7 расширен примером и пояснением, что FM-003 не применяется при dict-варианте.

### SC-ME-5. ⚪ Capability gaps (CLI/MCP/ContextService)

**Где:** [cod_doc/cli/](../../../cod_doc/cli/), [cod_doc/mcp/server.py](../../../cod_doc/mcp/server.py).

**Симптом:** CLI покрывает ~10% от спеки (нет `cod-doc doc/task/plan/link/decision/context/audit`). MCP-сервер — legacy на файловом `Project.tasks[]`, не на сервисах. ContextService отсутствует (есть только legacy `core/context.py`).

**Решение:** уже отслежено в плане как **COD-030, COD-031, COD-032, COD-040** (Section D + E). Не дубль, не задача — фактическая ситуация совпадает со статусом `pending` в плане.

### SC-ME-6. ✅ → задача COD-026. TUI без тестов

**Где:** [cod_doc/tui/](../../../cod_doc/tui/) — 1100+ строк, 0 тестов.

**Фикс:** заведена задача **COD-026** — smoke-тесты через `textual.pilot.Pilot` (низкий приоритет).

---

## 4. Low

### SC-LO-1. ✅ audit-followups-task-plan стейл

**Где:** [roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md).

**Симптом:** `last_updated: 2026-04-19`, не обновлялся 9 дней.

**Фикс (применён):** обновлён до 2026-04-28; DOC-ME-4 переведена в `done`; Progress Overview пересчитан (13/23 done).

### SC-LO-2. ✅ False-positive «битые ссылки» в стандартах

**Где:** [standards/document-link.md §1](../standards/document-link.md), [capabilities/plan-management.md §3](../capabilities/plan-management.md).

**Симптом:** аудит-агент отметил «битые ссылки» на `tasks/section-a.md`, `../modules/M1-auth/overview.md` и т.п.

**Фикс (анализ):** все эти ссылки находятся внутри fenced code blocks или в backticks — не активные, а синтетические примеры. Линтер ссылок должен игнорировать code-fence содержимое (это уже учтено в `LinkService.parse` — она пропускает code blocks). Действий не требуется.

### SC-LO-3. ✅ Orphan capabilities (упомянуты только в audit'е)

**Где:** `capabilities/decisions-and-questions.md`, `audit-and-ci.md`, `project-bootstrap.md`.

**Симптом:** созданы стабами при первом аудите, никто на них не ссылается из других документов.

**Фикс (применён):** оба MASTER.md (§2 структура и §4 матрица) уже ссылаются на эти файлы. Дальнейшие ссылки появятся естественно при реализации COD-024/030/032.

### SC-LO-4. ⏳ `api_navigation` помечен «будет позже»

**Где:** [migration/from-restate.md:71](../migration/from-restate.md).

**Решение:** связано с COD-051 (Restate importer) — пока deferred. Действий не требуется до старта Section F.

### SC-LO-5. ✅ → задача COD-014a. LinkService.rename markdown-paths skip

См. SC-HI-4. Описан как low-severity follow-up к закрытой COD-013.

---

## 5. Закрыто inline в этой ревизии

| Code | Fix | Файлы |
|------|-----|-------|
| SC-CR-1 | DocService.create FM-валидация | `cod_doc/services/doc_service.py`, `tests/services/test_doc_service.py` (+3 теста) |
| SC-ME-1 | §2a type→status таблица | `standards/frontmatter.md` |
| SC-ME-2 | MASTER.md §2 + §5 | `docs/system/MASTER.md` |
| SC-ME-3 | COD-020 → done | `roadmap/cod-doc-task-plan.md` |
| SC-ME-4 | source_of_truth dict в §7 | `standards/frontmatter.md` |
| SC-LO-1 | audit-followups refresh | `roadmap/audit-followups-task-plan.md` |
| SC-LO-2 | false-positive (анализ) | — |
| SC-LO-3 | MASTER orphans | `docs/system/MASTER.md` |
| (DOC-ME-4) | type→status в frontmatter | переведено в `done` |

## 6. Заведено в roadmap

| Task | Section | Priority | Notes |
|------|---------|----------|-------|
| **COD-024** | G-Hardening | high | CI workflow — стартует первым |
| **COD-025** | G-Hardening | high | Sensitive-data infra (зависит от COD-020) |
| **COD-026** | G-Hardening | low | TUI smoke-tests |
| **COD-014a** | G-Hardening | medium | rename markdown-cascade |
| **WEB-040** | E-Architecture-Hygiene (web) | medium | удалить db_resolver bypass |

## 7. Что осталось вне scope

- **Capability-completeness** (CLI / MCP / ContextService / Decisions+Questions) — уже отслеживается в плане как Section D/E. Реализация — будущие COD-030..032, COD-040.
- **TP-006…TP-011** — section-level cross-checks из task-plan стандарта; реализуются в составе COD-031 (cod-doc audit CLI).
- **api_navigation usage** — ждёт COD-051 (Restate importer).

---

## 8. Changelog

| Дата | Событие |
|------|---------|
| 2026-04-28 | Аудит проведён; 10 inline-фиксов применены, 5 задач заведено в roadmap (COD-024, COD-025, COD-026, COD-014a, WEB-040). 326+3 тестов зелёные. |
| 2026-04-28 | COD-024 закрыт — `.github/workflows/ci.yml` создан (pytest blocking, ruff/mypy advisory). Заведена follow-up COD-024a (lint debt cleanup). |
| 2026-05-02 | **Resolved.** Последняя задача SC-HI-3 (web → infra bypass) закрыта в WEB-040: удалён `cod_doc/api/web/db_resolver.py`, web-слой переведён на `cod_doc.api.deps.{get_project_db, try_open_project_db}`, архитектурное правило закреплено AST-тестами в `tests/api/test_web_layer_imports.py`. Suite 45 web-тестов зелёные. |
