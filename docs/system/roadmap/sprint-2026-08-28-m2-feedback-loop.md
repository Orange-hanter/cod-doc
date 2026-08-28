---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-28
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-27-m1-phase1.md
  - ../audit/2026-09-10-sprint-m1-phase1.md
  - ../../adoption-playbook.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
---

# Sprint 2026-08-28 → 2026-09-11 — «M2: обратная связь встроена»

> **Назначение.** Закрыть милстоун M2 «Обратная связь встроена» (ROADMAP):
> top-находки friction-лога починены, дрейф роутов не копится, хвосты аудита
> предыдущего спринта (F1/F2/F4) устранены.
>
> **Не source of truth.** Статусы задач — в БД (план `adoption-2026-08`);
> приоритеты — [ROADMAP.md](ROADMAP.md).

## 0. Ground truth на старт спринта (сверено 2026-08-28)

- Предыдущий спринт закрыт досрочно: audit
  [2026-09-10-sprint-m1-phase1.md](../audit/2026-09-10-sprint-m1-phase1.md),
  suite **1528 passed**, drift 122/122 in_sync, дерево git чистое.
- Friction-лог ADO-005: 14 записей (критерий M2 ≥10 выполнен). Top-1
  (projection_hash на импорте, записи #7/#12) закрыт как **ADO-023**.
- Незакрытые кандидаты лога: #5 (search без --reindex), #6 (нет doc delete),
  #8 (dry-run лимит 50 строк), #9 (venv не в `_SKIP_DIRS`), #10 (битый path
  для `*.txt`), #11 (скрытые каталоги — docs), #13 (шаблон MASTER.md),
  #14 (русскоязычный frontmatter / diataxis-quadrant).
- Хвосты аудита в БД: ADO-027 (F1, high), ADO-028 (F2, medium), ADO-029
  (F4, medium).
- Пилот ZAIrgRush живой: OS-cron `*/15 * * * * cod-doc routine tick -p
  zairgrush`, рутины `approval_stale_default`, `doc_drift_daily`.

## 1. Цели

- **G1 — Friction-находки закрыты (ADO-006).** Решением владельца берём
  **четыре** слота сверх закрытого top-1: #5 → ADO-030, #6 → ADO-031,
  #9 → ADO-032, #13 → ADO-033.
- **G2 — Дрейф не копится.** ADO-011 (`audit --web-routes` → 0 WR-1/WR-2,
  `capabilities/web-frontend.md §3` синхронизирован) + ADO-012 (advisory-шаг
  в CI).
- **G3 — Хвосты аудита.** ADO-027 (F1: чек alembic current==heads), ADO-028
  (F2: cron-парсинг в `tick()`), ADO-029 (F4: `project add` в
  onboarding-skill).
- **Стретч — SYM-007.** ADR-мост ZAIrgRush: 13 ADR → adr-система +
  decisions.jsonl (blocked_by ADO-016 — done, ready).

## 2. Контракты задач

### G1 — friction (новые задачи, секция C)

| ID | Friction | Контракт (acceptance) |
|---|---|---|
| **ADO-030** | #5 | После `import docs` поиск работает без ручного `--reindex`: FTS обновляется инкрементально в транзакции импорта. Фолбэк (если инкремент дорог): импорт печатает явную подсказку. Решение — в task-doc. Регрессионный тест «import → search hit» |
| **ADO-031** | #6 | CLI `cod-doc doc delete <key>` + bulk (`--path-glob`/`--type`), `--dry-run`, каскад секций/линков, activity event (proposal 09), cli-тесты. Поведение каскада — в task-doc до реализации |
| **ADO-032** | #9 | `_SKIP_DIRS` += `venv`, `.venv`; регрессионный тест; заодно проверить покрытие `node_modules`/`__pycache__` |
| **ADO-033** | #13 | Шаблон MASTER.md генерирует секции только по реально существующим каталогам пилота; регенерация на Orakul без ссылок на `/specs/`, `/arch/`, `/models/` |

### G3 — хвосты аудита (уже в БД)

- **ADO-027 (F1):** чек «alembic current == heads» автоматический (рутина или
  шаг верификации); расхождение репортится явно; прогон на cod-doc.
- **ADO-028 (F2):** `tick()` вычисляет next-fire по полному cron-выражению
  (`47 9 * * *` → сутки в 9:47), без деградации в интервал. Тесты: `*/N`,
  `0 */N`, `M H * * *`, невалидные выражения. Без новых зависимостей, если
  обходимо (проверить lockfile на croniter).
- **ADO-029 (F4):** skill `project-onboarding` + runbook явно требуют
  `project add` (регистрация в `~/.cod-doc/config.yaml`) как обязательный шаг.

### G2 — route drift (уже в БД)

- **ADO-011:** `cod-doc audit --web-routes` → 0 WR-1 и 0 WR-2.
- **ADO-012:** `audit --web-routes` в CI как advisory-шаг.

### Вне скоупа

Friction #8/#10/#11/#14 — backlog (оценить в конце спринта по остатку темпа).
SYM-008…011, Трек B, STB-023. F3 (single-file upload hash) — мягкий gap,
не берём.

## 3. Порядок исполнения

1. G1: ADO-032 (дёшево) → ADO-030 → ADO-031 → ADO-033.
2. G3: ADO-027 (high) → ADO-029 → ADO-028.
3. G2: ADO-011 → ADO-012.
4. Стретч: SYM-007.
5. Финал: полный suite, drift-чек, ADO-006 → done, чекбоксы M2 в ROADMAP.md,
   audit-отчёт `docs/system/audit/2026-09-11-sprint-m2-feedback-loop.md`
   (skill `audit-cadence`).

## 4. Риски

- **ADO-030 может оказаться дорогим** (инкрементальный FTS) — фолбэк на
  подсказку зафиксирован в контракте.
- **ADO-031 каскады** — удаление документа с линками/revisions не должно
  ломать историю; поведение описать до реализации.
- **Темп прошлого спринта** (закрыт досрочно) — стретч SYM-007 реален, но
  не провал спринта, если не влезет.

## 5. Definition of Done

- [ ] ADO-030/031/032/033 → done → ADO-006 → done (через `task_complete`
      с `commit_sha`)
- [ ] ADO-027/028/029, ADO-011/012 → done
- [ ] `ruff check`, `ruff format --check`, `mypy cod_doc/`, `pytest` — зелёные
- [ ] Drift 100% in_sync (CLI; MCP-процесс сессии может отставать от main)
- [ ] ROADMAP.md: чекбоксы M2, указатель активного спринта
- [ ] Audit-отчёт спринта в `docs/system/audit/`
