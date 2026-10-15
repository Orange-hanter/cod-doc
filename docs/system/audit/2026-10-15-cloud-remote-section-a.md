---
type: audit-report
scope: cloud-remote-section-a
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-10-15
last_updated: 2026-10-15
related_docs:
  - ../../../proposals/29-cloud-remote-sync.md
  - ../capabilities/remote-sync.md
  - ../DATA_MODEL.md
audience: [contributors, agents]
---

# Audit — Plan `cloud-remote-2026-10`, Section A closure («Идентичность и журнал»)

> **Контекст.** [RFC 29](../../../proposals/29-cloud-remote-sync.md) §11.2,
> секция A: у каждой authored-строки появляется глобальный `uid`, у каждого
> места порядка — синхронизируемый ранг, а каждая запись в authored-данные
> оставляет операцию в журнале `sync_op`. Упаковка журнала в changeset и
> file-remote — секция B, слияние и конфликты — секция C.

## 1. TL;DR

Секция A закрыта: 9/9 задач (CLO-001…009) `done`, код в `main` пятью PR
(#206, #210, #211, #212, #213), CI на всех пяти коммитах слияния зелёный.
Две миграции — `0049_sync_identity` (uid, шесть служебных таблиц) и
`0050_sync_rank` (ранг в 11 местах, снятие четырёх UNIQUE на `position`).
Проверка на копиях всех девяти БД машины (§6): upgrade 0048 → 0050 проходит
везде за 0,5–0,85 с, счётчики сохранны, висячих FK нет, строк без uid нет,
порядок внутри каждого родителя совпал.

Пользователю пока не видно ничего: remote, `push`, `pull` нет, журнал
копится локально. Статус `resolved` — по коду в `main`; рантайм сервисов
получит миграции после `cod-doc update`.

## 2. Задачи и PR

| Задача | Что | PR → коммит в `main` |
|---|---|---|
| CLO-001 | реестр классов `cod_doc/infra/sync_registry.py`, гейт полноты `tests/infra/test_sync_registry_complete.py` | #206 → `8c297ee` |
| CLO-002 | миграция `0049_sync_identity`: `uid` в 39 authored-таблицах, `replica`, `sync_op`, `changeset`, `sync_ref`, `sync_lease`, `entity_alias` | #210 → `69c2aec` |
| CLO-003 | миграция `0050_sync_rank`: `rank` в 10 таблицах + `document.node_rank`, снятие `uq_*_position` у четырёх таблиц | #211 → `92ef2a2` |
| CLO-004 | чтение и выдача порядка через ранг: `cod_doc/infra/rank.py`, `cod_doc/infra/ordering.py`, переписанные аллокаторы | #211 → `92ef2a2` |
| CLO-005 | HLC и своя реплика проекта: `cod_doc/infra/hlc.py` | #212 → `bf1c72e` |
| CLO-006 | хук захвата `cod_doc/infra/sync_capture.py` на фабрике сессий | #212 → `bf1c72e` |
| CLO-009 | покрытие: каждая write-функция из спек паритета пишет `sync_op` | #212 → `bf1c72e` |
| CLO-007 | `set_steps` — ORM-дифф с сохранением uid, `ingest_findings` — с `record`, `state` подсказки — ORM | #213 → `6ebf709` |
| CLO-008 | AST-гейт `tests/infra/test_sync_capture_no_bulk_authored.py`, allowlist пуст | #213 → `6ebf709` |

`completed_commit` у CLO-001 — `9e8064f`, коммит ветки до squash-мержа; в
`main` он вошёл как `8c297ee`.

## 3. Что построено

- **Реестр.** 65 таблиц моделей: 42 authored, 2 journal, 16 derived, 5 local
  (на момент #206; шесть служебных таблиц 0049 добавились как local). Колонки,
  которые уезжают, перечислены явно (`SHIPPED_COLUMNS`): новая колонка не
  уезжает, пока ей не дали класс, и гейт не даёт оставить её без решения.
  Restricted-документ и всё, что на него ссылается, абсолютные машинные пути,
  `activity_event.payload`, git-автор коммита — fail-closed local.
- **Идентичность.** uid — `uuid7` у обычной строки, `uuid5` от
  `[таблица, *ключ]` с uid концов у ребра; тег-таблицы с составным PK колонки не
  получают. NOT NULL обеспечивает хук, а не ограничение (DATA_MODEL §3.18.1).
- **Порядок.** Дробный ранг base-36; порядок показа — `(rank, uid, row_id)`.
  Вставка между соседями пишет один ранг. `position` стал local: у одних таблиц
  это индекс, пересчитываемый из ранга, у других — адрес-счётчик
  (DATA_MODEL §3.18.2).
- **Журнал.** Хук вешается `make_session_factory` на каждую фабрику:
  `before_flush` назначает uid, `after_flush` собирает операции, `before_commit`
  корневой транзакции пишет их с HLC своей реплики и автором из
  `activity_service.emit` / `revision_service.write`. Операцию, которую нельзя
  построить корректно, хук не пропускает, а роняет транзакцию (`SYNC-001`).
  Пишется всегда, без remote.

## 4. DoD RFC 29 §11.1

Пункты задачи: 1 — тест, падавший до правки; 2 — таблица в реестре, гейт
CLO-001; 3 — запись через `Session`/`record`, гейт CLO-008; 4 — миграция
симметрична, smoke, тест сохранности; 5 — новый тул/команда на обеих
поверхностях; 6 — коды ошибок из §10.10; 7 — CI зелёный на PR; 8 —
`task_complete` + `task_release`.

| Задача | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|
| CLO-001 | ✅ 39 + 24 + 19 тестов-регрессий security review падали до правок | ✅ сам гейт | — | — | — | — | ✅ | ✅ |
| CLO-002 | ✅ `test_migration_0049_preserves_data.py` падал без ревизии | ✅ шесть таблиц — local | — | ✅ upgrade/downgrade/upgrade на копии живой БД | — | — | ✅ | ✅ |
| CLO-003 | ✅ `test_migration_0050_sync_rank.py`; тай-брейк `(position, row_id)` ронял его на `doc_node` | ✅ `rank_column` сверяется со схемой | — | ✅ smoke upgrade → downgrade → upgrade | — | — | ✅ | ✅ |
| CLO-004 | ✅ `test_rank_allocators.py`: 6 из 7 падали без переписанных сервисов | — | ✅ | — | — | — | ✅ | ✅ |
| CLO-005 | ✅ `test_hlc.py` | ✅ | — | — | — | — | ✅ | ✅ |
| CLO-006 | ✅ `test_sync_capture.py`, включая регрессию на 1000 фабрик | ✅ | ✅ | — | — | ✅ `SYNC-001` | ✅ | ✅ |
| CLO-007 | ✅ `set_steps`-тесты падали: uid шага менялся | — | ✅ три `xfail(strict)` сняты | — | поведение тулов не изменилось | — | ✅ | ✅ |
| CLO-008 | ✅ сканер на `main` до CLO-007 давал 4 нарушения | — | ✅ сам гейт | — | — | — | ✅ | ✅ |
| CLO-009 | ✅ | — | ✅ | — | — | — | ✅ | ✅ |

Пункт 5 к секции A не относится: новых тулов и команд нет.

**Секция.** Все 9 задач `done`; CI на `main` зелёный (`gh run list --branch
main`, коммиты `8c297ee`…`6ebf709`). Сценарии секции: SCN-088 («каждая запись в
authored-данные попадает в журнал») покрыт `test_sync_capture.py` и
`test_sync_capture_write_paths.py`. SCN-093 («параллельные вставки шагов
сливаются по rank без конфликта») покрыт пока только однорепличной половиной —
вставка шага пишет один ранг (`test_rank_allocators.py`); слияние двух реплик
появится в CLO-018 (секция C). Оба сценария остаются `draft`.

## 5. Решения, отступающие от RFC

| # | RFC | Сделано | Почему | PR |
|---|---|---|---|---|
| D1 | 64 таблицы | 65 | `link_suggestion` попадает в `Base.metadata` лениво; гейт импортирует весь пакет `models` | #206 |
| D2 | restricted: только `row_is_local` для документа | fail-closed по строкам и колонкам: перечень разрешённых меток, `DOC_REFS` по каждой ссылке на документ/секцию, `withheld_columns`, машинные пути и `activity_event.payload` — local | четыре раунда security review нашли fail-open, утечку путей, parser differential и регрессию | #206 |
| D3 | uid ребра `uuid5(NS, kind ‖ uid концов ‖ relation)` | `uuid5(NS, json([таблица, *ключ]))` с uid концов | разделитель внутри значения склеил бы его с соседним; детерминизм — относительно uid концов, между независимо мигрированными БД uid ребра разный | #210 |
| D4 | `sync_op.replica_id` (§3.4) | `replica_uid` (§10.1) | контракт §10.1 приоритетнее описания | #210 |
| D5 | дубли рёбер не оговорены | миграция 0049 отказывает до первого DDL и называет таблицу; строки не удаляет | у `story_link` нет UNIQUE на ключе; на девяти БД машины дублей нет (§6) | #210 |
| D6 | тай-брейк бэкфилла `(position, row_id)` | тай-брейк, которым читал код до 0050: `node_key`, `key`, `scenario_id`; у `document` — `node_position NULLS LAST, doc_key` | иначе порядок показа менялся бы при ничьих по `position` | #211 |
| D7 | `position` пересчитывается из ранга | у `story_acceptance`, `open_question_option`, `adr_diagram` это адрес, append-счётчик; у `doc_node`, `story_section` — advisory | `<story_id>#<position>` в ссылках сценариев и `set_criterion_met` адресуют по `position` | #211 |
| D8 | ранг — строка | base-36 без заглавных | сортировка одинакова в SQLite `BINARY`, Python и PostgreSQL `en_US` | #211 |
| D9 | хук на классе `Session` (§3.4) | на конкретной фабрике (`install` из `make_session_factory`) | голый `Session(engine)` в тестах не захватывается случайно | #212 |
| D10 | `before_flush` собирает операции | `before_flush` — uid, `after_flush` — операции | до flush у новой строки нет FK, проставленных через relationship | #212 |
| D11 | ключ FK в `fields` — имя таблицы (§3.3) | имя колонки | у рёбер два FK на одну таблицу | #212 |
| D12 | — | `record()` требует `project_id`; `uid` в `fields` не пишется; подряд идущие upsert'ы сущности склеиваются | `sync_op.project_id NOT NULL`; `uid` = `entity_uid` | #212 |
| D13 | — | операцию, которую нельзя построить, хук не пропускает, а роняет транзакцию (`SYNC-001`) | security review: пропуск с предупреждением — fail-open, расхождение реплик без следа | #212 |
| D14 | — | часы засеваются из журнала один раз на процесс; два процесса одной реплики могут выдать одинаковый HLC, порядок между ними задаёт ULID `op_id` | без межпроцессного замка на каждую запись | #212 |
| D15 | `state` подсказки — ORM | `update_suggestion_state` получил обязательный `author`; автор ingest — `system:ingest:<source>`, автопринятия — `system:link-suggest` | без автора операция ушла бы как `system:unknown` | #213 |

## 6. Проверка на копиях всех БД машины

2026-10-15, ветка от `6ebf709`. Каждая БД из `~/.cod-doc/config.yaml`
скопирована sqlite backup API с источника в режиме `mode=ro`; оригиналы не
трогались, копии удалены после проверки. На копии: `alembic upgrade head` из
рабочего дерева с `COD_DOC_DB_URL` на копию; сверка счётчиков до и после,
`PRAGMA foreign_key_check`, строки без uid по `sync_registry.UID_TABLES`,
порядок внутри каждого родителя в 11 местах: до — `position` и тай-брейк
миграции, после — `rank, uid`.

| Проект | Ревизия | document / section / link / task / dependency / revision / activity_event | Строк с uid | Родителей сверено | upgrade, с | Проблемы |
|---|---|---|---|---|---|---|
| zairgrush | 0048 → 0050 | 109 / 492 / 389 / 108 / 4 / 3320 / 81826 | 885 | 90 | 0,64 | нет |
| cod-doc | 0048 → 0050 | 191 / 1482 / 960 / 651 / 276 / 5215 / 320938 | 4576 | 348 | 0,70 | нет |
| gatewaydemo | 0048 → 0050 | 288 / 1947 / 2832 / 336 / 27 / 3533 / 226927 | 2905 | 259 | 0,77 | нет |
| orakul | 0048 → 0050 | 406 / 2730 / 2779 / 383 / 36 / 4691 / 601308 | 3854 | 395 | 0,72 | нет |
| urukhaimark | 0048 → 0050 | 54 / 366 / 395 / 41 / 2 / 511 / 67719 | 499 | 54 | 0,57 | нет |
| zeuslogic | 0048 → 0050 | 14 / 119 / 30 / 87 / 77 / 415 / 11665 | 522 | 23 | 0,50 | нет |
| ai-reviewer | 0048 → 0050 | 45 / 270 / 304 / 50 / 53 / 659 / 44507 | 579 | 55 | 0,50 | нет |
| Restate | 0048 → 0050 | 293 / 2937 / 2878 / 1061 / 1072 / 9848 / 578254 | 10792 | 663 | 0,85 | нет |
| fashion-ai | 0048 → 0050 | 33 / 186 / 83 / 54 / 31 / 632 / 15582 | 766 | 109 | 0,55 | нет |

Счётчики после upgrade равны счётчикам до во всех девяти БД;
`foreign_key_check` пуст; строк без uid нет. Время — второго прогона: первый
upgrade в процессе стоил 5,05 с на холодном импорте.

**Smoke захвата** на копии cod-doc: через `make_session_factory` и сервисы —
`task_service.update_description` и `doc_service.add_section` от
`agent:precheck-smoke`, один commit (71 мс). В `sync_op` появились три
операции `upsert` с этим автором, одной репликой (`is_self = 1`, заведена
первым коммитом) и возрастающим HLC:

| entity_kind | entity_uid | fields |
|---|---|---|
| `task` | uid задачи | `description`, `last_updated` |
| `section` | uid новой секции | `anchor`, `body`, `content_hash`, `document_id` (= uid документа), `heading`, `level`, `rank` |
| `document` | uid документа | `last_updated` |

`position` секции (local) и `uid` в `fields` не попали; все поля — из
`SHIPPED_COLUMNS` своей таблицы.

## 7. Находки и что переходит в следующие секции

| # | Находка | Куда |
|---|---|---|
| F1 | Адресные `position` (`story_acceptance`, `open_question_option`, `adr_diagram`) после слияния двух реплик могут совпасть: UNIQUE снят, а `<story_id>#<position>` в ссылках сценариев и `set_criterion_met` адресуют по номеру | секция C (слияние, CLO-018) |
| F2 | AI-ревью на #210–#213 не отработало: недельный лимит Ollama Cloud, прогоны «PR Review (AI)» вернули degraded-блок. Код этих PR прошёл только security review и CI | повторить ревью диффа секции после сброса лимита либо в аудите секции B |
| F3 | Authored-таблицы без пути записи в коде (`NO_WRITE_PATH`): `tag`, `document_tag`, `task_tag`, `story_tag`, `module`, `module_dependency`, `module_code`, `external_ref`; `commit_link` пишется только `import_from_git_log`. Хук на них проверен прямой ORM-записью; `test_no_write_path_list_is_current` упадёт, когда появится писатель | писатель такой таблицы обязан пройти тест CLO-009 |
| F4 | `link_service.semantic.update_suggestion_state` получил обязательный `author`; единственный вызывающий — веб (`human:web`). На MCP и CLI этого пути нет, автор там не выверен | секция F (поверхности) или при выставлении пути на MCP/CLI |
| F5 | Накладные расходы захвата — около 0,7 мс на транзакцию (1000 вызовов `update_description`: 0,93 с → 1,65 с на файловой SQLite); время полного прогона тестов в пределах шума | следить на bulk-импорте (`doc import --replace`) в секции B |
| F6 | `sync_op` растёт без remote и без сборки: каждая правка оставляет операцию, упаковки и очистки нет | секция B (changeset, CLO-010/012) |
| F7 | Два процесса одной реплики (MCP-демон и CLI) могут выдать одинаковый HLC (D14); порядок задаёт ULID | проверить в CLO-018 при применении по HLC |

## 8. Итог секции

Секция A — 9/9. Следующий шаг — секция B: формат changeset v1 (CLO-010),
`FileRemote` (CLO-011), `push`/`pull` (CLO-012/013), дайджест (CLO-014) и
поверхности CLI/MCP (CLO-015/016). Перед `cod-doc update` на этой машине
отдельной подготовки БД не нужно: на всех девяти копиях 0049 и 0050 прошли
без отказа.
