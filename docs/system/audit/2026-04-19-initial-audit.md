---
type: audit-report
scope: docs/system
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
audit_target_revision: initial-package-2026-04-19
related_docs:
  - ../MASTER.md
  - ../roadmap/audit-followups-task-plan.md
---

# Initial Audit — `docs/system/`

> Аудит первоначального пакета документации COD-DOC (16 файлов, ~3100 строк, статус `draft`).
> Цель аудита — найти противоречия, пробелы, неработающие ссылки, недоговорённости в моделях, прежде чем что-либо превращать в код.
> Severity: **critical** — блокирует реализацию, **high** — нужно решить до этапа A roadmap, **medium** — после A, **low** — косметика.

## Сводка

| Severity | Count | Куда |
|----------|------:|------|
| critical | 2 | трекаются в follow-up плане как DOC-CRT-* |
| high     | 9 | ↳ DOC-HI-* |
| medium   | 7 | ↳ DOC-ME-* |
| low      | 5 | ↳ DOC-LO-* |
| **итого** | **23** | |

Полный список и приоритезация — [../roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md).

## 1. Покрытие требований пользователя

Заявленные пользователем capabilities проверены против пакета:

| Требование | Покрыто | Где |
|-----------|---------|-----|
| Создание задач | ✅ | capabilities/task-creation, standards/task-plan |
| Развитие документации | ✅ | capabilities/doc-evolution |
| Автолинковка | ✅ | capabilities/auto-linking, standards/document-link |
| Ссылки на документы | ✅ | standards/document-link |
| История изменений | ✅ | standards/revision-history |
| Концентрированный контекст | ✅ | capabilities/context-retrieval |
| Ведение плана | ✅ | capabilities/plan-management |
| User stories + граф зависимостей | ✅ | capabilities/user-stories-graph |

Базовые требования закрыты. Дальше — пробелы, которые всплыли при перекрёстном чтении.

## 2. Critical — блокирует реализацию

### CRT-1. Не определён формат `revision_id`

`revision-history.md` использует id `r_abc123`, `r_01hq…`, `r_def456`; формат не зафиксирован. ID должен быть стабильным по времени и сортируемым.

**Фикс:** ULID (`01HQX...`), документировать в `standards/revision-history.md §2`.

### CRT-2. Не определена таблица `embedding`

`capabilities/context-retrieval.md` обещает semantic-search с per-section embeddings, но в `DATA_MODEL.md` нет соответствующей таблицы (`embedding`, `embedding_chunk`).

**Фикс:** добавить §3.14 в `DATA_MODEL.md`.

## 3. High — нужно до начала Section A roadmap

### HI-1. Open Questions / архитектурные решения не моделируются

Restate имеет `Open Questions.md` как канонический реестр нерешённых вопросов. В пакете нет ни capability, ни сущности, ни шаблона.

**Фикс:** новая capability `decisions-and-questions.md`, новые типы документов в `frontmatter.md` (`decision`, `open-question`).

### HI-2. Нет каталога агентов / ролей

Restate использует `.github/agents/` (task-steward, docs-review, logical-commits). COD-DOC говорит про «agent:task-steward» в author-полях `revision`, но не определяет: какие агенты существуют, что им разрешено, как они объявляются в проекте.

**Фикс:** новая capability `agents-and-skills.md` + поле `agents/` в проекте, аналог `.github/agents/` Restate.

### HI-3. Нет стандарта чувствительных данных

В Restate `Sensitive Data Protection Standard.md` — кросс-проектный документ. У нас нет аналога; при этом БД содержит body документов, которые могут содержать PII.

**Фикс:** `standards/sensitive-data.md` + поле `sensitivity` в `Document`.

### HI-4. `cod-doc project new` не описан

Migration ссылается на `cod-doc project new --slug restate`, но что это создаёт в БД, какие skeleton-документы, какие агенты — нигде не описано.

**Фикс:** capability `project-bootstrap.md`.

### HI-5. Audit-чеки разбросаны

Правила валидации перечислены в каждом из 6 capability + frontmatter + task-plan. Нет одного места, где понятно, что именно делает `cod-doc audit --strict`.

**Фикс:** capability `audit-and-ci.md` со сводным каталогом проверок и CLI/CI-интеграцией.

### HI-6. Не определена модель ошибок

Сервисы возвращают `revision_id`, но что происходит при ошибке? Какой код, какая структура, как MCP/REST это передают? — нигде.

**Фикс:** новая секция в `ARCHITECTURE.md §11 Error model` + reusable `Error` enum для всех поверхностей.

### HI-7. Конкурентность и auth для shared Postgres профиля

ARCHITECTURE упоминает Postgres-профиль с REST API, но не описаны: optimistic-locking, авторизация запросов, identity (как surface отличает `human:dakh` от `mcp:claude`).

**Фикс:** `ARCHITECTURE.md §12 Concurrency & Identity` + capability `multi-user-mode.md` либо подсекция в `project-bootstrap`.

### HI-8. `Document.body` vs `Section.body` дублируют контент

