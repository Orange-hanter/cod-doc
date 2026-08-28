---
type: audit-report
scope: sprint-m2-feedback-loop
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-28
last_updated: 2026-08-29
related_docs:
  - ../roadmap/ROADMAP.md
  - ../roadmap/sprint-2026-08-28-m2-feedback-loop.md
  - 2026-09-10-sprint-m1-phase1.md
audience: [contributors, agents]
---

# Audit — Спринт 2026-08-28 → 2026-09-11 «M2: обратная связь встроена»

> **Контекст.** Второй спринт по [ROADMAP.md](../roadmap/ROADMAP.md) (план
> `adoption-2026-08`). Гола: G1 — friction top-1+4 (ADO-006), G2 — route
> drift (ADO-011/012), G3 — хвосты аудита F1/F2/F4. Стретч: SYM-007.
> План спринта: [sprint-2026-08-28-m2-feedback-loop.md](../roadmap/sprint-2026-08-28-m2-feedback-loop.md).

## TL;DR

Спринт закрыт досрочно (2026-08-29 при дедлайне 2026-09-11): все три гола и
стретч выполнены, 12 задач done, 10 коммитов в main. Friction-лог пилотов
дошёл до 14 записей за 2 дня (критерий M2 — ≥10); top-1+4 превращены в фичи
и влиты. Инженерное здоровье зелёное: 1562 теста passed, ruff/mypy/drift
чисто.

## 1. Deliverables

### G1 — Friction-находки (ADO-006)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-030 (friction #5) | `fecde21` | Инкрементальный FTS: `search_service.upsert_doc` в `import_or_update_markdown` — search после import без `--reindex`; регрессионный тест |
| ADO-031 (friction #6) | `afa75a9` | CLI `cod-doc doc delete` + bulk (`--path-glob`/`--type`/`--dry-run`/`--yes`); каскад секций/линков/FTS; `doc.deleted` activity event; revisions не трогаем |
| ADO-032 (friction #9) | `17b7137` | `venv` в `_SKIP_DIRS` импортёра и web-сканера |
| ADO-033 (friction #13) | `6cdbe4c` | MASTER.md Context Map — только существующие каталоги; placeholder при пустом проекте |

### G3 — Хвосты аудита F1/F2/F4

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-027 (F1) | `9aaac08` | Routine-чек `alembic_head`: alembic_version рабочей БД vs heads миграций; рутина `alembic_head_daily` зарегистрирована на cod-doc, прогон findings=0 |
| ADO-028 (F2) | `1c66f38` | Полный 5-полевой cron-парсинг (`_cron_next_fire`) в `tick()` вместо интервальной деградации; dom/dow OR-семантика; фолбэк 60 мин для нераспознанного |
| ADO-029 (F4) | `f9a09de` | onboarding-skill: `project add` — обязательный шаг с объяснением последствий (cron/CLI вне cwd) |

### G2 — Route drift (ADO-011/012)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-011 | `635b004` | `web-frontend.md` §3 — полный реестр 87 роутов (авто-генерация из FastAPI), audit-скрипт: drift 0/0 |
| ADO-012 | `cf328e7` | CI advisory-job `web-routes` (прогон audit'а роутов в CI, не блокирует PR); проверен в чистом HOME — для аудита достаточно `project add` |

### Стретч — SYM-007 (ADR-мост ZAIrgRush)

13/13 ADR проекта ZAIrgRush заведены в adr-систему проекта `zairgrush`;
оформлены supersede-связи ADR-004→ADR-003 и ADR-008→ADR-007. Источник
`decisions.jsonl` перенесён в репо владельца
(`ZAIrgRush/experiments/decisions.jsonl`) — с одобрения владельца.

## 2. Инженерное здоровье (на конец спринта)

| Проверка | Результат |
|---|---|
| `pytest tests/` | 1562 passed |
| `ruff check` / `format --check` | чисто (489 файлов) |
| `mypy cod_doc/` | чисто (315 файлов) |
| `doc drift -p cod-doc --all` | 123/123 in_sync |

## 3. Findings

- **F1 (friction #8, backlog).** `--dry-run` обрезает список кандидатов до
  50 строк — для корпуса 400+ документов решение по exclude принимать не по
  чему. Нужен `--limit`/полный вывод в файл.
- **F2 (friction #10, backlog, bug).** Импортированные `*.txt` получают
  битый `path` в БД → drift показывает их как `missing`. Импортёр не
  нормализует path для не-md файлов.
- **F3 (friction #11, docs).** Скрытые каталоги (`.cursor`, `.claude`, …)
  молча скипаются импортом — поведение разумное, но недокументировано и не
  видно в dry-run.
- **F4 (friction #14, backlog).** Русскоязычный frontmatter без ключа
  `type:` (`diataxis`/`quadrant` в Orakul) → все документы получают тип
  `module-spec`. Кандидат на маппинг в DocumentType (аналог ADO-015).
- **F5 (перенос).** F3 аудита M1 (single-file upload не обновляет hash) в
  спринт не брали — остаётся в backlog.
- **F6 (процесс).** Два инцидента «устаревшая ссылка / забытый реимпорт»:
  web-страница routines ссылалась на удалённую `_cron_interval_minutes`
  (поймано mypy на финальном прогоне), после правок трекаемых md дважды
  забывался `doc import`. Правило: финальный прогон mypy + drift —
  обязательный шаг перед отчётом, а не опция.
- **F7 (низкий).** Парсер §3 web-frontend.md принимает ровно один
  `METHOD /path` на строку таблицы — ограничение задокументировать в
  шаблоне секции.

## 4. Acceptance по голам

| Гол | Критерий | Итог |
|---|---|---|
| G1 — friction top-1+4 (ADO-006) | ≥4 находки из лога превращены в фичи и влиты | ✅ ADO-030/031/032/033 в main |
| G2 — route drift (ADO-011/012) | Реестр роутов с нулевым drift + CI-контроль | ✅ 87 роутов, 0/0, advisory-job |
| G3 — хвосты F1/F2/F4 | Все три finding'а закрыты кодом/доками | ✅ ADO-027/028/029 в main |
| Стретч — SYM-007 | ADR ZAIrgRush в adr-системе | ✅ 13/13 + 2 supersede |

Критерий M2 по friction-логу (≥10 записей) перевыполнен: 14 записей.

## 5. Next step

- **M3** по [ROADMAP.md](../roadmap/ROADMAP.md): фича по спросу из
  friction-лога — кандидаты F1/F2/F4 настоящего аудита.
- **Стретч-кандидат:** SYM-008 — петля E5-C в ZAIrgRush.
- Остаток friction-лога (#8, #10, #11, #14) — в backlog Section F,
  приоритизация на планировании M3.
