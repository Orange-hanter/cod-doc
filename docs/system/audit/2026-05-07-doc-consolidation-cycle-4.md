---
type: audit-report
scope: documentation-consolidation / cross-links + drift + dedup
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-07
last_updated: 2026-05-07
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-07-doc-consolidation-cycle-1.md
  - 2026-05-07-doc-consolidation-cycle-2.md
  - 2026-05-07-doc-consolidation-cycle-3.md
  - ../MASTER.md
---

# Documentation Consolidation — Cycle 4 (Cross-links & Integrity)

> **Назначение.** Зафиксировать состояние ссылочной целостности по итогам
> циклов 1-3, дезамбигировать DB-doc-records, обновить MASTER-индекс и
> зафиксировать обнаруженные drift'ы как backlog.

## 1. TL;DR

- Хеши гибридных ссылок в `/MASTER.md` — **10/10 VALID** (`check_stale_refs`).
- Cycle-2/3 audit-report'ы зарегистрированы как doc-records (active).
- Найден ещё один gap link_service (G4) — relative-path resolution из секций
  не работает, что даёт **39 broken-links на `docs/system/MASTER` →
  capabilities/standards/audit/roadmap**, хотя все целевые doc_keys
  существуют.
- Найден реликт фикстуры на диске: `arch/arch/architecture.md` (commit
  e51e85f) — двойной префикс пути, контент тот же что в старом «integration-test»
  bootstrap. Решено не удалять без явной команды (см. §4).
- Doc-drift на `docs/system/MASTER` и `MASTER`-root — `stale_export`:
  файлы редактировались on-disk, БД-проекция не пересинхронизирована.
  Это ожидаемое состояние после edit-in-place; resync — пункт backlog.

## 2. Cycle-4 deliverables

| # | Деливерабл | Файл/действие | Статус |
|---|------------|----------------|--------|
| D1 | Cycle-4 audit-report | `docs/system/audit/2026-05-07-doc-consolidation-cycle-4.md` | ✅ this file |
| D2 | DB doc-records для cycle-2 + cycle-3 audit-докумов | `doc_create` × 2 | ✅ |
| D3 | docs/system/MASTER.md §6 changelog: добавлены cycle-2 + cycle-3 entries | `Edit` | ✅ |
| D4 | docs/system/MASTER.md §2 структура: добавлены 2026-05-06 audits + cycle-{1..5} + paperclip roadmap | `Edit` | ✅ |
| D5 | check_stale_refs(cod-doc) повторно — 10/10 VALID | MCP | ✅ |
| D6 | Заведён gap G4 (relative-path link resolver) → backlog item в Cycle 5 | description below | ✅ |

## 3. Findings

### F1 — Link resolver gap (G4): relative-paths не резолвятся

`link_list(docs/system/MASTER)` возвращает 39 markdown-links со статусом
`broken_reason: "document not found: <key>"`, при этом все целевые doc_keys
(например, `docs/system/VISION`, `docs/system/capabilities/context-retrieval`)
**существуют** в БД. Парсер не учитывает директорию source-документа при
резолвинге `[label](relative/path.md)` — в результате link_service ищет
doc_key как `VISION` (без префикса `docs/system/`).

Это та же проблема, что описана в [proposal 15 §1](../../../proposals/15-link-system-and-rendering.md),
но более фундаментальная (не «дыра импорта», а отсутствие relative-resolution
вовсе). PCA-421 в плане paperclip-adoption должен включать этот фикс — добавить
явно в acceptance в Cycle 5 (memory-pattern: расширить scope F-задачи через
update_task, не заводить дубль).

### F2 — Doc-record `arch/arch/architecture` — фикстурный реликт

`/Users/dakh/Git/cod-doc/arch/arch/architecture.md` (commit e51e85f, 2026-04-05):
- Заголовок: «🏗️ Архитектура приложения: integration-test» (фикстурное имя)
- Layout: `arch/arch/` — двойной префикс, нелогичен; настоящая arch-доковка живёт в `/arch/architecture.md`.
- DB doc-record: `doc:arch_arch_architecture`, status `draft`, drift `stale_export`.

**Решение:** не удалять без явного запроса (карпатовский принцип «измерь
дважды, режь раз»). Зафиксировать как backlog `PCA-FIX-001` (TODO в Cycle 5)
с предложением (a) `git rm arch/arch/architecture.md`, (b) doc-record
deprecate→delete или rename'ить doc_key в `_legacy_fixture/arch_architecture`.

### F3 — Стабильное расхождение doc_drift у MASTER-документов

`docs/system/MASTER` и root `MASTER` имеют `status=stale_export` после
циклов 1-3 (мы редактировали файлы напрямую через Edit, не через
`projection_service.import_document` или `doc.patch_section`).

**Это нормально** для текущей архитектуры:
- Source of truth — БД для большинства docs.
- Но MASTER-документы исторически правились on-disk; reconciliation-flow
  есть в `capabilities/doc-evolution.md`, но автоматического resync
  после Edit'а не запускается.

**Решение:** в Cycle 5 запустить `import_document` для обоих MASTER-files
чтобы синхронизировать DB body (либо принять текущую дельту как известное
состояние и обновить в плановом порядке).

### F4 — DB plan_ready показывает блокированные tasks как ready

Описано в Cycle-2 G2 / Cycle-3 PCA-902: `task_create.blocked_by` не
персистится. В Cycle 4 проверено эмпирически: `plan_ready(paperclip-adoption-task-plan)`
возвращает PCA-002 (blocked_by=PCA-001) среди ready, что некорректно.

PCA-902 уже в Section F (priority `critical`).

## 4. Plan health

```
plan_progress(paperclip-adoption-task-plan)
→ total: 43, sections: A=17, B=6, C=7, D=3, E=7, F=3
plan_audit
→ issues_total: 0, cycles: [], done_with_unfinished_blocks: []
→ critical_path_length: 1 (отражает G2 — реальная глубина не известна до фикса)

check_stale_refs(cod-doc)
→ 10/10 VALID на всех гибридных-refs в /MASTER.md
```

## 5. Acceptance for cycle 4

- [x] Cycle-2 + Cycle-3 audit-отчёты зарегистрированы как doc-records.
- [x] `docs/system/MASTER.md` §6 имеет changelog-entries для cycles 1, 2, 3.
- [x] `docs/system/MASTER.md` §2 структура отражает текущий снапшот audit/
      и roadmap/.
- [x] `check_stale_refs` 10/10 VALID после правок.
- [x] Найденные gap'ы (F1/F2/F3/F4) либо привязаны к существующим backlog-
      задачам, либо выносятся в Cycle-5 backlog updates.

## 6. Out of cycle (handed off → Cycle 5)

1. **Расширить PCA-421 acceptance** добавлением relative-path resolver
   (F1/G4) через `update_task`.
2. **Создать backlog-задачу `PCA-FIX-001`** для уборки фикстурного
   `arch/arch/architecture.md` (F2).
3. **Memory updates** (если возникнет новый паттерн):
   - кейс «MCP-фасад принимает поле, но не персистит» — feedback-pattern.
   - кейс «edit-in-place vs DB-import drift» — стандартный handoff.
4. **Финальный close-out audit** Cycle 5 со сводкой 5 циклов и
   self-check.
