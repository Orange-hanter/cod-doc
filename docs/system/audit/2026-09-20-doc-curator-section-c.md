---
type: audit-report
scope: doc-curator-section-c
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-09-20
last_updated: 2026-09-26
related_docs:
  - ../../../proposals/25-doc-curator-agent.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
  - ../roadmap/ROADMAP.md
  - 2026-09-19-doc-curator-section-b.md
audience: [contributors, agents]
---

# Audit — Plan `doc-curator-2026-09`, Section C closure («Search quality»)

> **Контекст.** [RFC 25](../../../proposals/25-doc-curator-agent.md) §6 ставит
> секцию C планом после свопа профиля `agent` (Section B, закрыта
> [2026-09-19](2026-09-19-doc-curator-section-b.md)): «бюджет, пустой индекс,
> кросс-проект как SYM-011». Секция C плана `doc-curator-2026-09` — 6 задач:
> CUR-010 (типизированная ошибка отсутствия индекса), CUR-011 (per-kind
> лимит + вес заголовка в bm25), CUR-012 (инкрементальный FTS-upsert из
> write-path), CUR-013 (кросс-проектный поиск в hub-БД), CUR-014 (честный
> token_budget в `context_get`), CUR-015 (этот отчёт — аудит закрытия и
> сверка со STO-015/SYM-011).

## 1. TL;DR

