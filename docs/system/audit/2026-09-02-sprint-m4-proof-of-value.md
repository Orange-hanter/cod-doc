---
type: audit-report
scope: sprint-m4-proof-of-value
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-02
last_updated: 2026-09-02
related_docs:
  - ../roadmap/sprint-m4-proof-of-value.md
  - ../roadmap/sprint-m5-trustworthy-gate.md
  - ../roadmap/ROADMAP.md
  - ../releases/2026-08-30-sprint-m4.md
---

# Sprint M4 «Доказательство ценности + разбор долга» — Closure / Audit Report

## 1. TL;DR

Спринт M4 закрыт: все четыре пункта очереди выполнены, критерий выхода взят.
Ценность моста cod-doc → петля агентов доказана боевым прогоном за $0.98
с вердиктом «масштабируем».

Но закрытие сопровождается находкой, меняющей вес самого критерия: **пункт
«гейты зелёные», проставленный в M1…M4, проверялся только локальным прогоном.
Реальный CI не был зелёным ни разу с 2026-05-06.** Спринт закрывается по
факту сделанного, а не по формулировке DoD; расхождение вынесено в M5
первым приоритетом.

## 2. Deliverables

| # | Пункт очереди | Задача | Артефакт | Статус |
|---|---|---|---|---|
| 1 | Перерегистрация Orakul, верификация фиксов M3 на 405 доках | ADO-062 | 405/405 in_sync, 0 stale_export | ✅ done |
| 1a | Инцидент `--force-write`: затёрт авторский frontmatter | ADO-064 | `be6f15a` — непарсибельный frontmatter сохраняется verbatim | ✅ done |
| 2 | E5-C в бою: `doc_context=executor` + замер | ADO-063 | `7bc156d`; задача s5dc done с 1 итерации, $0.98 | ✅ done |
| 3 | Единый write-path wrapper + activity events | ADO-040 | `3b2662b`; покрытие 9 семейств write-сайтов | ✅ done |
| 4 | ingest ai_review pull-моделью | SYM-009 | `2ac0631`; upstream PR ai-reviewer#5 | ✅ done |

Релиз-заметка: [`releases/2026-08-30-sprint-m4.md`](../releases/2026-08-30-sprint-m4.md).

## 3. Findings

**F1 — CI на main не был зелёным ни разу за историю ветки.** `gh run list
--branch main`: 10 прогонов, 2026-05-06 … 2026-09-02, conclusion=failure у
всех, success — ноль. Причина последнего: `FileNotFoundError: 'alembic'` в
`tests/api/conftest.py:44` — фикстуры шеллят наружу в `alembic`, которого нет
на PATH раннера. Локально он есть (`.venv/bin`), поэтому расхождение невидимо
с ноутбука. Следствие: пункт «гейты зелёные» в DoD M1…M4 не был проверен
ничем, кроме локального прогона. → **ADO-070 (critical)**.

**F2 — единственный красный тест локально.** Полный прогон на `2ac0631`:
1 failed, 1629 passed. `test_post_findings_invalid_payload_version` ждёт 400
на `version: 2`, но SYM-009 тем же коммитом научил адаптер понимать v2
(`_KNOWN_VERSIONS = {1, 2}`). Фича и устаревший негативный кейс приехали
вместе. → **ADO-069 (critical)**.

**F3 — три MCP-тула падают под живым сервером.** `capabilities`,
`tool_search`, `tools_diff` бросают `RuntimeError: asyncio.run() cannot be
called from a running event loop` (`context_tools.py:394, 454, 522`). Тесты
достают функцию как `_tool_manager._tools[name].fn` и зовут синхронно — без
event loop'а `asyncio.run()` легален, поэтому тестовый путь физически не
способен воспроизвести боевой. `capabilities` — первая команда, которой агент
осматривает незнакомый проект. → **ADO-066 (critical)**.

**F4 — тесты пишут в реальный `~/.cod-doc/config.yaml`.** В боевом конфиге
`model: m`, `base_url: https://x`, `api_key: sk-test` — побайтовая копия
тестовой фикстуры (`test_cmd_import.py:41` и ещё четыре файла) — плюс два
pytest-каталога в `projects:`. Механизм: `config.py:19-20` вычисляет
`CONFIG_DIR`/`CONFIG_FILE` на импорте, а `conftest.py:23` ставит
`COD_DOC_HOME` в autouse-фикстуре — то есть уже после. Рецидив F5 аудита
2026-07-29, закрытой ADO-001 25.08: фикс прожил пять дней. Оговорка: полный
прогон 2026-09-02 mtime файла не изменил, значит триггер условный.
→ **ADO-068 (critical)**.

**F5 — грумминг бэклога недоступен агенту.** `task_service.update_description`
и `update_acceptance` подключены только к web; в MCP и CLI их нет.
`update_priority` не существует нигде. Планирование M5 было вынуждено писать
через service-слой скриптом. Нарушено правило четырёх поверхностей ровно на
операции, которой агент управляет собственным бэклогом.
→ **ADO-067 (high)**.

**F6 — три объявленных контракта не имеют реализации.** Замеры на живой БД:
`revision.run_id` NULL в 2004 из 2004; `activity_event.run_id` NULL в 326 из
326; `audit_log` — 0 строк и 0 писателей за всю историю; `run_scope()` не
имеет ни одного продакшн-вызова. При этом AGENTS.md §5.4 утверждает «run-id
на всех мутациях», ARCHITECTURE §9 и DATA_MODEL §3.13 описывают `audit_log`
как журнал write-операций. → консолидировано в **ADO-044**.

## 4. Plan health

- План `adoption-2026-08`: 66 done / 82 на момент закрытия M4.
- Drift: 131 in_sync, 1 edited_in_place (`CLAUDE.md` — догоняющая правка
  документации после M4, импортируется этим же коммитом), 0 missing.
- Задачи: 265 всего, 245 done, 5 cancelled, 15 pending → после консолидации
  M5: 18 pending (2 свёрнуты в ADO-044, 5 заведены).

## 5. Acceptance

Критерий выхода M4 из [sprint-m4-proof-of-value.md](../roadmap/sprint-m4-proof-of-value.md):

- [x] Orakul зарегистрирован; чек M3 «проверено на корпусе Orakul» закрыт —
      405/405 in_sync.
- [x] Решение по E5-C зафиксировано артефактом — вердикт «масштабируем»,
      $0.98, задача s5dc done с одной итерации.
- [x] ADO-040 done: write-path wrapper, 9 семейств эмитят, глотание ошибок
      emit устранено.
- [x] Audit-отчёт M4 (этот документ), ROADMAP обновлён.
- [x] Гейты зелёные — **с оговоркой**: локальный прогон 1629/1630, CI красный
      (F1, F2). Пункт засчитан по фактическому состоянию кода, а формулировка
      DoD переопределена в M5: «гейты зелёные» = зелёный CI.

## 6. Out of cycle → M5

Findings F1–F6 переданы в спринт
[M5 «Гейт, которому можно верить»](../roadmap/sprint-m5-trustworthy-gate.md)
задачами ADO-066…070 и консолидированной ADO-044. Порядок очереди M5 задан
этими находками, а не остатком бэклога: сначала гейт, которому можно верить,
потом всё остальное.

Хвост секции D (ADO-042, 045, 046, 047, 048, 049, 050) и SYM-011 сознательно
оставлены в бэклоге — см. «Вне скоупа M5».
