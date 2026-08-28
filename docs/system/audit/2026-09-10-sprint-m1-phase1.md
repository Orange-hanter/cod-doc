---
type: audit-report
scope: sprint-m1-phase1
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-28
related_docs:
  - ../roadmap/ROADMAP.md
  - 2026-07-29-state-of-the-project.md
  - ../../adoption-playbook.md
audience: [contributors, agents]
---

# Audit — Спринт 2026-08-27 → 2026-09-10 «M1 + Фаза 1»

> **Контекст.** Первый спринт по новой [ROADMAP.md](../roadmap/ROADMAP.md)
> (план `adoption-2026-08`). Три гола: G1 — M1 «Пилот работает»,
> G2 — SYM-005/006 (shared hub + findings pipeline), G3 — friction-лог и
> routines на пилотах. Отчёт закрывает спринт: все гола и стретч подтверждены
> на 2026-08-28.

## TL;DR

**Все три гола закрыты досрочно (2026-08-28), стретч SYM-006C/D тоже взят.**
M1 достигнут на двух пилотах (ZAIrgRush 31 док, Orakul 405 доков), findings
pipeline работает end-to-end (ingest → dedup → promote → stability), routines
файрятся вне cod-doc. Попутно закрыт главный friction M2 (projection_hash на
импорте, ADO-023) и два найденных по ходу бага хэширования. Полный suite:
**1528 passed**, drift 121/121 in_sync.

## 1. Deliverables

### G1 — M1 «Пилот работает»

- **ADO-016** (ZAIrgRush, 31 док) и **ADO-017** (Orakul, 405 доков) — done,
  оговорки задокументированы в reason закрытия. ROADMAP чекбоксы: `24ca045`.
- Merge worktree-ветки: `1a66aaa` (ADO-015 типы документов, SYM-004
  `--exclude`, ADO-022 projection fidelity, миграция 0026).

### G2 — Shared hub + findings pipeline

| Задача | Коммит | Содержание |
|---|---|---|
| SYM-005A | `aca5028` | ProjectEntry.db_url, `db_for_entry` со сверкой alembic_version, `cod-doc hub init` |
| SYM-005B | `7e532d0` | Миграция `0027_shared_hub`: UNIQUE(project_id, task_id), явный downgrade |
| SYM-005C | `d3f3255` | Миграция `0028_findings` + ORM + FTS scope «finding» |
| SYM-005D | `db627a3` | `finding_service/`: fingerprint/dedup/promote, atomic upsert, `finding.promoted` |
| SYM-006A | `78c9678` | `ingest_service/`: registry + адаптеры ai_review v1 / zairgrush_findings / zairgrush_tasks |
| SYM-006B | `d363169` | CLI `cod-doc ingest` + `cod-doc finding stability`, dry-run, 12 cli-тестов |

### G3 — Friction-лог + routines на пилотах

- **ADO-005** — 14 записей friction-лога (≥5 по acceptance); top-1 вынесен в
  ADO-023.
- **ADO-007** — routine `doc_drift_daily` на ZAIrgRush, 2 успешных прогона в
  `routine_run`; БД пилота проапгрейжена 0026→0028.

### Сверх плана (по ходу спринта)

- **ADO-023** (M2 top-1, bug high): bulk import не проставлял
  projection_hash → весь корпус в stale_export. Фикс `63c68d4` +
  регрессионный тест «import → drift in_sync».
- **ADO-025**: `remove_dependency` в service/MCP/CLI (`2bdb1f9`) — gap
  найден при закрытии ADO-007 (блокер снимали прямым SQL).
- **ADO-026** (bug): web bulk apply и scan_folder хэшировали первые 4 КБ →
  ложный edited_in_place для >4 КБ. Фикс `f18c8d2` (full-file sha256).
- **Стретч SYM-006C/D**: API v1 (`ad413bf` — findings/context/search,
  Bearer-gate контракт RFC 22 §3.3 задокументирован) + MCP
  finding_*/ctx_* (`1d17329` — standard=107, full=111, minimal/agent
  побайтово прежние).
- **ADO-024**: CLI `cod-doc routine list/tick/run` (`d707307`) + OS-cron на
  пилоте (`*/15 * * * * … routine tick -p zairgrush`). Реальный cron-fire
  подтверждён: probe-рутина `*/2` сработала на тике 16:30
  (`routine_run.status=done`), после чего удалена. Первый тик (16:15) упал
  с `Project not found` — пилот не был в глобальном реестре (F4), лечится
  `project add`.
- Reconcile drift: миграции 0026–0028 применены к рабочей БД (была на
  0025), 35 stale_export документов re-export'нуты, drift 121/121 in_sync.

## 2. Инженерное здоровье на 2026-08-28

| Проверка | Результат |
|---|---|
| `pytest tests/` | **1528 passed**, 0 failed (426 s) |
| `ruff check cod_doc/ tests/` | All checks passed |
| `ruff format --check` | 484 файла, чисто |
| `mypy cod_doc/` | Success, 313 source files |
| `doc drift -p cod-doc --all` | 121 in_sync, 0 stale / edited / missing |

## 3. Findings

- **F1. Миграции не применялись к рабочей БД.** 0026–0028 были написаны и
  протестированы, но `.cod-doc/state.db` оставалась на 0025 — обнаружилось
  только по отказам export guard'а. Нужен чек «alembic current == heads» в
  верификацию перед hand-off (кандидат в routine или pre-commit).
- **F2. tick() трактует cron как интервал.** `47 9 * * *` деградирует в
  «раз в час»; распознаются только `*/N`, `0 */N`, `0 0 * * *`. Для
  ADO-024 cron рутины заменён на `0 0 * * *`; полноценный cron-парсер —
  отдельная задача, если понадобится точное время суток.
- **F3. Single-file web upload не пишет content_sha256_head** — мягкий gap,
  drift показывает missing/stale вместо in_sync. Не блокер.
- **F4. Глобальный реестр `~/.cod-doc/config.yaml` не знал пилот** — CLI
  резолвил проект только из cwd до `project add`. Стоит отразить в
  onboarding-skill, что регистрация в реестре — обязательный шаг.
- **F5. `remove_dependency` отсутствовал** в service/MCP/CLI (ADO-025,
  закрыт в спринте).
- **F6. MCP-процесс сессии стареет относительно main** — enum-ошибки на
  свежих типах документов лечились CLI. При работающем dogfood'е MCP-сервер
  стоит перезапускать после merge.

## 4. Acceptance по голам

- **G1** ✓ — оба пилота заведены, plan ready непустой, search находит по
  доменным терминам (критерии M1 из ROADMAP).
- **G2** ✓ — ingest/dedup/promote/stability + API v1 + MCP-тулы; suite
  зелёный.
- **G3** ✓ — friction-лог 14 записей; routine на пилоте отработала
  (run_now ×2) **и подтверждён реальный cron-fire через OS-cron**
  (probe-рутина, тик 16:30, ADO-024).

## 5. Next step (следующий спринт)

- M2 по ROADMAP: оставшиеся friction-пункты из ADO-005 (слоты top-3 не
  выбраны — вернуться к логу).
- F1/F2/F4 → задачи в backlog (план `adoption-2026-08`, секция C/D).
- Bearer-гейт на /api/v1 — активируется при появлении первого удалённого
  caller'а (RFC 22 §3.3).