CUR-010…014 реализованы кодом, тестами и смержены в `main` (PR #71, #65, #73,
#72, #61) — в БД помечены `done`. CUR-015 закрывает разрыв между кодом и
прозой ровно так же, как CUR-009 закрывал его для секции B: audit-report,
сверка ROADMAP/RFC 25/RFC 22, регистрация в реестрах; закрытие самой задачи
в БД отложено на пост-мерж проход (см. §4). Section C убрала главный дефект,
найденный аудитом секции B (**F2**: живой FTS-индекс проекта cod-doc держал
63 строки типа `doc` при 442 задачах, `ensure_index` его не чинил — сработка
только на полностью пустом индексе): write-path задач/историй/ADR/findings
теперь пишет в индекс инкрементально при каждой мутации (CUR-012). Прямая
проверка на живой БД (`sqlite3 -readonly .cod-doc/state.db "select kind,
count(*) from db_search_idx group by kind"`, только чтение) даёт
**doc 170 / task 442 / story 31 / adr 12** — все канонические сущности
проиндексированы, F2 закрыт. Кросс-проектный поиск реализован для MCP
`ctx_search(projects=...)` и CLI `cod-doc search --projects`/`cod-doc ctx
search --projects` (CUR-013, RFC 22 §3.6) — но не для REST: `SearchHit` в
`cod_doc/api/v1/schemas.py` не несёт поле `project`, и `GET
/api/v1/search` не принимает `projects` вовсе (см. **F4**). Этот и
остальные пункты кросс-проектного скоупа (`[[doc:slug:key]]`, Chroma-фильтр
B12, `agent_pick --projects`) остаются в STO-015/SYM-011 — план
`adoption-2026-08`, не `doc-curator-2026-09` (§6).

## 2. Deliverables

| # | Задача | PR | sha мержа | Содержание |
|---|--------|----|-----------|------------|
| 1 | CUR-010 | [#71](https://github.com/Orange-hanter/cod-doc/pull/71) | `ecb7982` | `SearchIndexMissing(RuntimeError)` вместо сырого `OperationalError` в `search`/`ensure_index`/`reindex_all`: CLI `search`/`ctx search` — `click.ClickException`, exit 1 без трейса; REST — HTTP 503; web — понятное сообщение вместо 500; MCP `ctx_search` не менялся — исключение уходит как есть |
| 2 | CUR-011 | [#65](https://github.com/Orange-hanter/cod-doc/pull/65) | `53d5fec` | Per-kind `LIMIT` через `ROW_NUMBER() OVER (PARTITION BY kind ORDER BY score)` внутри CTE (общий `LIMIT` до группировки вытеснял task/adr-хиты doc-тяжёлым корпусом); все пять именованных весов `bm25(idx, kind, ref, project_id, title, body)` — FTS5 резервирует позицию весов и под `UNINDEXED`-колонку, `bm25(idx, 10.0, 1.0)` легло бы на `kind`/`ref`, а не `title`/`body`; `ctx search --scope/--limit`, `finding` добавлен в `--scope` CLI/web |
| 3 | CUR-012 | [#73](https://github.com/Orange-hanter/cod-doc/pull/73) | `2830e82` | Инкрементальный FTS-upsert из write-path `task_service`/`story_service`/`adr_service`/`finding_service` (`upsert_entity`/`delete_entity`, payload-функции по kind общие с `reindex_all`, так что полный ребилд и инкремент байт-идентичны); хук best-effort глотает только `SearchIndexMissing` (WARNING в лог, остальное падает вместе с мутацией); dismissed findings исключены из индекса (`_FINDING_UNINDEXED_STATUSES`) в обоих путях |
| 4 | CUR-013 | [#72](https://github.com/Orange-hanter/cod-doc/pull/72) | `5a0674d` | `search(..., project_ids=[...])` → `WHERE project_id IN (...)` по одному FTS5-индексу (без федерации — bm25 относителен корпусу, RFC 22 §2.2); `project` (слаг владельца) в каждом хите; `resolve_cross_project_ids` — общий `db_url` обязателен, иначе `ValueError`, а не тихая пустая выдача; MCP `ctx_search(projects=[...])` + `meta.index_by_project`; CLI `--projects` у `search`/`ctx search` |
| 5 | CUR-014 | [#61](https://github.com/Orange-hanter/cod-doc/pull/61) | `ee2ddfc` | `context_get` честно считает description/acceptance и L2-обогащение в `token_budget` через единый `_Budget` (take/afford/charge); `effective_depth` понижается до L1 (L3 → L2) при переполнении вместо эха `depth`; гарантия `tokens_used <= token_budget` |
| 6 | CUR-015 | этот PR | — | Аудит закрытия секции C (этот отчёт), сверка ROADMAP/RFC 25/RFC 22 с STO-015/SYM-011, регистрация в реестрах хэшей и MASTER. Закрытие задачи в БД — пост-мерж (см. §4, тот же протокол, что и у CUR-009) |

## 3. Findings

- **F1 (CUR-011, устранено этим же PR).** `db_search_idx` (миграция
  `20260515_0023_fts5_index.py`) — 5 колонок: `kind, ref, project_id
  UNINDEXED, title, body`. FTS5 резервирует позиционный слот в
  `bm25(table, w1, w2, ...)` и под `UNINDEXED`-колонки, хотя они не несут
  токенов: `bm25(db_search_idx, 10.0, 1.0)` реально взвесил бы `kind` и
  `ref`, а не `title`/`body`, как предполагал бы двухаргументный вызов.
  CUR-011 передаёт все пять весов (`w_kind=1.0, w_ref=1.0, w_project=1.0,
  w_title=10.0, w_body=1.0`, `cod_doc/services/search_service.py:115-127`) —
  задокументированная ловушка, не баг в проверке.
- **F2 (тестовая гигиена, обнаружено CUR-013, не продакшн-баг).**
  `tests/cli/test_search_cross_project.py::test_ctx_search_projects_returns_both`
  проверяет `--json` в hub-режиме через `result.stdout`, не через
  `result.output`: проверка схемы hub-БД (`run_alembic("upgrade", "head",
  ...)`) логирует alembic-плагины в stderr, а `CliRunner.output` — склейка
  stdout+stderr, так что валидный JSON на stdout ломается примесью лога.
  Комментарий в тесте фиксирует это явно. Не путать с `ADO-176`
  (`test_json_output_is_parseable.py`) — тот про rich-разметку в самом
  выводе; это — про то, какой атрибут `Result` читает тест в hub-сценариях.
  Стоит явной заметкой в коде, отдельной задачи не требует.
- **F3 (CUR-012, два самостоятельных решения в одном PR).** (a) Write-path
  хуки (`index_task`/`index_story`/`index_adr`/`index_finding`) —
  **best-effort**: глотается только `SearchIndexMissing` (лог WARNING,
  чинится `cod-doc search --reindex` после `alembic upgrade head`), любая
  другая ошибка падает и откатывает мутацию вместе с ней — индекс
  вторичен, но не ценой тихой порчи первичных данных. (b) `dismissed`
  finding исключается из индекса и при полном `reindex_all`, и при
  инкрементальном `index_finding` (`_FINDING_UNINDEXED_STATUSES`,
  `_finding_is_indexable`) — отклонённый шум не должен возвращаться в
  каждую выдачу `ctx_search`. Оба решения расписаны в докстринге модуля и
  проверены тестом `test_incremental_rows_match_a_full_reindex`.
- **F4 (блокирует полное закрытие STO-015/SYM-011, не входит в скоуп
  Section C).** `SearchHit` в `cod_doc/api/v1/schemas.py:61` — только
  `ref, title, snippet, score`, без `project`; `GET
  /projects/{slug}/search` (`cod_doc/api/v1/routes.py:150-165`) не
  принимает `projects`/`project_ids` вообще. `search_service.search`
  честно кладёт `project` в каждый хит (см. Deliverables #4), но REST его
  теряет молча — слаг владельца по HTTP не выходит, кросс-проектный поиск
  недоступен внешним потребителям API v1 (ZAIrgRush/Orakul). Явно вынесено
  вне скоупа в теле коммита CUR-013 («Вне скоупа (остаётся в SYM-011):
  … `GET /api/v1/search` — api/v1 отдаёт хиты через `SearchHit`, где
  лишний ключ `project` просто игнорируется»). Задача — STO-015.
- **F5 (F2 секции B, закрыто CUR-012).** Аудит секции B (2026-09-19)
  зафиксировал: `ensure_index` (CUR-007) реиндексирует только полностью
  пустой индекс, а непустой-но-неполный (63 строки `doc` при 442 задачах)
  оставляет как есть — куратор получал частичную, вводящую в заблуждение
  выдачу по `task`/`adr`/`story`. CUR-012 закрывает это не reindex-сроком,
  а write-path хуками: индекс поддерживается инкрементально на каждой
  мутации, а не только batch-реиндексом. Прямая проверка на живой БД
  (см. §1 TL;DR) подтверждает: **doc 170 / task 442 / story 31 / adr 12** —
  полное покрытие канонических сущностей, F2 закрыт.

## 4. Plan health

| Секция | Задачи | Done | Статус |
|---|---|---|---|
| A — Policy lock | 3 | 3/3 | ✅ done |
| B — Agent surface swap | 6 | 6/6 | ✅ done, аудит [2026-09-19-doc-curator-section-b.md](2026-09-19-doc-curator-section-b.md) |
| C — Search quality | 6 | 5/6 в БД (CUR-010…014); CUR-015 — код/аудит готовы в этом PR, закрытие в БД отложено до мержа в `main` (тот же протокол `task_complete` с `commit_sha`, что и у CUR-009) | 🟡 код и документация готовы, БД-закрытие после мержа |
| D — Curator loop | 3 | 2/3 | ⏳ в работе — `CUR-017` (daemon idle без задач, автогенерация из MASTER удалена, PR #49) `done`; `CUR-016` (`curator_next` MCP/CLI, своп `ctx_docs` → `curator_next` в `AGENT_TOOLS`, [PR #74](https://github.com/Orange-hanter/cod-doc/pull/74), sha `70e88d9`) `done` — смержен в `main` во время работы над этим аудитом, после rebase; `CUR-018` (докс/ROADMAP/аудит секции D) `in_progress` |

## 5. Acceptance (по критериям задачи CUR-015)

| Критерий | Итог |
|---|---|
| Аудит зарегистрирован в БД и MASTER, drift чист | 🟡 зарегистрирован в `docs/system/MASTER.md` §5/§6 и корневом `MASTER.md` (markdown, хэши пересчитаны — см. тело PR); регистрация в БД проекта cod-doc (`doc import`) откладывается на пост-мерж проход в основном чекауте — worktree сознательно не пишет в общую БД (см. список файлов для импорта в теле PR) |
| ROADMAP трек RFC 25 секция C помечена закрытой | ✅ `docs/system/roadmap/ROADMAP.md` — таблица секций A/B/C/D трека RFC 25 получила колонку статуса, секция C помечена закрытой со ссылкой на этот отчёт |
| Зелёный CI | Локальный полный гейт (ruff/ruff format/mypy/pytest) — см. тело PR; GitHub CI ждётся после `gh pr create` |

## 6. Out of cycle

- **Секция D (Curator loop).** `CUR-016` (`curator_next`-агрегатор, RFC 25
  §3.5) смержен в `main` ([PR #74](https://github.com/Orange-hanter/cod-doc/pull/74),
  `70e88d9`) — профиль `agent` теперь отдаёт `curator_next` вместо `ctx_docs`
  (`agent_capabilities`/`curator_next`/`ctx_search`/`ctx_drift`/
  `context_get`/`agent_report`); `CUR-018` (докс/ROADMAP/аудит закрытия
  секции D) — `in_progress`, не завершена.
- **Остаток STO-015 / SYM-011** (план `adoption-2026-08`, не
  `doc-curator-2026-09` — кросс-проектный поиск куратора закрыт здесь
  CUR-013, но полная Фаза 5 RFC 22 §3.6 шире):
  - `[[doc:slug:key]]` — резолв ссылок в пределах проектов с общим
    `db_url` (`link_service/__init__.py:22-28`, отложенная заметка).
  - Фикс B12 — `context_service._enrich_l3_semantic` без project-фильтра
    Chroma (утечка → намеренный кросс-проектный L3-режим).
  - `GET /api/v1/search` с `projects` — F4 этого отчёта: `SearchHit`
    молча отбрасывает `project`, эндпоинт не принимает `projects`.
  - `agent_pick --projects` — сознательно **не** делается: RFC 25 держит
    `agent_pick` task-centric инструментом coding-агента на
    `standard`/`full`, кросс-проектность в куратора `agent` туда не
    заводится (зафиксировано и в корневом `MASTER.md`).
