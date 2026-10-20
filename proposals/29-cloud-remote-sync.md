---
type: rfc
---

# 29 — cod-doc remote: облачное хранение и доступ по модели git

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: proposal 09 (activity), proposal 22 (hub), proposal 23 (cloud plane), proposal 26 (write-путь через сервисы)

> **Статус: принят 2026-10-11** (черновик 2026-09-27, сверен со схемой 2026-10-11).
> Решения — ADR-016 (команда через реплики и remote) и ADR-017 (глобальная
> идентичность и захват операций), оба приняты владельцем 2026-10-11. Открытые
> вопросы Q-007…Q-017 решены (§9), блокирующих нет. Контракты — §10, декомпозиция и
> DoD — §11; план `cloud-remote-2026-10` (CLO-001…043): секция A в `todo`, остальное
> в `backlog`. Целевой контракт — capability
> [remote-sync](../docs/system/capabilities/remote-sync.md), сценарии SCN-081…096 —
> [scenarios/remote-sync](../docs/system/scenarios/remote-sync.md).

## 1. Контекст

Состояние проекта — документы, задачи, планы, истории, ADR, ревизии — живёт в одной
файловой SQLite `<project>/.cod-doc/state.db`, и файл этот в `.gitignore`
(`.gitignore:13`). Отсюда три боли:

1. **Нет второй машины.** Ноутбук и десктоп видят разные БД; перенести состояние
   можно только копированием файла, и последняя копия молча затирает другую.
2. **Нет команды.** Второй человек или облачный агент не получает к проекту доступа
   вообще, кроме как через туннель к чужому демону.
3. **Нет бэкапа.** `cod-doc backup|restore` описан в
   [capabilities/backup-and-export.md](../docs/system/capabilities/backup-and-export.md),
   но не реализован; потеря диска = потеря всей истории задач.

RFC 23 решал 2 и 3 центральным Postgres: одна истина в облаке, клиенты работают
онлайн. Он не начат и упирается в пачку Postgres-блокеров STO-018…024. Этот RFC
берёт другую модель — **git**: у каждой машины полная локальная реплика, работа
офлайн, обмен через remote командами `clone` / `pull` / `push`. Remote бывает двух
видов с одним протоколом: «глупый» (S3/MinIO-бакет, без сервера) и «умный»
(`cod-doc serve` с токенами и ролями участников проекта).

**Почему не «просто git по state.db».** SQLite — бинарный файл; git не сливает его,
а выбирает одну сторону целиком. Коммитить дамп SQL не лучше: построчный diff дампа
конфликтует на каждом автоинкременте. Нужна синхронизация на уровне сущностей, а
git — источник модели (DAG коммитов, fast-forward push, объекты по хэшу), а не
транспорт.

## 2. Текущее состояние (проверено по коду 2026-09-27)

| Что | Где | Почему мешает синхронизации |
|---|---|---|
| PK — локальный `row_id INTEGER`, FK — тоже `row_id` | все `cod_doc/infra/models/*.py` | один и тот же `row_id` на двух машинах — разные сущности; переносить FK нельзя |
| Человеческие ID выдаются как `max+1` | `task_service.py:190` `_next_task_id`, `adr_service.py:105` `_next_adr_id` (обёртка `next_adr_id` `:120`), `scenario_service/_internals.py:33` `_next_scenario_id`, `story_service/crud.py:157` `next_story_id`, `question_service/_internals.py:49` `_next_question_id` | две офлайн-реплики гарантированно выдадут один и тот же `ADO-229` разным задачам |
| Позиции выдаются как `max+1` | `document_repo.py:208`, `story_repo.py:182`, `scenario_repo.py:111` | две вставки «в конец» на разных репликах получат одну позицию |
| Глобальные `UNIQUE` вместо проектных | `plan.scope`, `user_story.story_id` (`stories.py:48`), `module.module_id` — STO-018/024 закрыты миграцией `0047_project_scoped_uniques` 2026-10-11; поиски без `project_id` — STO-023 ✅; `task_service.get` — STO-029 ✅; write-пути по `task_id` — STO-030 ✅ | клон проекта в hub, где уже есть такой scope/story_id, упадёт на вставке; предусловие секции A |
| `revision.diff` — unified diff, не состояние | `infra/models/revisions.py:42` | по журналу ревизий сущность не восстановить; для сущностей без ревизий (dependency, tag) журнала нет вовсе |
| Глобальные ID уже есть точечно | `revision.revision_id` ULID (`revisions.py:33`), `activity_event.id` UUID, `finding.finding_uid` uuid7 | журнальные таблицы уже переносимы без перенумерации |
| Единая точка write-пути | `activity_service.py:177` `write_revision_and_emit_event`, `:147` `emit_for_write` (ADO-040) | место есть, но оно видит событие, а не полный набор изменённых полей |
| Presentation не пишет в ORM | гейт `tests/services/test_presentation_no_orm_writes.py` (ADO-208) | все записи идут через сервисы → через `Session` → их можно перехватить в одном месте |
| Bulk-запись мимо unit-of-work, в derived/local | `import_service.py:913`, `:929` (`update(DocumentModel)` хэшей рабочего дерева), `structure_service.py:182-185` (code_*), `repo_index_service.py:206` (repo_file), `link_service/semantic.py:101-102` (score/evidence подсказок), `search_service.py:161/177/280` (FTS) | `before_flush` не видит, но и не должен: эти поля не уезжают |
| Bulk-запись мимо unit-of-work, **в authored** | `scenario_service/steps.py:53-54` (`delete(ScenarioStepModel)` + повторная вставка в `set_steps`), `finding_service/dedup.py:70-71` (`sqlite_insert(...).on_conflict_do_update` — внешние находки `source != routine` authored), `link_service/semantic.py:318` и `:448-449` (`state` подсказки = решение человека, Q-017) | хук такие записи потеряет; переписываются в секции A (CLO-007) |
| Каскады БД | 80 правил `ondelete="CASCADE"` в `cod_doc/infra/models` | удаление дочерних строк каскадом журнал не видит (решение — Q-008) |
| Checkout | `checkout_service.py:73` `checkout`, `:150` `release`, `:236` `release_stale`; поля `task.checked_out_by`, `checked_out_at`, `expected_status_at_checkout` (`plans.py:119-121`) | замок локален одной БД (решение — Q-011) |
| Имена `sync` уже заняты | `cod-doc link sync`, `cod-doc ctx sync` + MCP `curator_sync` (`curator_sync_service.py` уже зовёт `git clone`), `cod-doc adr sync`, MCP `link_sync`, `adr_sync_body` | новые имена этого RFC — группа CLI `cod-doc sync …`, верхние `push/pull/fetch/clone/remote`, MCP-семейство `sync_*`; совпадений по полному имени нет, но в документации различаем явно |
| Резолв БД | `infra/db.py:85` `resolve_db_url`, `:121` `db_url_for_entry`, `:251` `db_for_entry` | реплика остаётся обычной SQLite, резолв менять не нужно |
| Hub | RFC 22 §3.1, `~/.cod-doc/hub.db`, `UNIQUE(project_id, task_id)` с миграции 0027 | единица синхронизации — проект, hub на это не влияет |
| Identity / auth | ARCHITECTURE §12.2 (`actor` — только DDL-спека, «гейт неактивен, ADO-217»), `api/v1/__init__.py:11-15` (Bearer — только docstring) | в API и MCP нет аутентификации вообще; `author` — свободная строка (ADR-012) |
| Модель ошибок | ARCHITECTURE §10 описывает `CodDocError` с кодами и exit 2–7, но `cod_doc/errors.py` нет: сервисы бросают stdlib-подклассы (`TaskNotFoundError(LookupError)`, `task_service.py:82-131`), MCP оборачивает в `ValueError`, CLI — `ClickException` с кодом 1, `/api/v1` отдаёт `{"detail": …}`; коды `PREFIX-NNN` живут только в `services/validation/_errors.py:9` | контракты §10 пишутся от того, что код делает сегодня; коды `SYNC-NNN` идут префиксом сообщения |
| Миграции | голова `0048_totals_project_scoped` (`versions/20261014_0048_totals_project_scoped.py`, AFT-024) | ревизии этого RFC — `0049…0052` (`0047_project_scoped_uniques` — предусловие STO-018/024) |
| Бэкап / экспорт | только проекции (`doc/plan/adr/scenario export`) | снапшота БД нет |
| Решения о хранении | ADR-005 (PostgreSQL, accepted), ADR-010 (два профиля: embedded SQLite и server/cloud на PostgreSQL, accepted 2026-09-06; работы — секция G `adoption-2026-08`, STO-*) | командный профиль сегодня **определён** как общая PostgreSQL; этот RFC предлагает другой путь к команде и требует нового ADR (§4) |
| VISION | §6 п. 5: цикл выполним удалённым ИИ-клиентом «против облачного узла COD-DOC» | цель та же, узел здесь — реплика с умным remote, а не общая БД |

## 3. Предложение

### 3.1 Словарь

| git | cod-doc remote |
|---|---|
| репозиторий | проект (`project.uid`) |
| рабочая копия + `.git` | локальная реплика `state.db` |
| объект | changeset или снапшот, адрес — sha256 содержимого |
| коммит | **changeset**: пачка операций над сущностями + родители |
| ветка `main` | `refs/main` проекта на remote; других веток в v1 нет |
| `git clone` / `pull` / `push` | `cod-doc clone` / `pull` / `push` |
| merge conflict | запись `sync_conflict`, видна в `curator_next` |

### 3.2 Классы данных

Реестр синхронизации (`cod_doc/infra/sync_registry.py`) относит каждую таблицу, а
при необходимости и колонку, ровно к одному классу. Таблица без записи в реестре
роняет тест — новая таблица не может молча выпасть из синхронизации.

| Класс | Что уезжает | Таблицы |
|---|---|---|
| `authored` | операции над полями | project, document, section, doc_node, doc_comment, plan, plan_section, task, dependency, affected_file, task_document, adr (+ supersedes/task/diagram), user_story (+ acceptance/link/section), scenario (+ step/link), module (+ dependency/code), tag (+ document_tag/task_tag/story_tag), external_ref, commit_link, approval (+ task/doc_revision link), routine (определение), structure_waiver, finding с `source != "routine"`, open_question (+ option/link), adr_relation, adr_topic; у `link_suggestion` — только решение (`state ≠ pending`, Q-017) |
| `journal` | append-only, без слияния | revision, activity_event кроме `link.*` и `routine.fired` |
| `derived` | ничего; пересобирается после pull | link, link_suggestion (кроме решения), doc_node_suggestion, `db_search_idx*` (FTS5), code_*, structure_* кроме structure_waiver, doc_code_claim, repo_file, repo_symbol, repo_import, finding с `source = "routine"` |
| `local` | никогда | routine_run, agent_run, trace_call, task_metrics, finding_source_run, sidecar `.cod-doc/runs/*.jsonl` (ADR-015); колонки `document.projection_hash`, `document.content_sha256_head` — они описывают локальное рабочее дерево; колонки `open_question_link.resolved`, `broken_reason`, `last_checked` — штампы проверки `question_verify`; колонка `position` (и `document.node_position`) у упорядоченных таблиц (вычисляется из `rank`, Q-009); строки документа с `sensitivity = restricted` и его секций (Q-015) |

Представления (`document_body`, `plan_totals`, `section_totals`, `ready_tasks`)
класса не получают — это SQL над таблицами. Реестр классифицирует 64 таблицы
моделей; гейт CLO-001 падает на 65-й без записи.

**Порядок (Q-009).** Порядок хранят одиннадцать мест; все получают синхронизируемую
колонку `rank TEXT` (дробный индекс, LexoRank-подобная строка):

| Таблица | Порядок внутри | `UNIQUE` на `position` сегодня |
|---|---|---|
| `section` | документа | нет (индекс) |
| `plan_section` | плана | нет |
| `doc_node` | проекта | нет (индекс) |
| `scenario` | `group_key` | нет (индекс) |
| `scenario_step` | сценария | `uq_scenario_step_position` |
| `story_section` | проекта | нет |
| `story_acceptance` | истории | `uq_story_acceptance_position` |
| `adr_diagram` | ADR | `uq_adr_diagram_position` |
| `adr_topic` | проекта | нет (индекс) |
| `open_question_option` | вопроса | `uq_open_question_option_position` |
| `document.node_position` | раздела дерева | нет (индекс), колонка `node_rank` |

Вставка между соседями пишет один `rank` и не трогает остальных; равные `rank`
упорядочиваются по `uid`. Целый `position` остаётся для существующих запросов, но
становится `local` и пересчитывается из `rank` на каждой реплике. Четыре
`UNIQUE` на `position` снимаются (§4). При решении Q-009 список был из шести
таблиц; сверка со схемой 2026-10-11 нашла ещё пять мест того же вида — правило
применяется ко всем.

**Решения по подсказкам ссылок (Q-017).** Подсказка (`link_suggestion`) остаётся
derived: каждая реплика считает её сама из эмбеддингов. Но `state` ≠ `pending` —
решение человека или автопринятия — authored и адресуется детерминированным ключом
`(uid секции-источника, to_doc_key, uid секции-цели | NULL)`. Пересчёт на любой
реплике сначала ищет решение по ключу: отклонённая подсказка не всплывает снова,
принятая остаётся принятой. Запись решения идёт через ORM (сегодня — bulk `update`,
переписывается в CLO-007).

**Restricted не уезжает (Q-015).** Документ с `sensitivity = restricted` и все его
секции — `local` построчно: класс определяется не таблицей, а значением колонки.
`confidential` и ниже синхронизируются: аудитория — участники проекта. Ссылки на
restricted-документ с других реплик честно битые (derived `link`).

Три рубежа (CLO-033). Захват не пишет строки restricted-документа и пересверяет
гейт на commit'е. **Упаковка** push'а пересматривает хвост по *текущим* меткам
(`sync_service.gate.shippable_tail`): документ, повышенный после захвата, но до
push, и ни разу не уезжавший, отзыва не пишет, и без этого шага хвост его тела
уехал бы первым же push'ем. Операции документа, его тела, их ревизий и маркеров
разрешения из хвоста удаляются (кроме операций отзыва), у ссылок на документ
вычищаются придержанные колонки. Снапшот судит строки по текущему состоянию сам.
**Растяжка** перед кодированием changeset'а и на каждой строке снапшота
(`gate.check_ops` / `check_row`): uid restricted-документа или строки его тела —
ни сущностью, ни значением; сработала — `RestrictedLeakError` (`SYNC-001`), и
отправки нет. Экспорт неотправленного (`export_pending`) обязан идти через те же
две функции. `sync status` показывает `restricted_local` и `restricted_rows`.