В DATA_MODEL обе таблицы хранят body. Не сказано, что — материализованная проекция (production) и что — denormalized cache. При записи будут расхождения.

**Фикс:** объявить `Section.body` derived (`body` хранится только в `document`; секции выделяются view с substring-индексами по anchor-границам), либо наоборот — `document.body` derived view над секциями. Решить в `DATA_MODEL.md §3.2-3.3`.

### HI-9. Нет таблицы `proposal` для review-flow

`doc-evolution.md` обещает `doc.propose_edit` с `pending_approval`. Нет таблицы для хранения предложений между propose и approve.

**Фикс:** добавить `proposal` в `DATA_MODEL.md §3.15`.

## 4. Medium — после фундамента

### ME-1. Documentation Graph — генерируемый артефакт не описан

Restate рендерит `Documentation Graph.md` руками. У нас сервис обладает всей информацией, но рецепт генерации и формат файла не описан.

**Фикс:** в `capabilities/auto-linking.md` (или в новой `documentation-graph.md`).

### ME-2. Нет процедуры backup/restore БД

Локальный `.cod-doc/state.db` — единственный source of truth. Что делать, если он повреждён.

**Фикс:** capability `backup-and-export.md`.

### ME-3. Нет CI-блюпринта

Сказано «CI: cod-doc audit, plan next, link verify» — без конкретики (GitHub Actions, GitLab CI). Нет шаблона.

**Фикс:** добавить в `audit-and-ci.md` (см. HI-5) example pipeline.

### ME-4. `frontmatter.md` и `task-plan.md` пересекаются по `status`

Frontmatter перечисляет 4 значения (`draft`/`review`/`active`/`deprecated`), task-plan — 3 (`pending`/`in-progress`/`done`). Restate явно показал, что путаница. У нас отмечено вскользь — нужно явно отделить набор-словарей.

**Фикс:** `standards/frontmatter.md §3.1 Status sets — by document type`.

### ME-5. Нет обработки `transclusion` (`![[…]]`)

Auto-linking упоминает поддержку, но как это интегрируется с rebuild и embeddings — не сказано (тот же контент дважды индексируется?).

**Фикс:** subsection в `standards/document-link.md §12 Transclusion`.

### ME-6. Нет правил приоритизации задач

`task-plan.md` определяет 4 уровня priority, но не правила, *как* их выставлять.

**Фикс:** §5.6 в `standards/task-plan.md` — расширить.

### ME-7. `audit_log` vs `revision` — границы перекрываются

`revision` пишется при изменении сущности, `audit_log` — при write-path вызове. Но успешный write-path производит revision; зачем тогда audit_log? Сейчас правила пересекаются.

**Фикс:** `revision-history.md §13 audit_log boundary` — пояснить (audit_log: read запросы, неудачные попытки, MCP-метаданные; revision: только успешные state-mutations).

## 5. Low — косметика

### LO-1. Опечатка «zamyka» в `doc-evolution.md:152`

Кириллическое «замыкает» по-латински. Исправить.

### LO-2. Несовпадение терминов «section file» / «section files»

Smешано в task-plan. Унифицировать в «section file» (единственное число) при описании структуры.

### LO-3. `tools/task-plan-ecosystem.md §3` ссылка на Restate без префикса

В `frontmatter.md §7` — ссылка на Restate-док без явной пометки. Добавить «(Restate)» для ясности.

### LO-4. Нет языкового стандарта документов

Все docs на русском, имена сущностей — на английском. Зафиксировать в `MASTER.md §7`.

### LO-5. `MASTER.md` не имеет changelog-таблицы

Декларирует, что после `active` любой документ обязан вести revision, но сам не показывает шаблон.

## 6. Подтверждённое (на чём аудит не нашёл проблем)

- Cross-references между файлами в основном корректны (после исправления §7→§3.13 в ARCHITECTURE).
- Соответствие Restate-форматов (frontmatter, task-plan) — точное.
- Граничные правила (cycle detection, depends_on gate, projection_hash) — описаны и непротиворечивы.
- Migration-план реалистичен: есть rollback (frozen projection), есть warn-mode для legacy-формата.
- Roadmap покрывает все capabilities; зависимости в графе валидны (циклов нет).

## 7. Решения по аудиту

1. **Применить trivial fix** немедленно (готово: `ARCHITECTURE.md` §9 ссылка `§7` → `§3.13`).
2. **Создать стаб-капабилити** для HI-1..HI-5 в этом же шаге (компактные документы).
3. **Завести follow-up план** ([../roadmap/audit-followups-task-plan.md](../roadmap/audit-followups-task-plan.md)) — все 23 пункта как задачи.
4. **Заблокировать реализацию section A roadmap-а** (`COD-001..005`) до закрытия CRT-1, CRT-2, HI-8, HI-9 — иначе схема будет переделана.

## 8. Дальнейшие аудиты

- После закрытия audit-followups плана → второй проход (focus: реализация vs документация).
- Перед `status: active` пакета → формальный sign-off от owner.
- Раз в месяц после миграции Restate → автоматический `cod-doc audit --strict` по самим докам пакета (dogfood).