**Отзыв (CLO-046).** Повышение до `restricted` документа, который уже уезжал (его
операция есть в changeset'е — своём отправленном или принятом), в той же
транзакции пишет одну операцию отзыва: `upsert` документа ровно с полем
`sensitivity`. Получатель применяет её обычными гейтами и после неё держит
документ с `sensitivity = restricted`: строки документа и секций остаются, текст
секций и комментариев получатель затирает сам, неотправленный хвост по
документу (свой у владельца, свой у получателя) вычищается — он захвачен до
повышения. Секции не удаляются: ревизия адресует сущность её `row_id`, и
пересозданная при понижении секция оторвала бы свои ревизии у получателей, а не
у владельца — `verify` разошёлся бы. По той же причине придержанные колонки
ссылок на документ (`scenario.doc_key`, `section_anchor`…) у получателя не
очищаются физически: в дайджест они и так входят значением приёмника (F8), а
очищенная ссылка вернула бы в дайджест получателя ревизии сценария. После отзыва
документ и его тело local у всех, `verify` совпадает у владельца и
получателей, новые правки не уезжают. Changeset неизменяем: CLI `doc update` и
MCP `doc_update` возвращают предупреждение «история документа остаётся на
remote». Документ, который ни разу не уезжал, отзыва не пишет — отзывать нечего.
Чужая операция над строкой, которую держит local только отозванный документ
(правка в полёте, захваченная до отзыва), поглощается применением, а не
отвергается: иначе pull владельца остановился бы навсегда; её ревизия
записывается. Своего никогда не уезжавшего restricted-документа это не касается —
гейт отвергает операцию как раньше (SYNC-102).

**Понижение** `restricted → internal` уезжает первым upsert'ом (`base` пуст)
документа, его секций, комментариев, тегов, освобождённых колонок ссылок на него
и ревизий, которых remote не видел; тело, удалённое владельцем за время
`restricted`, уходит удалением. Понизить документ вправе только реплика, чей отзыв
в силе: у неё текст. Получатель принимает понижение, только если документ
restricted у него по **чужому** отзыву, понижение пришло от той же реплики и имеет
форму первого upsert'а (`base` пуст, поля — ровно уезжающие колонки документа);
иначе операция поглощается без записи. Документ, который реплика повысила сама,
чужое понижение не откроет, а пришедший позже чужой отзыв того же документа не
отнимает у неё права понизить. У получателя чужого отзыва `doc update` отвечает
`DM-005` — иначе он разослал бы пустые секции поверх текста.

**Карантин.** Отзыв не уничтожает чужую работу молча: неотправленные правки
получателя по документу и тексты затрагиваемых строк до затирания ложатся в
local-событие `sync.withdraw_quarantined` (payload — операции хвоста и тексты;
`activity_event` не уезжает). Отзыв документа, которого журнал получателя не
знает, отвергается (SYNC-102), а неподвижная точка тела при чистке хвоста
находит и правки секции, стоящие в хвосте раньше операции, называющей документ.

Markdown-проекции не синхронизируются вовсе: их везёт git репозитория. После pull
`doc drift` покажет расхождение, если БД ушла вперёд от закоммиченного файла, —
ровно та же семантика, что сегодня после правки через MCP. **Два канала
независимы (Q-013):** git везёт файлы, remote — операции; одинаковые значения,
приехавшие по обоим каналам, сливаются без конфликта. В синхронизируемом проекте
правки идут через БД, `doc import` — только для новых файлов и инициализации
репозитория; `edited_in_place` там — отклонение, которое показывает куратор.

Routine-находки — `derived`: каждая реплика пересчитывает их сама, а партиция
автозакрытия (`finding_service.reconcile_partition`) остаётся локальной. Иначе две
реплики закрывали бы находки друг друга по несвежей картине.

### 3.3 Глобальная идентичность

Каждая `authored`-таблица получает колонку `uid TEXT NOT NULL` (uuid7) с уникальным
индексом `(uid)`. `row_id` остаётся локальным PK — ни один запрос и ни один FK внутри
реплики не меняется. В операциях FK сериализуются через uid:
`{"plan_section": "0192…"}` вместо `plan_section_id: 17`; при применении
транслируются обратно в локальный `row_id`.

**Человеческие ID** — пять генераторов: задачи (`ADO-229`, префикс плана), `ADR-NNN`,
`US-NNN`, `SCN-NNN`, `Q-NNN` (§2). Выдаются так:

1. **Аренда блока.** На pull/push реплика берёт у remote блок номеров на префикс
   (по умолчанию 20). Аренда — объект `leases/<prefix>` на remote, обновляемый тем
   же CAS, что и `refs/main`, поэтому работает и на глупом remote. Сервис выдачи
   (`_next_task_id` и соседи) сначала берёт номер из локального остатка аренды.
2. **Провизорный ID** — когда аренды нет или она исчерпана офлайн:
   `ADO-~k3f2` (суффикс — 4 символа base32 от uid). Провизорный ID рабочий: на него
   можно ссылаться, делать checkout, писать в коммит.
3. **Закрепление** — на push провизорный ID получает номер из новой аренды. Старое
   имя пишется в `entity_alias(project_id, kind, alias, uid)`; `task_get` и CLI
   резолвят алиас с пометкой «переименовано в ADO-241».
4. **Переписывание строковых ссылок.** Часть authored-колонок ссылается на
   человеческий ID строкой, а не FK: `adr_task.task_id`, `commit_link.task_id`,
   `finding.promoted_task_id`, `structure_finding.promoted_task_id`,
   `open_question.resolved_by_adr`, `to_ref` у `story_link` / `scenario_link` /
   `open_question_link`. Закрепление переписывает их одной транзакцией по списку
   `human_id_refs` из реестра (§10.2); упоминания в тексте (тела секций, описания,
   сообщения коммитов) не переписываются — их резолвит алиас. `link.to_task_id` —
   derived и пересобирается сам.

Коллизия двух постоянных номеров невозможна по построению: блоки не пересекаются.

**Натуральные ключи (Q-007).** Аренда защищает только номера. У части сущностей
ключ — имя, которое придумывает человек, под `UNIQUE`; одно имя на двух репликах
получает разные `uid`. Правило зависит от вида ключа:

| Ключ | Вид | При совпадении |
|---|---|---|
| `document.doc_key`, `section.anchor` (в документе), `doc_node.node_key`, `routine.name`, `plan.scope`, `module.module_id`, `story_section.key`, `tag.name`, `adr_topic.name`, `task_document.key` (в задаче), `external_ref (system, external_id)`, `structure_waiver (scope, finding_fingerprint)` | имя-смысл: одно имя = одно намерение | склейка: выживает `uid` с меньшим HLC создания, второй пишется в `entity_alias`, поля сливаются по §3.7; тела без общего предка — равные без конфликта, разные → `sync_conflict` |
| `plan_section.letter` (в плане) | метка-порядок: буква ничего не значит | вторая по HLC перебуквляется в следующую свободную, задачи остаются при своих секциях; событие `plan_section.relettered` |
| `task.task_id`, `adr.adr_id`, `user_story.story_id`, `scenario.scenario_id`, `open_question.question_id` | арендованный номер | не совпадают по построению (аренда); провизорные — закрепление |
| `project.slug` | имя проекта в реестре машины | не сливается: проект адресуется `project.uid`; при `clone` в hub с занятым слагом — `--project <другой слаг>` |

Перебуквление детерминировано: обе реплики выбирают одну и ту же букву, потому что
сортируют по `(hlc, uid)`.

Алиас склейки local: его пишет реплика, которая склеивала. Клон не берёт его у
сборщика снапшота, а выводит из проверенной истории тем же правилом (§3.6, CLO-052).

**Предусловие.** `plan.scope`, `user_story.story_id` и `module.module_id` были
уникальны глобально, а не в пределах проекта: в hub-режиме клон второго проекта с
тем же scope упал бы на вставке. STO-018 и STO-024 закрыты миграцией
`0047_project_scoped_uniques` (`UNIQUE(project_id, …)`); STO-023 убирает поиски по
этим ключам без `project_id`, иначе вместо ошибки миграции были бы плавающие
падения и чужой проект в выдаче (закрыта 2026-10-11). STO-029 — тот же класс для
`task_service.get(task_id)`, и STO-030 — write-пути задач (checkout, complete,
update_status) по `task_id` без `project_id`; обе закрыты тем же PR #193.

**Рёбра и связи** — `dependency`, `adr_supersedes`, `adr_relation`, `adr_task`,
`module_dependency`, `scenario_link`, `story_link`, `open_question_link`,
`commit_link`, `affected_file`, `document_tag`/`task_tag`/`story_tag` — получают
**детерминированный** `uid = uuid5(NS_COD_DOC, kind ‖ uid концов ‖ relation)`, а не
случайный. Одно и то же ребро, добавленное на двух репликах, получает один `uid`, и
add-wins сводится к идемпотентной вставке. У тег-таблиц составной PK без `row_id`;
`uid` для них — та же формула, отдельная колонка не нужна (вычисляется при захвате).

**Логические FK без ограничения.** `adr.topic_id` ссылается на `adr_topic.row_id`
без FK (миграция 0046). Реестр объявляет такие колонки явно (`logical_fks`, §10.2),
иначе захват сериализовал бы локальный `row_id`, бессмысленный на другой реплике.

### 3.4 Журнал операций

```sql
CREATE TABLE sync_op (
  op_id        TEXT PRIMARY KEY,          -- ULID
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  replica_id   TEXT NOT NULL,             -- replica.uid автора операции
  hlc          TEXT NOT NULL,             -- hybrid logical clock, сортируемая строка
  entity_kind  TEXT NOT NULL,             -- 'task', 'section', …
  entity_uid   TEXT NOT NULL,
  kind         TEXT NOT NULL,             -- 'upsert' | 'delete'
  fields       TEXT NOT NULL,             -- JSON {field: new_value} только изменённые
  base         TEXT NOT NULL,             -- JSON {field: value_before} — для 3-way merge
  author       TEXT NOT NULL,             -- как в revision.author (ADR-012)
  changeset    TEXT                       -- NULL = ещё не упакована (локальный хвост)
);
CREATE INDEX ix_sync_op_pending ON sync_op(project_id) WHERE changeset IS NULL;

CREATE TABLE replica (
  uid        TEXT PRIMARY KEY,            -- uuid7, генерится при init/clone
  project_id INTEGER NOT NULL REFERENCES project(row_id),
  is_self    BOOLEAN NOT NULL DEFAULT 0,
  label      TEXT                         -- 'dakh@macbook', 'hermes-server'
);
```

**Захват** — хук unit-of-work в `infra/` (`event.listen(Session, "before_flush")`):
проходит `session.new/dirty/deleted`, берёт по реестру §3.2 только `authored`-модели
и колонки, из `inspect(obj).attrs[*].history` собирает `fields` и `base`. Правка
каждого сервиса не нужна; вся запись и так идёт через `Session` (гейт ADO-208).

**Обход хука** — bulk `session.execute(update|insert|delete(Model))`. Новый AST-гейт
`tests/infra/test_sync_capture_no_bulk_authored.py` запрещает bulk-запись в
`authored`-таблицы и колонки. Места из §2, бьющие в `derived`/`local`, проходят
гейт как есть. Три места, бьющие в authored, переписываются до включения гейта
(CLO-007):

| Место | Что делаем |
|---|---|
| `scenario_service/steps.py:53-54` `set_steps` (delete + вставка всех шагов) | ORM-дифф по шагам: совпавшие — правка полей, новые — вставка с `rank`, лишние — удаление; так ещё и не теряется `uid` шагов |
| `finding_service/dedup.py:70-71` (upsert находок) | для `source != routine` — явный захват `sync_capture.record(...)` (§10.3) рядом с upsert; routine-находки derived и не трогаются |
| `link_service/semantic.py:318`, `:448-449` (`state` подсказки) | ORM-присваивание `row.state = …`: решение authored (Q-017) |

`sync_capture.record` — единственный разрешённый способ bulk-записи в authored:
гейт пропускает вызов `session.execute` над authored-таблицей только в функции,
которая в той же транзакции зовёт `record` с теми же строками.

**Время** — HLC (физическое время + счётчик + `replica_id` для тай-брейка). Стенные
часы разных машин расходятся; HLC даёт полный порядок, согласованный с причинностью.

### 3.5 Changeset и remote

Changeset — неизменяемый объект:

```json
{
  "v": 1,
  "project": "<project.uid>",
  "parents": ["<sha256>", "..."],
  "replica": "<replica.uid>",
  "actor": "dakh",
  "schema_head": "0050_sync_rank",
  "hlc_max": "…",
  "ops": [ { "op_id": "…", "entity_kind": "task", "entity_uid": "…", "kind": "upsert",
             "fields": {"status": "done"}, "base": {"status": "in_progress"},
             "author": "claude-opus", "hlc": "…" } ]
}
```

Адрес — `sha256` канонического JSON (сортированные ключи, без пробелов), сжатие
zstd. Первые 4 байта объекта — версия формата: место под будущее шифрование (§5).

Раскладка на remote (одинаковая для S3 и для диска умного сервера):

```
<project_uid>/
  refs/main                   # sha256 головы; меняется только CAS
  leases/<PREFIX>             # {"next": 261, "holders": {...}}; меняется только CAS
  leases/checkout/<task_uid>  # {"replica", "actor", "at", "ttl"} — замок checkout (Q-011)
  objects/ab/cdef….zst        # changeset'ы
  snapshots/<epoch>/<sha>.db.zst  # authored + журнал на момент changeset <sha> (CDS1, ниже)
  meta.json                   # {"epoch": "0050_sync_rank", "schema_min": "0050_sync_rank", "created": …, "snapshot": {…}}
```

**Транспорт** — один интерфейс, три реализации:

```python
class RemoteStore(Protocol):
    def get_ref(self, project_uid: str, name: str) -> RefValue | None: ...
    def cas_ref(self, project_uid: str, name: str,
                expected: RefValue | None, new: bytes) -> bool: ...
    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]: ...
    def put_object(self, project_uid: str, sha: str, data: bytes) -> None: ...
    def get_object(self, project_uid: str, sha: str) -> bytes: ...
    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None: ...
```

| Схема URL | Реализация | CAS | Доступ |
|---|---|---|---|
| `file:///path` | `FileRemote` | `os.link` + rename в каталоге ref | права ФС; для тестов и NAS |
| `s3://bucket/prefix` | `S3Remote` (boto3) | conditional PUT `If-Match: <ETag>` / `If-None-Match: *` (есть в S3 и MinIO) | S3-креды, всё или ничего |
| `https://host` | `HttpRemote` → `/api/sync/v1/projects/{uid}/…` на `cod-doc serve` | на сервере, транзакцией | Bearer-токен + роль (§3.8) |

Для локальной обвязки подходит уже поднятый MinIO на Hermes (`localhost:9000` через
туннель); отдельной инфраструктуры RFC не требует.

### 3.6 Протокол

```mermaid
sequenceDiagram
    participant L as Локальная реплика
    participant R as Remote
    Note over L: push
    L->>L: упаковать sync_op WHERE changeset IS NULL → changeset C (parents = локальная голова)
    L->>R: has_objects([C…]) → put_object(отсутствующие)
    L->>R: cas_ref(refs/main, expected = известная голова, new = C)
    alt CAS прошёл
        R-->>L: ok — fast-forward
    else remote ушёл вперёд
        R-->>L: rejected
        L->>L: pull, затем push заново
    end
    Note over L: pull
    L->>R: get_ref(refs/main) → H
    L->>R: get_object(…) по родителям от H до общего предка
    L->>L: применить чужие ops, слить с локальным хвостом (§3.7)
    L->>L: merge-changeset M (parents = [локальная голова, H]); пересобрать derived
```

- **push** — только fast-forward. Отказ CAS = «remote ушёл вперёд», клиент сам
  делает pull и повторяет (`cod-doc push` делает это до 3 раз, дальше отдаёт ошибку).
- **pull** = fetch + merge. Merge-changeset несёт только операции, разрешившие
  расхождение; если локального хвоста нет — fast-forward без merge-changeset.
- **clone** — последний снапшот + changeset'ы после него. Снапшот пишет тот, кто
  делает push, если с прошлого снапшота накопилось больше N операций (по умолчанию
  5000), — иначе clone проекта со 100k операций replay'ил бы их все.
  Реализация (CLO-029, `sync_service/snapshot.py`, `clone.py`):
  - снапшот пишет и первый push в remote без снапшота: строки, заведённые до
    журнала, есть только в снапшоте, и clone без него на них упал бы;
  - формат — не файл SQLite (с чужого remote это исполняемая схема), а
    `b"CDS1"` + zstd потока строк канонического JSON §10.4: заголовок
    `{v, kind: "header", project, head, epoch, schema_head, digest}`, строки
    `["row", таблица, uid, поля]` (поля — как в операции: без придержанных,
    FK — uid, restricted не входит), в конце `["end", {lines, sha256}]`.
    Строку `["alias", {…}]` писал сборщик до CLO-052; загрузка её разбирает
    как секцию формата и пропускает с предупреждением в лог. Распаковка потоковая, потолки — строка
    64 МиБ, поток 16 ГиБ, сжатый 1 ГиБ;
  - снапшот несёт только то, что покрывает дайджест головы. Журнал `sync_op`
    (история LWW, штампы создания, цепочки статуса) и индекс `changeset`
    клон собирает сам из changeset'ов remote — обход `parents` от головы,
    каждый объект по адресу (`decode`), штампы — `check_clock`; применённым
    помечается только достижимое от проверенной головы;
  - алиасы склейки (`entity_alias`, `merged`) клон выводит из того же
    проверенного журнала (CLO-052, `sync_service/clone_aliases.py`): проходит
    changeset'ы в порядке применения pull'а и повторяет решения о ключах-именах
    без записи строк — вставка занимает ключ (FK в ключе — строка, не uid),
    совпадение с живой строкой — склейка по правилу `keys.glue_survivor`,
    правка ключа — LWW, удаление и каскад освобождают ключ; журнал
    проигравшего переезжает к выжившему, как у pull. Итог совпадает с
    обычной репликой, получившей ту же историю: алиас есть там, где обе
    вставки двойника в истории, цепочки остаются цепочками. Алиасу сборщика
    снапшота клон не верит — он local и мог указывать на uid, которого в
    истории нет (такой снапшот раньше ронял clone `SYNC-102`). Проигравший,
    оказавшийся живой строкой загруженного состояния, алиаса не получает
    (предупреждение в лог). `pinned` (CLO-030) не покидает реплику и на клоне
    не заводится;
  - снапшот собирается только с состояния, равного голове (голова реплики =
    запушенный changeset, хвоста нет, дайджест совпал); указатель —
    `meta.json["snapshot"] = {epoch, sha, ops}`, сдвигается CAS; сбой
    снапшота push не отменяет; `PushResult.snapshot` — sha записанного;
  - загрузка — тем же путём, что чужая вставка (`apply.create_row`,
    `journal.apply_row`, часы операций), одной транзакцией под
    `suspended`; дайджест загруженного обязан совпасть с дайджестом
    changeset'а `head`, прочитанного с remote по адресу, — иначе `SYNC-102`;
  - URL clone — каталог проекта на remote: `<URL remote>/<project_uid>`
    (листинга проектов в `RemoteStore` нет); его печатает `sync status`
    (`SyncStatus.clone_url`). Слаг — с remote (`project.slug` authored);
    `--project` обязан совпасть, а без снапшота обязателен;
  - клон не заводит дефолтные рутины и дерево разделов: они приезжают со
    снапшота (находка F2 аудита секции B).
- **Схема и эпохи (Q-012).** Реплика не применяет changeset с `schema_head` новее
  своей головы миграций: `pull` отвечает «remote на схеме 0053_sync_access, у вас 0050_sync_rank —
  `cod-doc update`». Миграция, меняющая `authored`-колонки, открывает **эпоху**:
  1. `cod-doc update` перед миграцией проверяет неупакованные операции и требует
     `cod-doc push`; push на старой схеме принимается, пока `meta.epoch` remote не
     сменилась;
  2. первый push на новой схеме пишет снапшот `snapshots/<epoch>/…` и поднимает
     `meta.epoch` и `meta.schema_min`;
  3. changeset'ы прошлых эпох больше не воспроизводятся: `clone` и отставшие
     реплики стартуют со снапшота эпохи;
  4. реплика, пропустившая смену эпохи с незапушенными операциями, применить их уже
     не может — `cod-doc sync export-pending` выгружает их в файл для ручного
     переноса.
  Push со схемой старше `meta.schema_min` отвергается тем же сообщением про
  `cod-doc update`. Миграция, не трогающая `authored`-колонки, эпоху не открывает.
  Реализация (CLO-032, `infra/sync_epochs.py`, `sync_service/epochs.py`):
  - эпоха называется открывшей её ревизией; эпоха реплики — последняя
    открывающая ревизия не новее головы миграций её БД. Решение «открывает /
    не открывает» записано застывшим реестром `sync_epochs.MIGRATIONS` для
    каждой ревизии после `0049_sync_identity`; гейт
    `tests/infra/test_sync_epochs_registry.py` прогоняет цепочку по шагу и
    сверяет решение с фактическим изменением уезжающих колонок
    (`SHIPPED_COLUMNS`: колонка или её тип, таблица; исчезнувшая колонка —
    всегда изменение). Сейчас эпоха одна — `0050_sync_rank`;
  - `cod-doc update`, фаза B: перед миграцией, открывающей эпоху, БД с
    проектом, у которого настроен remote и есть неупакованные операции, не
    мигрируется — `SYNC-106` «3 незапушенные операции — сначала cod-doc
    push -p P» с подсказками `cod-doc runtime rollback` (push сборкой, на
    которой операции записаны) и `export-pending`. Проект без remote не
    проверяется. Hub-БД держит любой её проект. БД, которую проверка не
    прочитала, тоже не мигрируется (fail closed); URL в лог и отчёт не
    попадает (пароль hub-БД);
  - push сверяет `meta.json` до обмена: эпоха remote новее — `SYNC-105` с
    подсказкой `export-pending`; схема старше `schema_min` — `SYNC-104`;
    эпоха реплики новее — снапшот после push пишется вне каденции, тот же CAS
    `meta.json` поднимает `epoch` и `schema_min`, событие `sync.epoch_opened`,
    `PushResult.epoch_opened`. Не записался снапшот — эпоха не открыта,
    следующий push попробует снова;
  - pull реплики, чья эпоха старше `meta.epoch`, — `SYNC-105` до скачивания
    (с хвостом — «сначала export-pending, затем cod-doc update»);
  - `meta.json` — недоверенный ввод: `v` = 1, `project_uid` = uid проекта,
    `epoch`/`schema_min` — имена ревизий ≤ 128 символов, `snapshot_every` —
    1…10⁷; иначе `SYNC-102`. Незнакомое коду, но правильно записанное имя
    эпохи — эпоха новее своей (`SYNC-105`, «cod-doc update»);
  - `export-pending` — конверт `{v, kind: "pending", slug, exported_at, ops,
    changeset}`, где `changeset` — форма §10.4 (`to_dict`) с нулевым
    дайджестом; хвост и растяжка — те же `gate.shippable_tail` и
    `gate.check_ops`, что у push (CLO-033), restricted не уходит и в файл.
    Файл новый, 0600, без `--out` — stdout. Обратного импорта нет: операции
    старой эпохи на новую схему не ложатся, переносятся руками через CLI/MCP.
  - п. 3 для отставших (CLO-050, `sync_service/epoch_rebase.py`): pull
    реплики, чья схема уже в эпохе `meta.epoch`, а changeset головы — в
    прошлой (после `cod-doc update`), не проигрывает changeset'ы между ними:
    реплика встаёт на снапшот по указателю `meta.json["snapshot"]` (указатель
    обязан быть в эпохе `meta.epoch`, иначе `SYNC-102`). Проверка — как у
    clone: changeset'ы от головы снапшота до применённых читаются по
    адресам, штампы — `check_clock`, голова реплики обязана быть их предком
    (иначе `MergeRequiredError`, как у переписанной истории); они ложатся в
    журнал и индекс историей, строки снапшота идут через `snapshot.load`, и
    дайджест загруженного сверяется с changeset'ом головы до коммита. Строки
    кладутся поверх своих одной транзакцией: уезжающая строка без пары в
    снапшоте удаляется, отличающаяся правится на месте (`row_id` остаётся),
    недостающая создаётся; local-строки, local-колонки и local-таблицы не
    трогаются. Незапушенные операции снапшот не примет — `SYNC-105` «сначала
    export-pending, затем проект заново через clone»; молча они не
    выбрасываются. Событие `sync.epoch_rebased`, `PullResult.rebased`;
    `--dry-run` называет снапшот, хвост после него — следующим прогоном;
  - changeset эпохи старше `meta.epoch` среди скачанных pull'ом — `SYNC-105`
    до первой записи (такой поверх истории новой эпохи пишет только клиент
    без CLO-032; реплике без головы — `clone`). Changeset'ы эпохи, которую
    remote ещё не сменил, реплика на новой схеме применяет как обычно: так
    она догоняет remote перед push'ем, открывающим эпоху.
- **Checkout (Q-011).** Если у проекта настроен remote, `task_checkout` берёт замок
  `leases/checkout/<task_uid>` тем же CAS, что и `refs/main`, — на S3 и на умном
  remote одинаково. Занятый замок — отказ сразу: «взята dakh@macbook в 14:02».
  `task_release`, `task_complete` и TTL (`release_stale`) снимают замок там же.
  Код замка живёт в `checkout_service` (`checkout` `:73`, `release` `:150`,
  `release_stale` `:236`); аренда встаёт перед локальной транзакцией checkout:
  сначала CAS на remote, потом запись в БД, при ошибке записи — снятие аренды.
  Без сети `task_checkout` отказывает; `task_checkout --offline` берёт провизорный
  замок, а двойной захват ловится при pull как `sync_conflict`. Проект без remote
  работает по ADO-039 как сегодня.
  Реализация (CLO-031, `sync_service/checkout_lease.py`): значение аренды —
  `{task_id, agent, replica, acquired_at, ttl_minutes}`, держатель — пара
  (агент, реплика); снятие — `delete_json` по etag после коммита (хук сессии),
  откат транзакции checkout снимает аренду, взятую этим вызовом. Чужую аренду
  снимают `release_stale` и `release --force`, и только истёкшую по её
  `ttl_minutes`. Провизорный замок —
  отметка `sync_ref("@checkout", task_uid)`; push/pull берёт по ней аренду,
  занятую — оставляет до разрешения `double_checkout`, которое переписывает
  аренду на выбранного держателя.
  **Осиротевшая аренда (CLO-048).** Реплика взяла аренду и упала или потеряла
  сеть, не отправив операцию checkout: другие реплики о замке не знают, их
  `release_stale` ищет устаревшие замки в своей БД и такую аренду не находит.
  Решение: **checkout перехватывает истёкшую чужую аренду**, если в его
  локальной БД задача свободна. Перехват — `cas_json` с `expected_etag`
  прочитанной истёкшей версии (на S3 — `If-Match`), поэтому из двух реплик,
  перехватывающих одну аренду, проходит одна; вторая перечитывает и получает
  `SYNC-108` уже от победителя. Листинг для этого не нужен, и перехват
  закрывает случай, когда задача не известна ни одному `release_stale`.
  Перехват пишет событие `task.checkout_lease_taken_over` с прежним держателем.
  Замок, известный локально (операция держателя уехала и пришла pull'ом),
  перехвату не подлежит: локальная проверка отказывает до сети, а
  идемпотентный повтор под тем же именем агента получает `SYNC-108`.
  Аренда истекла, если `acquired_at + ttl_minutes + CLOCK_SKEW_ALLOWANCE`
  (2 мин) раньше локальных часов: допуск покрывает расхождение часов реплик,
  живую аренду с недоистёкшим сроком не перехватывает никто. Дополнительно
  `release_stale` после коммита проходит листингом по `leases/checkout/`
  (`RemoteStore.list_prefix`, §10.5) и снимает истёкшие аренды по etag
  прочитанной версии, в том числе аренды задач, которых в этой БД нет.
  Продления аренды нет: если работа идёт дольше `ttl_minutes`, а операция
  держателя ещё не уехала, другая реплика может перехватить аренду, и
  двойной захват всплывёт при pull как `double_checkout` (§3.7). Это та же
  цена, что у `release_stale`, который снимает локальные замки старше TTL.
  Тело аренды пишут чужие машины, поэтому оно проверяется: строки
  `task_id`/`agent`/`replica`/`acquired_at` длиной 1…256 символов,
  `ttl_minutes` от 1 до 10080 (неделя), тело целиком ограничено потолком
  транспорта `MAX_JSON_BYTES`. Не по форме и `acquired_at` впереди
  локальных часов больше чем на `CLOCK_SKEW_ALLOWANCE`, и момент, срок
  которого выходит за пределы `datetime` (`OverflowError`): такая аренда не
  истекла бы никогда. Перехватывать её как истёкшую нельзя, потому что
  «будущее» может означать просто спешащие часы держателя. Вечным замком
  она тоже не становится: её снимает `release --force`. Аренда не по форме считается занятой
  (`CheckoutLeaseMalformedError`, `SYNC-108`) с подсказкой
  `cod-doc task release <id> --force`, которая снимает её и при отсутствии
  локального замка. Если тело вообще не JSON, etag для условного удаления
  получить нельзя, и подсказка предлагает удалить ключ на remote вручную.
- **Пересборка derived** после pull: `link_sync` по затронутым секциям, FTS по
  затронутым документам, routine-находки — при следующем прогоне рутины.

### 3.7 Слияние

Слияние детерминировано: две реплики, применившие одно множество changeset'ов в
любом порядке, приходят к одному состоянию. Проверка — `cod-doc sync verify`:
дайджест реплики сравнивается с дайджестом в последнем changeset'е.

**Дайджест.** sha256 потока строк, отсортированного по `(entity_kind, uid)`;
строка — канонический JSON `authored`-колонок сущности (ключи по алфавиту, FK как
`uid`, время в UTC ISO-8601). В дайджест **не** входят `row_id`, `local`-колонки
(`position`, хэши рабочего дерева), строки `restricted`-документов и
`revision.parent_revision_id` (порядок ревизий на репликах может различаться,
Q-010); состав ревизий по `revision_id` входит.

| Что | Правило | Конфликт |
|---|---|---|
| скалярное поле (title, priority, plan_section) | LWW по HLC **на уровне поля**: правки разных полей одной задачи не конфликтуют | нет |
| одинаковое значение с обеих сторон (в т.ч. правка, приехавшая и git, и remote) | значения равны — операция поглощается | нет |
| порядок (`rank`, Q-009) | LWW по полю; вставки рядом не трогают соседей, равные `rank` — по `uid` | нет |
| совпадение натурального ключа (Q-007) | имя-смысл — склейка + `entity_alias`; метка-порядок — перебуквление второй | разные тела без общего предка → `sync_conflict` |
| текст (section.body, task.description, task.acceptance, task_document.body, adr.decision, user_story.narrative) | 3-way merge построчно от `base`; непересекающиеся ханки сливаются | пересекающиеся ханки → `sync_conflict`, в БД остаётся «наше», «их» лежит в конфликте |
| `task.status`, `task.checked_out_*` | проигрывание **цепочки** всех записей поля по `(hlc, op_id)` от общего начала (CLO-044, ниже): последовательная запись применяется как есть, конкурентная — если её допускает правило колонки (`ALLOWED_TRANSITIONS` из текущего значения; у замка — не двойной захват); записи этих полей из хвоста не обрезаются | отвергнутая конкурентная запись → `sync_conflict` `status` / `double_checkout` |
| множества (dependency, *_tag, story_link, adr_task, affected_file) | add-wins: добавление одной стороны и удаление другой → элемент остаётся | нет |
| delete против edit (Q-008) | delete выигрывает: в журнал идёт одна операция delete родителя (tombstone по uid), поддерево сносит тот же `ON DELETE CASCADE` на каждой реплике; операция над потомком мёртвого родителя не применяется | `sync_conflict` со снимком поддерева — `sync resolve --take theirs` восстанавливает |
| человеческий ID | не сливается: коллизий нет по §3.3 | — |
| journal (revision, activity_event) | объединение по глобальному ID | нет |
| цепочка ревизий (Q-010) | чужая ревизия встаёт со своим `revision_id`, `author`, `reason`; `parent_revision_id` = локальная голова на момент применения; история на реплике линейна, оптимистическая блокировка (ARCHITECTURE §12.1) работает как сегодня — устаревший `expected_parent_revision_id` получает `OptimisticLockError` | нет |

**Статус — цепочка, а не пара** (CLO-044; находка F11 аудита секции C).
Попарная проверка «больший HLC при допустимом переходе из значения другой
стороны» у трёх и более записей одной задачи зависела от порядка доставки:
промежуточная запись проигрывала допустимым переходом, обрезалась из хвоста и
не уезжала, а третья реплика судила пару, которой в истории не было
(blocked A → in_progress B → done C: в одних порядках конфликта нет, в других
C открывала `status` по паре blocked → done). Правило для `task.status` и полей
замка `checked_out_by`/`checked_out_at`:

1. Записи этих полей из своего хвоста **не обрезаются** ни при проигрыше, ни
   при равенстве значений: каждая уезжает, и каждая реплика рано или поздно
   держит всю цепочку записей поля.
2. Значение поля — проигрывание всех известных реплике записей (журнал, хвост,
   применяемый changeset) по `(hlc, op_id)`. Начало — `base` самой ранней
   записи. Запись, чей `base` равен текущему значению, **последовательная**
   (автор видел это состояние) и применяется как есть: линейная история одной
   реплики не судится. Запись поверх другого значения **конкурентная** и
   применяется, только если её допускает правило колонки: у `status` —
   переход из текущего значения в её значение по `ALLOWED_TRANSITIONS`
   (одно значение в разных написаниях — без проверки); у замка — всё, кроме
   двойного захвата (запись взяла свободный замок, а в цепочке его держит
   другое значение).
3. Отвергнутая запись — конфликт `status` (у замка — `double_checkout`, один на
   задачу) с подписью, одной на всех репликах: `ours` — значение проигрывания
   (оно в БД на **каждой** реплике), `theirs` — отвергнутая запись, `base` — её
   `base`. Своя запись тоже может оказаться отвергнутой, когда доезжает более
   ранняя чужая; правило «в БД остаётся наше» к цепочке не применяется — иначе
   реплики разошлись бы в поле до решения человека.
4. Проигрывание повторяется при каждой чужой записи поля. Конфликт цепочки —
   один на подпись (повтор обновляет его); цепочка, которой больше нечего
   отвергнуть (доехала промежуточная запись), закрывает его сама
   (`resolved_by = sync:chain`). Отвергнутая запись не старше маркера
   разрешения той же подписи (ниже, CLO-045) конфликт заново не открывает.

Итог зависит только от множества записей: одно значение и одно множество
отказов у всех реплик при любом числе записей и любом порядке обмена
(property-тест CLO-027 без ограничения на число писателей статуса).

Конфликт — строка таблицы, а не маркеры в тексте:

```sql
CREATE TABLE sync_conflict (
  uid          TEXT PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id),
  entity_kind  TEXT NOT NULL,
  entity_uid   TEXT NOT NULL,
  field        TEXT,                 -- NULL для delete/edit и склейки ключей
  ours         TEXT, theirs TEXT, base TEXT,  -- для delete/edit theirs = JSON-снимок поддерева
  theirs_op    TEXT NOT NULL,        -- op_id чужой операции
  status       TEXT NOT NULL,        -- 'open' | 'resolved'
  resolved_by  TEXT, resolved_at DATETIME,
  opened_hlc   TEXT                  -- старший штамп записей конфликта (0052, CLO-045)
);
```

Разрешение (`sync_resolve --take ours|theirs|<текст>`) — обычная запись через
сервис: она порождает новую операцию, ревизию и activity event (ADO-040) и уезжает
при следующем push.

**Маркер разрешения** (CLO-045; находка F4 аудита секции C). `sync_conflict`
local, и конфликт с одной подписью `(entity_kind, entity_uid, field, kind)`
бывает открыт на нескольких репликах — у каждой со своим uid и своим «нашим».
Значение разрешения со свежим штампом выигрывает везде, но конфликт на других
репликах без маркера висел бы до ручного resolve на каждой. Поэтому
`resolve_conflict` вместе с записью значения кладёт в хвост маркер — операцию
журнала с зарезервированным видом сущности `sync_resolution` (формат — §10.4):

1. **Получатель** закрывает свои открытые конфликты той же подписи, у которых
   `opened_hlc` меньше штампа маркера (`NULL` — конфликт до миграции 0052):
   `resolved_by = sync:remote:<uid маркера>`, событие `sync.conflict_resolved`
   с `take = remote`. Конфликт, открытый позже маркера, остаётся открытым.
2. `opened_hlc` — **старший штамп записей, сошедшихся в конфликте**, а не
   часы реплики в момент открытия: местное время открытия зависит от того,
   доехал ли маркер раньше записей. По той же причине конфликт, чьи записи не
   новее уже известного маркера подписи, не открывается вовсе — множество
   открытых конфликтов зависит только от множества операций.
3. Маркер выпускается, только если подпись не local у разрешившей реплики:
   сущность уезжает (не restricted), поле не придержано; удалённая сущность —
   если её uid уже ходил в журнале. Получатель проверяет маркер как любую
   чужую операцию (проект changeset'а, штампы, `SYNC-102`) и сверх того — форму
   (только `upsert` с пустым `base`, ровно четыре поля подписи, uid маркера —
   UUID, вид конфликта из `sync_conflict.kind`, поле есть ровно у видов про
   поле и уезжает у таблицы, таблица захватывается и не журнал) и гейт реестра
   (сущность или поле подписи local у получателя — `SYNC-102`).
4. В дайджест маркер не входит: он живёт только в `sync_op` (свой — хвостом
   до push, чужой — историей после применения), отдельной таблицы нет. `curator_next` показывает открытые конфликты одним пунктом
очереди с готовой командой — ниже битых ссылок, выше неразобранного Инбокса.

### 3.8 Доступ (умный remote)

Активирует спеку ARCHITECTURE §12.2, но с поправкой: actor — сущность **сервера**,
а не проекта, и членство вынесено отдельно.

```sql
CREATE TABLE actor (
  uid        TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,          -- 'human' | 'agent'
  handle     TEXT NOT NULL UNIQUE,   -- 'dakh', 'dakh/claude-code'
  owner_uid  TEXT REFERENCES actor(uid),  -- агент принадлежит человеку
  created    DATETIME NOT NULL,
  disabled   BOOLEAN NOT NULL DEFAULT 0
);
CREATE TABLE api_token (
  uid        TEXT PRIMARY KEY,
  actor_uid  TEXT NOT NULL REFERENCES actor(uid),
  label      TEXT NOT NULL,          -- 'macbook', 'ci', 'cursor-cloud'
  token_hash TEXT NOT NULL UNIQUE,   -- sha256; сам токен показывается один раз
  created    DATETIME NOT NULL, last_used DATETIME, revoked_at DATETIME
);
CREATE TABLE project_member (
  project_uid TEXT NOT NULL,
  actor_uid   TEXT NOT NULL REFERENCES actor(uid),
  role        TEXT NOT NULL,          -- 'reader' | 'writer' | 'admin'
  PRIMARY KEY (project_uid, actor_uid)
);
```

| Роль | fetch / clone | push | аренда ID | участники, токены, `meta.schema_min` |
|---|---|---|---|---|
| reader | да | нет | нет | нет |
| writer | да | да | да | нет |
| admin | да | да | да | да |

Агентский actor наследует не больше роли своего владельца и может быть сужен до
reader.

- **Проверка на push.** Сервер отвергает changeset, в котором `actor` не совпадает с
  владельцем токена, а `author` операции не равен ни handle actor'а, ни handle
  его агентов. Провенанс перестаёт быть свободной строкой именно там, где пишут
  несколько людей; локально ADR-012 работает как раньше.
- **Токены** хранятся у клиента в `~/.cod-doc/credentials` (0600) или в Keychain
  (`cod-doc remote login` выбирает Keychain на macOS). Сравнение на сервере — по
  sha256, constant-time.
- **Тот же Bearer-гейт** закрывает `/api/v1` (обещанный в `api/v1/__init__.py`) и
  streamable-http MCP. Это CAP-020/022 из RFC 23, сделанные один раз.
- **Глупый remote** не знает участников: доступ равен S3-кредам, `author` не
  проверяется. Годится для одного владельца на нескольких машинах или для группы,
  где все доверяют всем; `cod-doc remote add s3://…` пишет об этом предупреждение.
- **Развёртывание (Q-014).** `cod-doc serve` слушает только loopback; наружу его
  выставляет reverse proxy (Caddy) с TLS от Let's Encrypt, на VPS открыт только 443.
  TLS в cod-doc не встраивается. Сервер ограничивает частоту запросов на токен и IP
  и пишет отказы аутентификации в журнал (`auth.denied` в `activity_event` сервера) —
  без этого первый публичный порт на Hermes слеп к перебору. MinIO и MailPit
  остаются за SSH-туннелем; облачные агенты (VISION §6 п. 5) ходят тем же HTTPS.

**Как сделано (2026-10-19, CLO-034/036/038).** Решения, которых спека выше не
называла:

- **Хранилище доступа** — отдельная БД сервера на общей схеме миграций
  (`Config.access_db_url`, по умолчанию `$COD_DOC_HOME/access.db`), а не БД
  проекта: actor — сущность сервера, а проекты лежат каждый в своей БД. Нет
  файла — гейт выключен и всё работает как до CLO-036; `cod-doc access init`
  создаёт его и включает гейт без рестарта (файл проверяется на каждом
  запросе). Журнал хранилища держит FK через строку проекта `_access`, которой
  нет в реестре.
- **Ревизий у доступа нет**, только activity event (ADO-040): `revision_revert`
  по ним отменил бы отзыв токена.
- **Гейт на MCP проверяет не только токен.** Владелец токена привязывается к
  контексту запроса (`services/request_identity`); MCP идёт в режиме
  `stateless_http`, поэтому контекст доходит до тула. `session_factory` тула
  пускает только в проект, где владелец участник; `activity_service.emit` —
  единственная точка, через которую проходит каждая запись, — требует роль
  не ниже writer и `author`, равный handle владельца или его агента
  (`human:alice`, `agent:alice/claude-code`); префикс вида обязан совпасть с
  видом handle — `system:alice` отвергается. Так правило «провенанс — не
  свободная строка» действует на MCP уже сейчас, до `/api/sync/v1`.
- **Reader на MCP — по списку чтения** (`mcp/access_policy.READER_TOOLS`,
  deny по умолчанию): откат транзакции в `emit` не возвращает файлы проекции
  (`doc_export`), сеть (`sync_*`) и вызовы LLM, сделанные до него.
- **Узел закрыт без хранилища**: `COD_DOC_REQUIRE_AUTH=1` (ставит
  `deploy/sync-remote`) превращает «нет `access.db`» из «гейт выключен» в 503.
- **Loopback не освобождает от токена**, если хранилище есть: за reverse proxy
  на том же хосте все запросы пришли бы с `127.0.0.1`.
- **MCP на не-loopback адресе без хранилища не стартует** (`--host 0.0.0.0` →
  ошибка с подсказкой `access init`). За прокси публичное имя добавляется к
  защите SDK от DNS rebinding: `--allowed-host` / `COD_DOC_MCP_ALLOWED_HOSTS`.
- **`X-Forwarded-For`** учитывается только от loopback и сетей
  `COD_DOC_TRUSTED_PROXIES`: иначе подделанный заголовок уводил бы от лимита.
- **Лимиты**: 10 неудачных попыток с адреса за минуту, 600 запросов на токен за
  минуту, дальше 429 с `Retry-After`; блок пишет `auth.denied` один раз на окно.
- **CLI доступа работает на сервере** против его хранилища (`docker compose exec
  … cod-doc token create`), а с `--remote NAME` — через https-remote проекта
  (CLO-038, роль admin).

**Как сделано (2026-10-20, CLO-035/037/038).**

- **Объекты умного remote** сервер хранит в своём `FileRemote`
  (`Config.sync_remote_root`, по умолчанию `$COD_DOC_HOME/remotes`): CAS, потолки и
  проверка имён — те же, что у `file://`; маршруты `api/sync` только переводят HTTP
  в `RemoteStore`. Клиент — `infra/remote/http.HttpRemote`; он проходит общий
  контракт транспортов, включая межпроцессную гонку CAS и clone (SCN-082), против
  настоящего сервера за гейтом. `http://` — только loopback.
- **Сверх таблицы §10.9**: `GET json?prefix=` (`list_prefix`), `GET
  snapshots/{epoch}/{sha}`, маршруты доступа `members` и `tokens`. `meta.json`
  пишет и writer (`PUT json/meta.json` — первый push новой эпохи), `PUT meta` —
  ручной `schema_min` admin'ом. CAS-отказ — 412 (`SYNC-103`).
- **Проверка push** — на `PUT objects/{sha}`: сервер разбирает changeset тем же
  `changeset.decode`, что pull; непригодный объект — 404 `SYNC-102` и не
  хранится; `actor` не владелец токена, `author` операции не его handle/агент
  (вид в префиксе тоже сверяется), чужой проект — 422 `SYNC-203`. Push локальной
  истории с legacy-авторами (`mcp`, `cli`) на умный remote поэтому отвергается —
  такой remote для истории одного владельца не нужен, для неё есть `file://`/`s3://`.
- **Токен через remote** — только себе или своему агенту: иначе admin одного
  проекта получал бы все роли владельца токена в других проектах.
- **`remote login`** — Keychain на macOS (`security`, токен на время записи виден в
  argv), иначе `~/.cod-doc/credentials` (0600, атомарная запись). Читает токен
  `HttpRemote`: файл кредов, затем Keychain.

### 3.9 Поверхности

Правило четырёх равных поверхностей соблюдается; гейты паритета
(`_surface_parity.py`) расширяются на `sync_service`.

| CLI | MCP (standard / full) | Что делает |
|---|---|---|
| `cod-doc remote add <name> <url>` / `list` / `remove` | — | запись в `~/.cod-doc/config.yaml` у проекта: `remotes: {origin: s3://…}` |
| `cod-doc remote login <name>` | — | сохранить токен |
| `cod-doc clone <url> [--project <slug>] [<dir>]` | — | снапшот + хвост, регистрация в реестре |
| `cod-doc fetch` / `pull` / `push` `-p <slug>` | `sync_pull`, `sync_push` | §3.6 |
| `cod-doc sync status -p <slug>` | `sync_status` | ahead/behind, неупакованные операции, открытые конфликты, аренды, эпоха remote, число `restricted`-документов и их строк (не уезжают), подсказка про drift |
| `cod-doc task checkout <id> [--offline]` | `task_checkout(offline=…)` | при настроенном remote — замок через CAS (§3.6); `--offline` — провизорный |
| `cod-doc sync export-pending` | — | выгрузка незапушенных операций в файл после пропущенной смены эпохи |
| `cod-doc sync conflicts` / `resolve <uid> --take …` | `sync_conflicts`, `sync_resolve` | §3.7 |
| `cod-doc sync verify` | `sync_status(verify=True)` | дайджест реплики против remote |
| `cod-doc sync log [-n 20]` | — | changeset'ы: автор, реплика, число операций |
| `cod-doc member add/list/remove`, `cod-doc token create/revoke` | — | только умный remote, роль admin |

Профиль `agent` (6 curator-тулов) не расширяется: куратор узнаёт о конфликтах и
отставании от remote через `curator_next`. Администрирование доступа в MCP не
выставляется намеренно: выдача токена агентом, которому этот токен и нужен, —
эскалация привилегий. Для MCP-тулов из этой таблицы потребуется обновить счётчики
профилей и `PROSE_COUNTERS`.

**Автосинк** — рутина `sync` в существующем движке routines (`pull`, затем `push`
по расписанию проекта, по умолчанию раз в 10 минут, при отсутствии сети — тихо до
следующего прогона). Движок зовёт проверку как `check_fn(session, project_id,
**check_args)` внутри своей транзакции (`routine_service.py:545` `CHECK_CATALOG`,
`run_now` `:672`). Для `sync` это недопустимо: сетевой обмен держал бы
пишущую транзакцию SQLite минутами. Поэтому `sync`-проверка использует переданную
сессию только для записи итога прогона, а `pull`/`push` открывают собственные
короткие транзакции через `sync_service` (§10.6): сеть — вне транзакции, применение
операций — в одной транзакции на changeset.

## 4. Миграция / обратная совместимость

- **Пять ревизий alembic** после `0047_project_scoped_uniques` (STO-018/024 — проектные UNIQUE для `plan.scope`, `story_id`, `module_id`):

  | Ревизия | Что | Секция |
  |---|---|---|
  | `0049_sync_identity` | `ADD COLUMN uid` во все authored-таблицы, бэкфилл (uuid7, у рёбер — uuid5 по §3.3), `CREATE UNIQUE INDEX`; таблицы `replica`, `sync_op`, `sync_ref`, `sync_lease`, `changeset`, `entity_alias` | A |
  | `0050_sync_rank` | `ADD COLUMN rank` (и `document.node_rank`) в 11 мест порядка, бэкфилл в порядке текущего `position`; снятие четырёх `UNIQUE(…, position)` | A |
  | `0051_sync_conflict` | таблица `sync_conflict` | C |
  | `0052_sync_conflict_opened_hlc` | `sync_conflict.opened_hlc` (CLO-045) | D |
  | `0053_sync_access` | `actor`, `api_token`, `project_member` — нужны только серверу; эпоху не открывает (номер 0052 занят, CLO-049) | E |

- **Снятие `UNIQUE(position)`** на SQLite — пересоздание таблицы. Это допустимо для
  `scenario_step`, `story_acceptance`, `adr_diagram`, `open_question_option`: у них
  нет CASCADE-детей (проверяется в CLO-003 до миграции, а не после). Для `document`
  и `section` — недопустимо, и там `UNIQUE` на `position` нет.
- **Никакого `batch_alter_table` на `document`**: на SQLite он пересоздаёт таблицу, и
  CASCADE уносит все секции и ссылки (см. CLAUDE.md). Только `ALTER TABLE … ADD
  COLUMN` + `CREATE UNIQUE INDEX`. Тест по образцу
  `test_migration_0035_preserves_data.py` наливает данные на `0047_project_scoped_uniques` и
  гонит upgrade; число секций, ссылок, задач и рёбер до и после совпадает.
- **Проект без remote** работает как сегодня; `sync_op` пишется всегда, чтобы
  первый `remote add` + `push` не терял историю между миграцией и подключением.
  Первый push выгружает снапшот и становится корнем DAG.
- **Hub-режим**: единица синхронизации — проект; в одной `hub.db` часть проектов
  может иметь remote, часть нет.
- **Postgres не нужен.** И реплика, и сервер работают на SQLite. Из Postgres-блокеров
  RFC 23 этому RFC нужны три — STO-018 и STO-024 (глобальные `UNIQUE` →
  проектные, сделано миграцией 0047) и STO-023 (поиски без `project_id`), без чего
  клон в hub ломается (§3.3). Четвёртое предусловие — STO-029 (`task_service.get`
  ищет `task_id` без `project_id`) — правка кода, не миграция: ревизий в таблице выше
  не добавляет. STO-019…022 остаются за RFC 23.
- **`doc import` в синхронизируемом проекте (Q-013)** — только для новых файлов и
  инициализации репозитория; правки идут через БД. Для самого cod-doc это расходится
  с нынешним PostToolUse-хуком «после правки `.md` — `doc import`»; смена хука и
  правила в CLAUDE.md — отдельная идея, этот RFC её не делает.
- **Связь с ADR-010 (Q-016).** ADR-016 **уточняет** ADR-010, а не отменяет его — так
  же, как ADR-010 уточнил ADR-005. PostgreSQL остаётся опциональным профилем
  server/cloud; секция G `adoption-2026-08` (STO-018…024) и CI-job `test-postgres`
  живут, но выходят из критического пути к командной работе: команда = реплики на
  SQLite + remote. Связь фиксируется `adr_relate(ADR-016 → ADR-010)`.
- **Связь с RFC 23.** Его premise «SoT = Postgres в облаке» заменяется на «сервер —
  это такая же реплика с умным remote». Сервер может материализовать проект в
  локальную SQLite и отдавать её web UI и облачным агентам через remote MCP; секции
  B–D RFC 23 (`agent_apply`, токены, проекция) садятся на auth из §3.8. Сам RFC 23
  не отменяется; его секция A (Postgres SoT) — `deferred`.
- **Принцип «Не берём: multi-tenant»** из `proposals/README.md` не нарушается:
  команда — это участники одного проекта на одном сервере владельца. Организаций,
  тенантов, регистрации и биллинга нет.

## 5. Риски и что не делаем

**Риски**

| Риск | Смягчение |
|---|---|
| Хук захвата пропускает запись → реплики молча расходятся | AST-гейт bulk-записи (§3.4); `sync verify` в рутине `sync` пишет находку при несовпадении дайджеста |
| Новая таблица забыта в реестре | тест «каждая модель классифицирована» (§3.2) |
| Качество 3-way merge для markdown-текстов | построчный merge + конфликт вместо маркеров; единица слияния — секция, а не документ, поэтому правки разных секций одного документа не пересекаются |
| Рост `sync_op` и объектов | упакованные операции старше последнего снапшота удаляются локально (`sync gc`); на remote объекты старше двух снапшотов — по флагу |
| Расхождение схем между машинами | `schema_head` в changeset + отказ применять новее; эпохи (§3.6) — `cod-doc update` сначала требует push |
| Потеря незапушенных правок при пропущенной смене эпохи | `update` блокируется до push; если реплика всё же отстала — `sync export-pending` в файл |
| Утечка токена | отзыв (`token revoke`), `last_used`, токен на устройство, а не на человека |
| Перебор токенов на публичном 443 | rate limit на токен и IP, журнал `auth.denied`, TLS у Caddy (§3.8) |
| Секрет уехал до повышения до `restricted` | отзыв: у получателей документ restricted, тело затёрто (§3.2, CLO-046); предупреждение «история документа остаётся на remote»; чистка истории — только пересозданием remote (вне v1) |
| Сдвиг часов | HLC, а не стенное время |
| Checkout зависит от сети | `--offline` с конфликтом при pull; проект без remote — как сегодня |

**Не делаем в v1**

- Ветки, PR-flow и ревью изменений в БД — один `refs/main` на проект.
- Realtime-совместное редактирование и посимвольные CRDT.
- Multi-tenant, SaaS, регистрацию, организации.
- Синхронизацию `derived`-таблиц и markdown-проекций.
- E2E-шифрование объектов (формат оставляет под него версию, §3.5).
- Partial clone (по плану, по разделу дерева).
- P2P между репликами без remote.
- Подписи changeset'ов (провенанс на умном remote даёт токен).
- Postgres как бэкенд реплики.
- Гранулярные права ниже проекта (на документ / раздел / sensitivity) — это §12.3,
  отдельный RFC.

## 6. Решения

| Развилка | Решение | Почему |
|---|---|---|
| Центральная БД или распределённые реплики | реплики | офлайн-работа и бэкап «бесплатно»; не упирается в Postgres-блокеры |
| Синк состоянием или операциями | операции с `base` | нужен 3-way merge текста и LWW на уровне поля; снапшоты — только ускорение clone |
| Захват в сервисах или в `Session` | `before_flush` | одно место на все сервисы; гейт ADO-208 уже гарантирует, что запись идёт через `Session` |
| Человеческие ID | аренда блоков + провизорный ID | постоянные номера не сталкиваются никогда; офлайн не блокирует работу |
| Один протокол или два | один, три транспорта | S3 закрывает «я и мои машины» без сервера, сервер — команду; тесты гоняются на `file://` |
| Конфликт текста | строка `sync_conflict`, в БД «наше» | маркеры `<<<<` в секции сломали бы проекцию и поиск |

## 7. Оценка

43 задачи, 8–10 недель; план `cloud-remote-2026-10` (префикс `CLO`), задачи и DoD —
§11. Решения Q-007…Q-017 и сверка со схемой добавили к первоначальным 35 задачам
`rank`, склейку ключей, tombstone поддерева, аренду checkout, эпохи, развёртывание,
переписывание трёх bulk-мест и решения по подсказкам.

| Секция | Содержание | Задач |
|---|---|---|
| A. Идентичность и журнал | реестр классов, миграции `uid` и `rank`, HLC, хук захвата, снятие bulk в authored, гейты | 9 |
| B. Changeset и file-remote | формат, `RemoteStore` + `FileRemote`, push, pull с fast-forward, дайджест, CLI и MCP | 7 |
| C. Слияние и конфликты | `sync_conflict`, LWW, 3-way, статус, рёбра, tombstone, ключи, ревизии, подсказки, куратор, сходимость | 11 |
| D. S3, clone, аренды, эпохи | `S3Remote`, снапшоты и clone, аренда номеров, аренда checkout, эпохи, `restricted` | 6 |
| E. Умный remote и доступ | доступ, `/api/sync/v1`, Bearer и rate limit, роли, CLI доступа, Caddy | 6 |
| F. Поверхности и документация | рутина `sync`, ARCHITECTURE/DATA_MODEL, capability и скилл, финальный прогон | 4 |

A → B → C строго последовательны; D и E параллельны после B; F идёт по ходу.
Предусловия вне плана — STO-018 ✅, STO-024 ✅, STO-023 ✅, STO-029 ✅, STO-030 ✅.

## 8. Критерий успеха

1. Две реплики клонированы с одного S3-remote; обе офлайн правят **одну и ту же**
   задачу (одна — `title`, другая — `status`) и одну секцию (пересекающиеся строки);
   каждая создаёт по новой задаче.
2. Обе делают `pull` + `push`. Итог: правки разных полей слились без конфликта;
   секция дала ровно один `sync_conflict`, видимый в `curator_next`; новые задачи
   получили непересекающиеся постоянные номера, провизорные имена резолвятся через
   алиас.
3. После `sync resolve` и ещё одного цикла `sync verify` на обеих репликах
   показывает один дайджест.
4. На умном remote токен с ролью reader получает 403 на push; changeset с чужим
   `author` отвергается.
5. При настроенном remote второй `task_checkout` той же задачи получает отказ до
   начала работы; `restricted`-документ после push на remote не найден.

## 9. Открытые вопросы

Заведены сущностями со связью `relation=blocks` на этот RFC; все десять решены
владельцем 2026-10-10 по сценариям «ноутбук A / десктоп B офлайн → push / pull»,
текст выше приведён к решениям. Везде выбран рекомендованный вариант; к Q-013
владелец добавил правило про `doc import`. Дайджест `sync verify` (§3.7) —
техническое определение, отдельным вопросом не заводился.

| Вопрос | Тема | Решение |
|---|---|---|
| Q-007 | совпадение натуральных ключей | имя-смысл — склейка + `entity_alias`; буква `plan_section` — перебуквление (§3.3) |
| Q-008 | удаление и DB-каскады | tombstone родителя; потомок под мёртвым родителем → конфликт со снимком (§3.7) |
| Q-009 | порядок элементов | дробный `rank`, `position` — local (§3.2, §4) |
| Q-010 | ревизии и блокировка | линейная история, `revision_id` общие, parent = локальная голова (§3.7) |
| Q-011 | checkout на репликах | замок через remote CAS, `--offline` — провизорный (§3.6) |
| Q-012 | эволюция схемы | эпохи: push до миграции, снапшот эпохи, `export-pending` (§3.6) |
| Q-013 | markdown и git | каналы независимы; `doc import` — только новые файлы и инициализация (§3.2, §4) |
| Q-014 | выставление умного remote | Caddy + TLS на 443, rate limit, журнал отказов (§3.8) |
| Q-015 | restricted / confidential | `restricted` — local построчно, не уезжает; уже уехавший документ при повышении отзывается у получателей одной операцией, понижение — первый upsert всего документа (§3.2, CLO-046) |
| Q-016 | ADR-016 и ADR-010 | уточняет; Postgres опционален, RFC 23 §A — deferred (§4) |
| Q-017 | решения по подсказкам ссылок | `link_suggestion.state ≠ pending` — authored по детерминированному ключу (§3.2); решено 2026-10-11 |

Сверка RFC со схемой 2026-10-11 (после решения Q-007…Q-016) расширила области
применения решений, не меняя их: Q-007 — ещё восемь имён-смыслов и класс
«арендованный номер», Q-009 — одиннадцать мест порядка вместо шести, Q-008 — 80
каскадов вместо 74. Открытых блокирующих вопросов у RFC нет.

## 10. Контракты

Контракты написаны от того, что код делает сегодня (§2, строка «Модель ошибок»).
Где документация описывает целевое, а кода нет (`CodDocError`, exit 2–7, Bearer),
это сказано явно. Имена ниже — обязательные: задачи §11 ссылаются на них.

### 10.1 Схема

Новые таблицы (DDL для SQLite; `TEXT` для ULID/uuid, время — `DATETIME` UTC):

```sql
-- 0049_sync_identity
CREATE TABLE replica (
  uid         TEXT PRIMARY KEY,               -- uuid7
  project_id  INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  is_self     BOOLEAN NOT NULL DEFAULT 0,     -- ровно одна is_self=1 на проект
  label       TEXT,                           -- 'dakh@macbook'
  created     DATETIME NOT NULL
);
CREATE UNIQUE INDEX uq_replica_self ON replica(project_id) WHERE is_self = 1;

CREATE TABLE sync_op (
  op_id        TEXT PRIMARY KEY,              -- ULID
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  replica_uid  TEXT NOT NULL,
  hlc          TEXT NOT NULL,                 -- '<ms:013d>.<counter:04d>.<replica_uid>'
  entity_kind  TEXT NOT NULL,                 -- имя таблицы из реестра
  entity_uid   TEXT NOT NULL,
  kind         TEXT NOT NULL CHECK (kind IN ('upsert','delete')),
  fields       TEXT NOT NULL,                 -- JSON: {колонка: новое значение}, FK как uid
  base         TEXT NOT NULL,                 -- JSON: {колонка: прежнее значение}
  author       TEXT NOT NULL,                 -- '<kind>:<id>' (ADR-012)
  changeset    TEXT                           -- sha256 или NULL = неупакована
);
CREATE INDEX ix_sync_op_pending ON sync_op(project_id) WHERE changeset IS NULL;
CREATE INDEX ix_sync_op_entity  ON sync_op(entity_kind, entity_uid);

CREATE TABLE changeset (                      -- локальный индекс известных changeset'ов
  sha          TEXT PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  parents      TEXT NOT NULL,                 -- JSON-массив sha
  replica_uid  TEXT NOT NULL,
  actor        TEXT NOT NULL,
  epoch        TEXT NOT NULL,                 -- голова миграций эпохи
  hlc_max      TEXT NOT NULL,
  digest       TEXT NOT NULL,                 -- дайджест после применения (§3.7)
  applied_at   DATETIME                       -- NULL = известен, не применён
);

CREATE TABLE sync_ref (                       -- что реплика знает о remote
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  remote       TEXT NOT NULL,                 -- 'origin'
  name         TEXT NOT NULL,                 -- 'main'
  sha          TEXT,                          -- последняя виденная голова
  etag         TEXT,                          -- для CAS
  fetched_at   DATETIME,
  PRIMARY KEY (project_id, remote, name)
);

CREATE TABLE sync_lease (                     -- арендованный остаток номеров
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  prefix       TEXT NOT NULL,                 -- 'ADO', 'ADR', 'US', 'SCN', 'Q'
  next_number  INTEGER NOT NULL,
  end_number   INTEGER NOT NULL,              -- включительно
  leased_at    DATETIME NOT NULL,
  PRIMARY KEY (project_id, prefix)
);

CREATE TABLE entity_alias (
  project_id   INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  entity_kind  TEXT NOT NULL,
  alias        TEXT NOT NULL,                 -- провизорный ID или склеенный uid
  uid          TEXT NOT NULL,                 -- живая сущность
  reason       TEXT NOT NULL CHECK (reason IN ('pinned','merged')),
  created      DATETIME NOT NULL,
  PRIMARY KEY (project_id, entity_kind, alias)
);

-- во всех authored-таблицах (кроме тег-таблиц с составным PK):
ALTER TABLE <t> ADD COLUMN uid TEXT;          -- бэкфилл, затем:
CREATE UNIQUE INDEX uq_<t>_uid ON <t>(uid);
-- NOT NULL обеспечивает хук и тест, не ограничение: ADD COLUMN NOT NULL без
-- DEFAULT на SQLite невозможен, а пересоздавать document нельзя (§4).

-- 0050_sync_rank — в 11 местах порядка (§3.2):
ALTER TABLE <t> ADD COLUMN rank TEXT;         -- document: node_rank
-- 0051_sync_conflict — sync_conflict (§3.7, с колонкой kind):
--   kind TEXT NOT NULL CHECK (kind IN ('field','text','status','delete_edit',
--        'key_merge','double_checkout'))
-- 0053_sync_access — actor, api_token, project_member (§3.8; 0052 занят CLO-045).
```

Remote-раскладка — §3.5; `meta.json`:
`{"v": 1, "project_uid", "epoch", "schema_min", "created", "snapshot_every": 5000,
"snapshot": {"epoch", "sha", "ops"}}` — `snapshot` — указатель на последний снапшот
и число операций журнала в нём (CLO-029). `epoch` и `schema_min` — имя ревизии,
открывшей эпоху; поднимает их первый push новой эпохи тем же CAS, что двигает
указатель (CLO-032).
Аренда номеров на remote — `leases/<PREFIX>`:
`{"next": 261, "block": 20, "holders": {"<replica_uid>": [241, 260]}}`.
Замок checkout — `leases/checkout/<task_uid>`:
`{"replica_uid", "actor", "at", "ttl_minutes": 30}`.

### 10.2 Реестр классов

```python
# cod_doc/infra/sync_registry.py — stdlib + domain, без services (слои)
class SyncClass(StrEnum):
    AUTHORED = "authored"; JOURNAL = "journal"; DERIVED = "derived"; LOCAL = "local"

class KeyKind(StrEnum):
    NAME = "name"          # склейка (Q-007)
    LABEL = "label"        # перебуквление (plan_section.letter)
    LEASED = "leased"      # арендованный номер

@dataclass(frozen=True, slots=True)
class NaturalKey:
    columns: tuple[str, ...]          # ('project_id', 'doc_key')
    kind: KeyKind

@dataclass(frozen=True, slots=True)
class TableSpec:
    table: str
    cls: SyncClass
    local_columns: frozenset[str] = frozenset()   # projection_hash, position, …
    authored_columns: frozenset[str] = frozenset()  # для DERIVED с authored-полем (link_suggestion.state)
    row_is_local: Callable[[Mapping[str, object]], bool] | None = None  # restricted
    natural_keys: tuple[NaturalKey, ...] = ()
    logical_fks: Mapping[str, str] = field(default_factory=dict)  # {'topic_id': 'adr_topic'}
    human_id_refs: Mapping[str, str] = field(default_factory=dict)  # {'task_id': 'task'}
    rank_column: str | None = None                # 'rank' / 'node_rank'
    deterministic_uid: tuple[str, ...] | None = None  # концы ребра для uuid5
    text_columns: frozenset[str] = frozenset()    # 3-way merge вместо LWW

REGISTRY: Mapping[str, TableSpec]
def spec_for(table: str) -> TableSpec            # KeyError → баг, ловит тест
def authored_tables() -> list[TableSpec]
```

Гейт CLO-001: `set(Base.metadata.tables) == set(REGISTRY)`; для каждой
authored-таблицы все FK-колонки покрыты (`logical_fks` или настоящий FK).

### 10.3 Захват

```python
# cod_doc/infra/sync_capture.py
def install(session_factory: sessionmaker[Session]) -> None
    # event.listen(Session, "before_flush") для фабрики; идемпотентна.
    # Зовётся из infra/db.py::make_session_factory — один раз на фабрику.

def record(session: Session, *, table: str, entity_uid: str,
           kind: Literal["upsert", "delete"], fields: Mapping[str, object],
           base: Mapping[str, object], author: str) -> str   # → op_id
    # Явный захват для разрешённых bulk-мест (§3.4). Та же транзакция.

def suspended(session: Session) -> ContextManager[None]
    # Только для применения чужих операций (§10.6): иначе каждый pull
    # порождал бы эхо-операции. Вне sync_service вызов — нарушение (гейт).
```

Правила хука: `author` берётся из `session.info["author"]`, который ставят
сервисы через существующий вход в транзакцию; нет автора → `"system:unknown"` и
предупреждение в лог, не исключение (ADR-012 не делает автора обязательным).
Строка с `row_is_local(row) == True` не пишется. Операция без изменённых
authored-колонок (только `local`) не пишется.

### 10.4 Changeset

```json
{
  "v": 1,
  "project": "<project_uid>",
  "parents": ["<sha256>"],
  "replica": "<replica_uid>",
  "actor": "<handle>",
  "epoch": "0050_sync_rank",
  "schema_head": "0050_sync_rank",
  "hlc_max": "<hlc>",
  "digest": "<sha256 состояния после применения>",
  "ops": [{"op_id", "hlc", "entity_kind", "entity_uid", "kind", "fields", "base", "author"}]
}
```

Каноническая форма: UTF-8, ключи по алфавиту на всех уровнях,
`separators=(",", ":")`, `ensure_ascii=False`, время — `YYYY-MM-DDTHH:MM:SS.ffffffZ`,
`ops` по `(hlc, op_id)`. Адрес — `sha256(canonical_json)`; хранится как
`b"CDC1" + zstd(canonical_json)` (4 байта — версия формата, место под шифрование).
Чтение проверяет sha содержимого и отвергает несовпадение (`SYNC-102`).

**Маркер разрешения в v1** (CLO-045, §3.7). `entity_kind = "sync_resolution"`
зарезервирован (таблицы с таким именем в схеме нет): операция с ним — маркер,
`entity_uid` — его uid (uuid7), `kind = "upsert"`, `base = {}`, `fields` —
подпись конфликта `{"entity_kind", "entity_uid", "field", "kind"}`; разрешившая
реплика — реплика `hlc`, время разрешения — `hlc`. Ключи операции и
каноническая форма не меняются, поэтому версия формата остаётся `1`
(`CDC1`, `"v": 1`): байты и адреса прежних changeset'ов те же (golden-тест),
`decode` прежний. Реплика без CLO-045 получает такой changeset вместе со
`schema_head` 0052 и отвечает `SYNC-104` («cod-doc update») до применения, а
не разбирает маркер как строку незнакомой таблицы.

### 10.5 Транспорт

```python
# cod_doc/infra/remote/base.py
@dataclass(frozen=True) class RefValue: sha: str; etag: str
@dataclass(frozen=True) class SnapshotRef: epoch: str; sha: str; key: str

class RemoteStore(Protocol):
    def get_ref(self, project_uid: str, name: str) -> RefValue | None: ...
    def cas_ref(self, project_uid: str, name: str,
                expected: RefValue | None, new_sha: str) -> RefValue | None: ...
        # None = CAS отвергнут (не исключение); иначе новая RefValue
    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]: ...
    def put_object(self, project_uid: str, sha: str, data: bytes) -> None: ...  # идемпотентно
    def get_object(self, project_uid: str, sha: str) -> bytes: ...
    def get_json(self, project_uid: str, key: str) -> tuple[dict, str] | None: ...  # (тело, etag)
    def cas_json(self, project_uid: str, key: str, expected_etag: str | None,
                 body: dict) -> str | None: ...   # leases/*, meta.json
    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool: ...
    def list_prefix(self, project_uid: str, prefix: str) -> list[str]: ...
        # CLO-048: ключи JSON прямо под "leases/…/" (без подкаталогов, надгробие S3 — не ключ),
        # по возрастанию; больше MAX_LIST_KEYS (100 000) — SYNC-101.
        # FileRemote: scandir под тем же спуском O_NOFOLLOW; S3: ListObjectsV2 c Delimiter="/",
        # не больше 100 страниц
    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None: ...
    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None: ...

def open_remote(url: str, *, credentials: Credentials | None) -> RemoteStore
    # file:// → FileRemote, s3:// → S3Remote, https:// → HttpRemote; иначе SYNC-110
```

Ошибки транспорта: сеть / 5xx → `RemoteUnavailableError` (`SYNC-101`), нет объекта
или битый sha → `ObjectMissingError` (`SYNC-102`), 401/403 → `RemoteAuthError`
(`SYNC-201`/`SYNC-202`). CAS-отказ — не ошибка, а `None`.

### 10.6 Сервис

`cod_doc/services/sync_service/` — пакет по образцу `story_service/`. Функции,
делающие сетевой обмен, принимают **фабрику сессий**, а не сессию: сеть идёт вне
транзакции (§3.9).

```python
@dataclass(frozen=True) class PushResult:
    changesets: list[str]; ops: int; head: str; retries: int; pinned: dict[str, str]
    snapshot: str | None                                              # CLO-029
    epoch_opened: str | None                                          # CLO-032
@dataclass(frozen=True) class PullResult:
    applied: list[str]; ops: int; fast_forward: bool; conflicts: list[str];
    aliases: list[str]; head: str
    rebased: str | None                                               # CLO-050
@dataclass(frozen=True) class SyncStatus:
    remote: str | None; head_local: str | None; head_remote: str | None
    ahead: int; behind: int; pending_ops: int; open_conflicts: int
    epoch_local: str; epoch_remote: str | None; restricted_local: int
    restricted_rows: int                   # строки этих документов и их тела, CLO-033
    leases: dict[str, tuple[int, int]]; drift_hint: str | None
    clone_url: str | None                         # <URL remote>/<project_uid>, CLO-029
@dataclass(frozen=True) class VerifyResult: local: str; remote: str | None; equal: bool

def push(sf, project: str, *, remote: str = "origin", author: str,
         max_retries: int = 3) -> PushResult
def fetch(sf, project: str, *, remote: str = "origin") -> list[str]   # новые sha
def pull(sf, project: str, *, remote: str = "origin", author: str,
         dry_run: bool = False) -> PullResult
def status(sf, project: str, *, remote: str = "origin", verify: bool = False) -> SyncStatus
def verify(sf, project: str, *, remote: str = "origin") -> VerifyResult
def clone(url: str, *, root: Path | None = None, slug: str | None = None,
          author: str) -> CloneResult                                 # CLO-029
def export_pending(sf, project: str, *, path: Path) -> int            # → число операций
def render_pending(sf, project: str) -> tuple[bytes, int]             # stdout, CLO-032

# конфликты — обычные write-сервисы (ревизия + activity, ADO-040):
def list_conflicts(session, project_id: int, *, status: str = "open") -> list[dict]
def resolve_conflict(session, project_id: int, conflict_uid: str, *,
                     take: Literal["ours", "theirs"] | str, author: str,
                     reason: str | None = None) -> dict

# аренды — зовутся из существующих сервисов:
def lease_number(session, project_id: int, prefix: str) -> str        # 'ADO-241' или 'ADO-~k3f2'
def checkout_lease(sf, project: str, task_uid: str, *, actor: str,
                   offline: bool = False) -> None                     # SYNC-108/109
def release_lease(sf, project: str, task_uid: str, *, actor: str) -> None
```

Встраивание в существующее: `_next_task_id` / `_next_adr_id` /
`_next_scenario_id` / `next_story_id` / `_next_question_id` при настроенном remote
зовут `lease_number`; без remote — работают как сегодня. `checkout_service.checkout`
при настроенном remote сначала зовёт `checkout_lease`. Гейт паритета —
`tests/services/test_sync_mutation_surface_parity.py` (`ServiceSpec(name="sync_service", …)`).

### 10.7 MCP

Семейство `sync_*`, профили `standard`/`full` (в `agent` — нет; сигнал — через
`curator_next`). Строка семейства — в `docs/mcp-integration.md`, числа пересчитает
`scripts/regen.sh`. Ошибки — `ValueError` с сообщением, начинающимся с кода:
`"SYNC-108: задача ADO-230 взята dakh@macbook в 14:02"` (конвенция MCP-тулов
сегодня, §2).

| Тул | Параметры | Ответ |
|---|---|---|
| `sync_status` | `project`, `remote="origin"`, `verify=False` | `SyncStatus` как dict |
| `sync_pull` | `project`, `remote="origin"`, `author="mcp"`, `dry_run=False` | `PullResult` |
| `sync_push` | `project`, `remote="origin"`, `author="mcp"` | `PushResult` |
| `sync_conflicts` | `project`, `status="open"` | `[{uid, entity_kind, entity_uid, kind, field, ours, theirs, base, created}]` |
| `sync_resolve` | `project`, `conflict_uid`, `take`, `author="mcp"`, `reason=None` | конфликт после разрешения + `revision_id` |
| `task_checkout` (есть) | + `offline: bool = False` | как сегодня |

`remote add`, `clone`, `member`, `token` в MCP нет намеренно (§3.9).

### 10.8 CLI

Новые верхние команды и группа `sync`; опция `-p/--project` обязательна, где есть
проект; `--json` печатается через `click.echo` (ADO-176); импорты `infra`/`services`
— в телах команд (ADO-179); `_cod-doc` перегенерирует `scripts/regen.sh`.

| Команда | Опции | Вывод / код |
|---|---|---|
| `cod-doc remote add NAME URL -p P` | `--kind auto\|file\|s3\|https` | пишет `remotes:` в `~/.cod-doc/config.yaml`; для `s3://` — предупреждение «без разграничения доступа» |
| `cod-doc remote list/remove` | `-p P`, `--json` | |
| `cod-doc remote login NAME -p P` | `--token-stdin` | токен в Keychain (macOS) или `~/.cod-doc/credentials` (0600) |
| `cod-doc clone URL [DIR]` | `--project SLUG`, `--author`, `--json` | URL — `<URL remote>/<project_uid>`; DIR по умолчанию `./<slug>`; регистрирует проект и remote `origin` |
| `cod-doc fetch/pull/push -p P` | `--remote NAME`, `--json`, `pull --dry-run` | |
| `cod-doc sync status -p P` | `--verify`, `--json` | |
| `cod-doc sync conflicts -p P` | `--status open\|resolved\|all`, `--json` | |
| `cod-doc sync resolve UID -p P` | `--take ours\|theirs\|--text-file F`, `--reason` | |
| `cod-doc sync verify / log / export-pending` | `-p P`, `log -n 20`, `export-pending --out F` | `export-pending` без `--out` — в stdout; `--out` — новый файл 0600 (существующий не перезаписывается) |
| `cod-doc member add/list/remove`, `cod-doc token create/revoke` | только против `https://`-remote, роль admin | `token create` печатает токен один раз |
| `cod-doc task checkout ID` (есть) | `+ --offline` | |

Коды выхода — как сегодня: `0` успех, `1` любая ошибка (`click.ClickException`),
сообщение начинается с `SYNC-NNN`. Целевая карта exit 2–7 из ARCHITECTURE §10 —
не этот RFC.

### 10.9 REST `/api/sync/v1`

Все маршруты — `Authorization: Bearer <token>`; роль проверяется по
`project_member`. Ошибки — `{"error": {"code": "SYNC-NNN", "message": "…",
"details": {…}}}`: у sync-клиента машинный разбор кода, поэтому не FastAPI-шный
`{"detail"}` из `/api/v1`.

| Метод и путь | Роль | Запрос | Ответ |
|---|---|---|---|
| `GET /projects/{uid}/meta` | reader | — | `meta.json` |
| `GET /projects/{uid}/refs/{name}` | reader | — | `{sha}` + `ETag` · 404 нет ref |
| `PUT /projects/{uid}/refs/{name}` | writer | `{sha}`, `If-Match` / `If-None-Match: *` | 200 `{sha}` + `ETag` · 412 CAS отвергнут · 422 `SYNC-203`/`SYNC-104` |
| `POST /projects/{uid}/objects/has` | reader | `{shas: [...]}` | `{present: [...]}` |
| `PUT /projects/{uid}/objects/{sha}` | writer | тело `CDC1+zstd` | 201 · 200 уже есть · 400 `SYNC-102` sha не совпал · 422 `SYNC-203` чужой author |
| `GET /projects/{uid}/objects/{sha}` | reader | — | тело · 404 |
| `GET /projects/{uid}/json/{key}` | reader | `key ∈ leases/*` | тело + `ETag` · 404 |
| `PUT /projects/{uid}/json/{key}` | writer | тело, `If-Match` / `If-None-Match: *` | 200 + `ETag` · 412 |
| `DELETE /projects/{uid}/json/{key}` | writer | `If-Match` | 204 · 412 |
| `GET /projects/{uid}/snapshots/latest` | reader | — | `{epoch, sha}` + тело по `Accept` |
| `PUT /projects/{uid}/snapshots/{epoch}/{sha}` | writer | тело | 201 |
| `PUT /projects/{uid}/meta` | admin | `{schema_min}` | 200 |

Без токена — 401 `SYNC-201`; роль не позволяет — 403 `SYNC-202`; частота — 429
`SYNC-204` с `Retry-After`. Каждый 401/403/429 пишет `auth.denied`.

### 10.10 Коды ошибок

| Код | Когда | Исключение (stdlib-основа) | MCP / CLI / REST |
|---|---|---|---|
| `SYNC-001` | bulk-запись в authored без `record` (ловит гейт, в рантайме — `assert` в тестах); растяжка упаковки: restricted-строка в changeset'е или снапшоте (`RestrictedLeakError`, CLO-033) | — | — |
| `SYNC-101` | remote недоступен | `RemoteUnavailableError(ConnectionError)` | ValueError / 1 / 502 |
| `SYNC-102` | объект не найден или sha не совпал | `ObjectMissingError(LookupError)` | ValueError / 1 / 404, 400 |
| `SYNC-103` | push не прошёл после `max_retries` | `PushRejectedError(RuntimeError)` | ValueError / 1 / — |
| `SYNC-104` | схема changeset новее своей / push старше `schema_min` | `SchemaMismatchError(RuntimeError)` | ValueError / 1 / 422 |
| `SYNC-105` | эпоха remote новее эпохи реплики: push с незапушенными операциями, pull (CLO-032); changeset эпохи старше `meta.epoch` среди скачанных, незапушенные операции у реплики, встающей на снапшот эпохи (CLO-050) | `EpochPassedError(RuntimeError)` | ValueError / 1 / — |
| `SYNC-106` | `cod-doc update` перед миграцией, открывающей эпоху, при незапушенных операциях проекта с remote; или проверка не прочитала БД (CLO-032) | `PendingOpsError(RuntimeError)` | — / 1 / — |
| `SYNC-107` | аренда номеров исчерпана офлайн (не ошибка: выдаётся провизорный ID, код — в предупреждении) | — | warning |
| `SYNC-108` | замок checkout занят; аренда не по форме — тоже занят (CLO-048) | `CheckoutLeaseHeldError(CheckoutConflictError)`; не по форме — `CheckoutLeaseMalformedError(CheckoutLeaseHeldError)` | ValueError / 1 / 409 |
| `SYNC-109` | remote недоступен для checkout без `--offline` | `CheckoutLeaseUnavailableError(RemoteUnavailableError)` | ValueError / 1 / — |
| `SYNC-110` | неизвестная схема URL / remote не настроен | `RemoteConfigError(ValueError)` | ValueError / 1 / — |
| `SYNC-201` | нет или неверный токен | `RemoteAuthError(PermissionError)` | ValueError / 1 / 401 |
| `SYNC-202` | роль не позволяет | `RemoteAuthError` | ValueError / 1 / 403 |
| `SYNC-203` | `actor`/`author` changeset'а не совпал с токеном | `AuthorMismatchError(PermissionError)` | — / 1 / 422 |
| `SYNC-204` | превышена частота запросов | `RemoteRateLimitedError(RemoteUnavailableError)` | ValueError / 1 / 429 |
| `SYNC-205` | узел требует аутентификацию (`COD_DOC_REQUIRE_AUTH=1`), а хранилища доступа нет (CLO-036) | — | — / — / 503 |
| `SYNC-301` | `take` не подходит к виду конфликта | `ConflictResolveError(ValueError)` | ValueError / 1 / — |
| `SYNC-302` | конфликт не найден или уже разрешён | `ConflictNotFoundError(LookupError)` | ValueError / 1 / — |

Исключения живут в `cod_doc/services/sync_service/errors.py` и несут атрибут
`code`; при появлении `cod_doc/errors.py` (ARCHITECTURE §10) их перевешивают на
`CodDocError` без смены кодов.

### 10.11 События activity

| Событие | Когда | `scope_kind` / `payload` |
|---|---|---|
| `sync.pushed` | успешный push | `project` / `{remote, changesets, ops, head}` |
| `sync.pulled` | pull с применёнными операциями | `project` / `{remote, applied, ops, conflicts, fast_forward}` |
| `sync.conflict_opened` / `sync.conflict_resolved` | конфликт | `<entity_kind>` / `{conflict_uid, kind, take?}` |
| `sync.alias_created` | склейка или закрепление | `<entity_kind>` / `{alias, uid, reason}` |
| `plan_section.relettered` | перебуквление | `plan` / `{from, to, section_uid}` |
| `sync.epoch_opened` | первый push новой эпохи | `project` / `{epoch, snapshot}` |
| `sync.cloned` | clone завершён (CLO-029) | `project` / `{remote, url, snapshot, head, applied, ops}` |
| `sync.epoch_rebased` | отставшая реплика встала на снапшот эпохи (CLO-050) | `project` / `{epoch, snapshot, from, from_epoch, changesets, rows}` |
| `task.checkout_leased` / `task.checkout_lease_released` | аренда замка | `task` / `{remote, offline}` |
| `task.checkout_lease_taken_over` | checkout перехватил истёкшую чужую аренду (CLO-048) | `task` / `{remote, previous_agent, previous_replica, previous_acquired_at, previous_ttl_minutes}` |
| `auth.denied` (сервер) | 401/403/429 | `project` / `{token_label?, ip, path, code}` |

Применение чужих операций **не** порождает событий ни для каждой операции, ни
ревизий заново: ревизии приезжают журналом (§3.7), а `sync.pulled` — одно на pull.

### 10.12 Конфигурация и секреты

`ProjectEntry` (`cod_doc/config.py:48`, `extra="allow"`) получает поле
`remotes: dict[str, RemoteSpec]`, `RemoteSpec = {url: str, kind: str}`. Токенов в
`config.yaml` нет — только в Keychain / `~/.cod-doc/credentials`:

```yaml
# ~/.cod-doc/credentials (chmod 600)
https://sync.example.dev:
  token: cdt_…            # показывается один раз при token create
s3://cod-doc-remotes:
  access_key: …           # или стандартная цепочка AWS/MinIO
  secret_key: …
```

`SECRET_FIELDS` (`config.py:83`) не расширяется: в конфиге секретов нет. Сервер
хранит только `sha256(token)`.

## 11. Декомпозиция и DoD

План `cloud-remote-2026-10` (префикс `CLO`), 43 задачи, все заводятся в статусе
`backlog` до приёмки ADR-016/017. Секции A → B → C последовательны; D и E
параллельны после B; F — по ходу. Предусловия вне плана: **STO-018 ✅, STO-024 ✅, STO-023 ✅, STO-029 ✅, STO-030 ✅**.

### 11.1 DoD

**Задача** закрыта, когда выполнено всё из AGENTS.md (§DoD) и следующее:

1. Acceptance задачи выполнен и проверен **тестом, который падал до правки**;
   сценарии, связанные с задачей, имеют тест на свой `expected`.
2. Новая или изменённая таблица — в `sync_registry`, гейт CLO-001 не падает.
3. Запись в authored-данные идёт через `Session` или `sync_capture.record`; гейт
   CLO-008 не падает.
4. Миграция — симметричные `upgrade`/`downgrade`, smoke `upgrade head` +
   `downgrade -1`, тест сохранности данных на предыдущей ревизии.
5. Новый тул или команда — на обеих поверхностях, строка в
   `docs/mcp-integration.md`, `scripts/regen.sh` прогнан, паритет-тест обновлён.
6. Ошибки — только коды из §10.10; новый код сначала вписывается в RFC.
7. CI зелёный на PR (не локальный прогон, ADO-070).
8. Задача закрыта `task_complete` с `commit_sha`, затем `task_release`.

**Секция** закрыта, когда все её задачи `done`, сценарии секции имеют тесты,
CI на `main` зелёный и написан аудит
`docs/system/audit/<дата>-cloud-remote-section-<x>.md` (TL;DR, deliverables,
находки F1…, acceptance, следующий шаг).

**RFC** реализован, когда критерии §8 воспроизведены на двух реальных машинах
(MacBook и десктоп) с MinIO на Hermes и с умным remote за Caddy, итоговый аудит
опубликован, а RFC переведён в `implemented` по RFC 30.

### 11.2 Секции и задачи

Обозначения: тип (f — feature, m — migration, r — refactor, t — test, d — docs),
приоритет (C — critical, H — high, M — medium, L — low; правило «≤ 20% high и
critical» из `task-standard` соблюдено: 8 из 43), блокеры, сценарии.

**A. Идентичность и журнал**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-001 | Implement: реестр классов `sync_registry` и гейт полноты | f · M | — | все 64 таблицы классифицированы по §3.2; тест падает на модели без записи и на FK, не покрытом `logical_fks`; `row_is_local` для `restricted`, `authored_columns` для `link_suggestion.state` | `cod_doc/infra/sync_registry.py`, `tests/infra/test_sync_registry_complete.py` |
| CLO-002 | Migration: `0049_sync_identity` — `uid`, служебные таблицы | m · H | CLO-001, STO-018 ✅, STO-023 ✅, STO-024 ✅, STO-029 ✅, STO-030 ✅ | DDL §10.1; бэкфилл uuid7, у рёбер — uuid5; тест сохранности на `0048`: число секций, ссылок, задач, рёбер совпадает; downgrade симметричен | `cod_doc/infra/migrations/versions/…0049_sync_identity.py`, `cod_doc/infra/models/*.py`, `tests/infra/test_migration_0048_preserves_data.py` |
| CLO-003 | Migration: `0050_sync_rank` — `rank` в 11 местах, снятие 4 UNIQUE | m · M | CLO-002 | порядок внутри каждого родителя до и после совпадает; у четырёх пересоздаваемых таблиц нет CASCADE-детей (проверка в тесте); downgrade восстанавливает UNIQUE | `…0050_sync_rank.py`, `tests/infra/test_migration_0050_rank.py` |
| CLO-004 | Refactor: выдача и чтение порядка через `rank` | r · M | CLO-003 | вставка между соседями пишет один `rank`; `position` пересчитывается; аллокаторы `document_repo.py:208`, `story_repo.py:182`, `scenario_repo.py:111` переписаны; SCN-093 | `cod_doc/infra/repositories/*.py`, `cod_doc/services/plan_service/sections.py`, `cod_doc/services/doc_tree_service.py` |
| CLO-005 | Implement: HLC и `replica` (`is_self`) | f · M | CLO-002 | HLC монотонен при откате часов назад; одна `is_self` на проект; формат из §10.1 | `cod_doc/infra/hlc.py`, `tests/infra/test_hlc.py` |
| CLO-006 | Implement: хук `before_flush` → `sync_op` | f · C | CLO-001, CLO-005 | контракт §10.3: `fields`/`base` только authored-колонок, FK как uid, `restricted` и local не пишутся, `suspended` глушит хук; SCN-088 | `cod_doc/infra/sync_capture.py`, `cod_doc/infra/db.py`, `tests/infra/test_sync_capture.py` |
| CLO-007 | Refactor: убрать bulk-запись в authored | r · M | CLO-006 | `set_steps` — ORM-дифф с сохранением `uid`; upsert внешних находок — с `record`; `state` подсказки — ORM; поведение тулов не меняется | `cod_doc/services/scenario_service/steps.py`, `cod_doc/services/finding_service/dedup.py`, `cod_doc/services/link_service/semantic.py` |
| CLO-008 | Test: AST-гейт «нет bulk в authored» | t · M | CLO-007 | гейт падает на `session.execute(update(TaskModel)…)` в сервисе и пропускает функцию с `record`; allowlist пуст | `tests/infra/test_sync_capture_no_bulk_authored.py` |
| CLO-009 | Test: каждая authored-мутация пишет `sync_op` | t · M | CLO-006 | для каждой write-функции из спеков паритета создаётся минимум одна операция с верным `entity_uid` | `tests/infra/test_sync_capture_write_paths.py` |

**B. Changeset и file-remote**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-010 | Implement: формат changeset v1 | f · M | CLO-006 | каноникализация §10.4 стабильна (golden-тест); `CDC1+zstd`; чтение отвергает чужой sha с `SYNC-102` | `cod_doc/services/sync_service/changeset.py` |
| CLO-011 | Implement: `RemoteStore` и `FileRemote` | f · M | CLO-010 | протокол §10.5; два процесса делают CAS одновременно — проходит ровно один | `cod_doc/infra/remote/base.py`, `cod_doc/infra/remote/file.py` |
| CLO-012 | Implement: `sync_service.push` | f · H | CLO-011 | упаковка хвоста, `has`/`put`, CAS, до 3 повторов через pull; `SYNC-103` после; SCN-083 | `cod_doc/services/sync_service/push.py` |
| CLO-013 | Implement: `fetch`/`pull` с fast-forward и применением | f · H | CLO-012 | применение под `suspended`, uid → row_id, одна транзакция на changeset, `SYNC-104` на новую схему; SCN-085 | `cod_doc/services/sync_service/pull.py`, `apply.py` |
| CLO-014 | Implement: дайджест и `verify` | f · M | CLO-013 | дайджест §3.7 не зависит от `row_id`, `position`, parent ревизий; две реплики после fast-forward равны | `cod_doc/services/sync_service/digest.py` |
| CLO-015 | Implement: CLI `remote`, `fetch/pull/push`, `sync status/log` | f · M | CLO-013 | таблица §10.8; `--json` парсится; `remote add s3://` предупреждает | `cod_doc/cli/cmd_sync.py`, `cod_doc/cli/__init__.py` |
| CLO-016 | Implement: MCP `sync_status/pull/push` | f · M | CLO-015 | таблица §10.7; паритет-спек `sync_service`; строка в `docs/mcp-integration.md` | `cod_doc/mcp/tools/sync_tools.py`, `tests/services/test_sync_mutation_surface_parity.py` |

**C. Слияние и конфликты**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-017 | Migration + Implement: `sync_conflict` и сервис конфликтов | m · M | CLO-013 | `0051`; `list_conflicts`, `resolve_conflict` пишут ревизию и событие; `SYNC-301/302` | `…0051_sync_conflict.py`, `cod_doc/services/sync_service/conflicts.py` |
| CLO-018 | Implement: LWW по полю, поглощение равных, порядок по `rank` | f · H | CLO-017 | разные поля одной задачи сливаются без конфликта; равные значения не дают операции; SCN-081, SCN-093 | `cod_doc/services/sync_service/merge.py` |
| CLO-019 | Implement: 3-way merge текстов | f · M | CLO-017 | колонки `text_columns`; непересекающиеся ханки сливаются, пересекающиеся — конфликт `text` | `cod_doc/services/sync_service/text_merge.py` |
| CLO-020 | Implement: статус задачи через статус-машину | f · M | CLO-018 | недопустимая пара переходов и двойной офлайн-checkout → конфликт | `merge.py`, `cod_doc/services/task_status_machine.py` (только чтение) |
| CLO-021 | Implement: множества add-wins, детерминированный uid рёбер | f · M | CLO-018 | одно ребро с двух реплик — одна строка; удаление против добавления — ребро остаётся | `merge.py`, `cod_doc/infra/sync_registry.py` |
| CLO-022 | Implement: tombstone родителя и конфликт со снимком | f · H | CLO-017 | одна операция delete; потомок под мёртвым родителем → конфликт `delete_edit` со снимком; resolve theirs восстанавливает поддерево; SCN-092 | `cod_doc/services/sync_service/apply.py`, `conflicts.py` |
| CLO-023 | Implement: склейка имён-смыслов и перебуквление | f · M | CLO-018 | ключи §3.3; алиас и конфликт тел; перебуквление одинаково на репликах; SCN-090, SCN-091 | `cod_doc/services/sync_service/keys.py` |
| CLO-024 | Implement: чужие ревизии поверх локальной головы | f · M | CLO-013 | `revision_id` сохраняется, parent = локальная голова; `OptimisticLockError` у устаревшего клиента | `apply.py`, `cod_doc/services/revision_service.py` |
| CLO-025 | Implement: решения по подсказкам ссылок по ключу (Q-017) | f · M | CLO-007, CLO-013 | отклонённая на A подсказка после pull и пересчёта на B не всплывает | `cod_doc/services/link_service/semantic.py` |
| CLO-026 | Implement: `curator_next`, CLI и MCP по конфликтам | f · M | CLO-017 | пункт очереди выше Инбокса и ниже битых ссылок; `sync conflicts/resolve`, `sync_conflicts/sync_resolve`; SCN-089 | `cod_doc/services/curator_service.py`, `cmd_sync.py`, `sync_tools.py` |
| CLO-027 | Test: сходимость трёх реплик в любом порядке | t · M | CLO-018…CLO-024 | property-тест: перестановки порядка применения дают один дайджест; SCN-087 | `tests/services/sync/test_convergence.py` |

**D. S3, clone, аренды, эпохи**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-028 | Implement: `S3Remote` с conditional PUT | f · M | CLO-011 | `If-Match`/`If-None-Match`; тесты против MinIO в CI-сервисе; гонка CAS — один победитель | `cod_doc/infra/remote/s3.py`, `.github/workflows/ci.yml` |
| CLO-029 | Implement: снапшоты по эпохам и `cod-doc clone` | f · M | CLO-028, CLO-014 | снапшот каждые 5000 операций; clone = снапшот + хвост; дайджест равен remote; SCN-082 | `cod_doc/services/sync_service/snapshot.py`, `clone.py` |
| CLO-030 | Implement: аренда номеров и закрепление провизорных ID | f · M | CLO-012 | пять генераторов; провизорный `ADO-~xxxx`; закрепление переписывает `human_id_refs`; алиас резолвится в `task_get`; SCN-086 | `cod_doc/services/sync_service/leases.py`, генераторы ID из §2 |
| CLO-031 | Implement: аренда checkout через remote | f · H | CLO-028, CLO-020 | `SYNC-108/109`, `--offline`, release и TTL через remote; SCN-094 | `cod_doc/services/checkout_service.py`, `leases.py` |
| CLO-032 | Implement: эпохи и `export-pending` | f · M | CLO-029 | `update` с незапушенным → `SYNC-106`; первый push эпохи — снапшот и `schema_min`; SCN-095 | `cod_doc/services/update_service.py`, `snapshot.py` |
| CLO-033 | Implement: `restricted` не уезжает | f · M | CLO-006 | строки restricted нет ни в одном changeset; счётчик в `sync status`; предупреждение при повышении уехавшего; SCN-096 | `sync_registry.py`, `push.py` |

**E. Умный remote и доступ**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-034 | Migration + Implement: `actor`, `api_token`, `project_member` | m · M | CLO-002 | `0053` (CLO-049); токен хранится как sha256, показывается один раз; отзыв действует сразу | `…0053_sync_access.py`, `cod_doc/services/access_service.py` |
| CLO-035 | Implement: `/api/sync/v1` и `HttpRemote` | f · M | CLO-034, CLO-029 | маршруты §10.9; формат ошибок `{"error": {...}}`; HttpRemote проходит те же тесты, что FileRemote | `cod_doc/api/sync/`, `cod_doc/infra/remote/http.py` |
| CLO-036 | Implement: Bearer-гейт, rate limit, `auth.denied` | f · C | CLO-034 | закрывает `/api/sync/v1`, `/api/v1` и streamable-http MCP; constant-time; 401/403/429 пишут событие | `cod_doc/api/auth.py`, `cod_doc/api/server.py`, `cod_doc/mcp/server.py` |
| CLO-037 | Implement: роли и проверка `actor`/`author` на push | f · M | CLO-035, CLO-036 | reader → 403 `SYNC-202`; чужой author → 422 `SYNC-203`; агент не выше владельца; SCN-084 | `cod_doc/api/sync/routes.py`, `access_service.py` |
| CLO-038 | Implement: CLI `member`, `token`, `remote login` | f · M | CLO-034 | Keychain на macOS, иначе credentials 0600; `token create` печатает один раз | `cod_doc/cli/cmd_access.py` |
| CLO-039 | Docs: развёртывание умного remote за Caddy | d · L | CLO-036 | рецепт Caddyfile + launchd/systemd; ufw: только 443; проверено на Hermes | `deploy/sync-remote/README.md` |

**F. Поверхности и документация**

| ID | Задача | Тип · Пр. | Блокеры | Acceptance | Файлы |
|---|---|---|---|---|---|
| CLO-040 | Implement: рутина `sync` | f · L | CLO-013 | сеть вне транзакции рутины (§3.9); без сети — тихий пропуск | `cod_doc/services/routine_service.py` |
| CLO-041 | Docs: ARCHITECTURE §8/§10/§12 и DATA_MODEL | d · L | CLO-037 | профили деплоя «реплика + remote», коды SYNC, `actor` из спеки в факт | `docs/system/ARCHITECTURE.md`, `docs/system/DATA_MODEL.md` |
| CLO-042 | Docs: capability §0, скилл `doc-sync`, руководство | d · L | CLO-032 | `remote-sync.md` §0 «as implemented»; скилл знает о `sync status`; правило Q-013 описано | `docs/system/capabilities/remote-sync.md`, `plugins/cod-doc/skills/doc-sync/SKILL.md` |
| CLO-043 | Test: критерии §8 на двух машинах и итоговый аудит | t · M | CLO-027, CLO-033, CLO-037 | прогон §8 п. 1–5 на MacBook + десктоп через MinIO и Caddy; аудит; RFC → `implemented` | `docs/system/audit/…-cloud-remote-final.md` |

**Сценарии → задачи:** SCN-081 → CLO-018 · SCN-082 → CLO-029 · SCN-083 → CLO-012 ·
SCN-084 → CLO-037 · SCN-085 → CLO-013 · SCN-086 → CLO-030 · SCN-087 → CLO-027 ·
SCN-088 → CLO-009 · SCN-089 → CLO-026 · SCN-090, SCN-091 → CLO-023 ·
SCN-092 → CLO-022 · SCN-093 → CLO-004 · SCN-094 → CLO-031 · SCN-095 → CLO-032 ·
SCN-096 → CLO-033.
