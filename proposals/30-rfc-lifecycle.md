---
type: rfc
---

# 30 — Жизненный цикл RFC

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: нет обязательных;
> опирается на OQM (открытые вопросы), ADO-238 (тип `rfc`), RFC 34 (связи ADR).
> Разблокирует RFC 31 (трек L), RFC 32 (дельты к capability), RFC 33 (очередь
> `rfc_review`), RFC 35.

## 1. Контекст

Исследование `docs/system/research/2026-09-30-rfc-lifecycle.md` ответило на
вопрос «RFC — черновик ADR?»: нет. Это разные сущности:

| Вопрос | Сущность | Жизнь во времени |
|---|---|---|
| Что предлагаем и зачем? | RFC | после решения замораживается |
| Что решили и почему? | ADR | действует, пока не заменён |
| Что делаем и сколько сделано? | план + задачи | живёт до закрытия |

RFC при принятии порождает 0…N ADR и план. Во всех изученных процессах (PEP,
KEP, Rust RFC, Nygard ADR) у предложения есть собственный статус,
«реализовано» выводится из трекера реализации, а отклонение — статус с
причиной, а не удаление. У cod-doc ничего из этого нет: статус RFC живёт
прозой в `proposals/README`.

Исследование «Конвейер артефактов»
(`docs/system/research/2026-09-30-artifact-pipeline.md`, §4.2, §4.6, §5)
добавило к RFC 30 поля `goal`, `appetite`, `no_gos` и гейт принятия
«нет открытых блокирующих вопросов».

## 2. Текущее состояние (проверено по коду и живой БД 2026-10-04)

Пересверено с main 2026-10-07, после ARG-008…010 (полки ADR) и DEBT-001.

| Факт | Где |
|---|---|
| Тип `rfc` есть (ADO-238), миграция 0044 вернула его 32 документам | `cod_doc/domain/entities.py:67` |
| RFC раскладывается в раздел `proposals` по типу | `cod_doc/services/doc_taxonomy.py:265` |
| Статус RFC — общий `DocumentStatus`; `accepted` сводится в `active`, `rejected`/`superseded` — в `deprecated`; стандарт отсылает к отдельному полю `rfc_status` | `docs/system/standards/frontmatter.md:62`, `:83` |
| На живой БД: 33 RFC (с этим), 30 `draft`, 3 `active`; отбракованные 16–21 — `draft` | `document` |
| Отбраковка и причины — только проза | `proposals/README`, блок «Отбраковка 2026-08-29» |
| Номера резервируются прозой («30 зарезервирован…», «резерв C4 сдвинут с 34 на 35») | `proposals/README` |
| `plan.parent_doc_id` есть в схеме и читается контекстом, но его некому записать: `create_plan` не принимает родителя; пуст у 19 из 19 планов | `cod_doc/services/plan_service/sections.py:94`, `cod_doc/services/context_service.py:536` |
| У ADR нет ссылки на документ-источник; ADR-009/016/017 в `proposed` без родителя. Связи ADR ↔ ADR (RFC 34, миграция 0045) и полки `adr.topic_id` (ARG-008, миграция 0046) есть | `cod_doc/infra/models/adrs.py:23` |
| `adr_health` ловит долгий черновик ADR, но не «ADR без RFC» | `cod_doc/services/adr_health.py:16` |
| Вопрос умеет блокировать документ: `question_link(to_kind=document, relation=blocks)` | `cod_doc/domain/entities.py:352` |

## 3. Предложение

### 3.1. Цикл

```
draft → review → accepted → implemented
          │  ↘ rejected
          ↘ deferred → review
draft/review → withdrawn
accepted/implemented → superseded (by RFC N)
```

| `rfc_status` | Смысл | Переход | `document.status` |
|---|---|---|---|
| `draft` | автор пишет | — | `draft` |
| `review` | обсуждение | `rfc_review` | `review` |
| `accepted` | реализация разрешена | `rfc_accept` — гейт §3.3 | `active` |
| `implemented` | план RFC закрыт | вычисляется (§3.4) | `active` |
| `rejected` | «нет», причина обязательна | `rfc_reject` | `deprecated` |
| `deferred` | «не сейчас», причина обязательна | `rfc_defer` | `draft` |
| `withdrawn` | автор снял | `rfc_withdraw` | `deprecated` |
| `superseded` | заменён RFC N | `rfc_supersede` | `deprecated` |

`rfc_status` — источник истины; `document.status` пишет только `rfc_service`
по таблице выше, чтобы существующие фильтры документов не врали. Допустимые
переходы — `RFC_TRANSITIONS` в одном модуле (`services/rfc_status_machine.py`)
по образцу `task_status_machine.py`. `deferred` — Q-006.

### 3.2. Схема

Таблица `rfc_meta`, 1:1 к документу (Q-001):

```sql
CREATE TABLE rfc_meta (
  document_id     INTEGER PRIMARY KEY REFERENCES document(row_id) ON DELETE CASCADE,
  project_id      INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
  rfc_number      INTEGER NOT NULL,
  rfc_status      VARCHAR(16) NOT NULL DEFAULT 'draft',
  goal            TEXT,
  appetite        TEXT,      -- «сколько готовы потратить», свободный текст
  no_gos          TEXT,      -- markdown-список
  decision_reason TEXT,      -- обязателен для rejected/deferred/withdrawn
  decided_at      DATE,      -- ставит accept/reject/defer/withdraw
  superseded_by   INTEGER REFERENCES document(row_id) ON DELETE SET NULL,
  UNIQUE (project_id, rfc_number),
  CHECK (rfc_status IN ('draft','review','accepted','implemented',
                        'rejected','deferred','withdrawn','superseded'))
);
```

- `rfc_number` — из имени файла `proposals/NN-slug`; коллизию ловит UNIQUE
  (Q-005).
- ADR → RFC: `adr.source_doc_id INTEGER NULL REFERENCES document(row_id) ON
  DELETE SET NULL` (Q-003). Миграция трогает `adr`, не `document`; колонка
  добавляется как `adr.topic_id` в 0046 — `ADD COLUMN`, без пересоздания
  таблицы, которое унесло бы по CASCADE диаграммы и связи ADR.
- RFC → план: существующий `plan.parent_doc_id`; новый необязательный
  параметр `parent_doc_key` у `create_plan` / `plan_create` / `cod-doc plan
  create` и отдельная операция `plan_set_parent`.
- RFC ↔ вопросы: существующий `open_question_link`, `relation=blocks`.

Frontmatter-проекция RFC получает поля `rfc_status`, `goal`, `appetite`,
`no_gos` (+ `decision_reason`, `superseded_by` при наличии). Импорт сменённого
в файле `rfc_status` проводит через `RFC_TRANSITIONS`: недопустимый переход —
ошибка импорта, а не молчаливая запись.

### 3.3. Гейт принятия

`rfc_accept(project, doc_key, plan_scope=None)` отказывает, если:

1. есть открытый вопрос со связью `blocks` на этот RFC — ошибка перечисляет их;
2. пусты `goal`, `appetite` или `no_gos`;
3. статус не `review`.

При успехе: `rfc_status=accepted`, `decided_at=today`; если передан
`plan_scope` — план получает `parent_doc_id`. ADR, у которых
`source_doc_id` = этот RFC и статус `proposed`, **не** принимаются
автоматически: ответ возвращает их списком «решить следом». Тело после
принятия не блокируется — правка секции принятого RFC даёт пункт куратора
(§3.6). Проверка «у каждой дельты есть целевая секция capability» добавится
в RFC 32.

### 3.4. `implemented`

Хранится (Q-002). `rfc_service.refresh_implemented(session, plan_id)`
вызывается из пути закрытия задачи, когда закрыта последняя открытая задача
плана с `parent_doc_id`: `accepted → implemented`. Переоткрытие задачи
возвращает `implemented → accepted`. Рутина `rfc_health` сверяет оба
направления на случай записи в обход сервиса.

### 3.5. Поверхности

| Сервис `rfc_service` | MCP | CLI |
|---|---|---|
| `list(status=…)` | `rfc_list` | `cod-doc rfc list [--status]` |
| `get` — мета, ADR, план с прогрессом, вопросы | `rfc_get` | `cod-doc rfc show NN` |
| `update_meta` (goal/appetite/no_gos) | `rfc_update` | `cod-doc rfc set NN --goal …` |
| `review` / `accept` / `reject` / `defer` / `withdraw` / `supersede` | `rfc_transition(action=…)` | `cod-doc rfc review|accept|reject|defer|withdraw|supersede NN` |
| `create_plan(parent_doc_key=…)`, `set_parent` | `plan_create(parent_doc_key)`, `plan_set_parent` | `cod-doc plan create --rfc NN`, `cod-doc plan set-parent` |
| `adr_service.create/update(source_doc_key)` | `adr_create/adr_update(source_doc_key)` | `cod-doc adr new --rfc NN` |

Профили: `rfc_*` и `plan_set_parent` — `standard`/`full`; имена новых тулов
вписываются в строку семейства `docs/mcp-integration.md`, числа профилей
пересчитывает `scripts/regen.sh` (DEBT-001). Паритет мутаций — новый
`tests/services/test_rfc_mutation_surface_parity.py` по образцу ADR. Каждый
переход пишет ревизию и событие `rfc.<action>` (ADO-040).

Веб: `/p/<slug>/rfc` — список с фильтром по статусу, номером, прогрессом
плана и числом открытых блокирующих вопросов; карточка RFC — мета, ADR-дети,
план, вопросы, кнопки переходов.

### 3.6. Каталог и куратор

- `proposals/README`: таблица каталога между маркерами
  `<!-- rfc-catalog:begin/end -->` генерируется из `rfc_meta` (номер,
  документ, статус, план и прогресс, ADR). Нарратив треков и граф mermaid
  остаются ручными.
- Рутина `rfc_health` → findings `source_ref="rfc_health"` с автозакрытием
  партиции:

| Находка | Условие |
|---|---|
| `rfc_accepted_without_plan` | `accepted` и нет плана с `parent_doc_id` |
| `rfc_implemented_drift` | статус расходится с закрытостью плана |
| `rfc_stale_review` | `draft`/`review` без ревизий 30 дней |
| `rfc_edited_after_accept` | ревизия секции позже `decided_at` у `accepted`/`implemented` |
| `adr_orphan_proposal` | ADR `proposed` без `source_doc_id` дольше `STALE_PROPOSAL_DAYS` — дополняет `adr_health`, не дублирует «долгий черновик» |

### 3.7. Скиллы

`rfc-authoring`: раздел «Жизненный цикл» — по §3.1; новые поля frontmatter;
резерв номера — заглушкой RFC в `draft`, а не строкой README. `adr-author`:
шаг «указать `source_doc_key`, если решение принимается в RFC».

## 4. Миграция и обратная совместимость

1. Миграция создаёт `rfc_meta` и `adr.source_doc_id`; `document` не трогает
   (никакого `batch_alter_table`).
2. Бэкфилл в той же миграции: строка `rfc_meta` на каждый `type=rfc`,
   `rfc_number` из `doc_key`; 16–21 → `rejected` с причиной из README, прочие
   → `draft`. Миграционный тест наливает документы на предыдущей ревизии
   (образец `test_migration_0035_preserves_data.py`).
3. Остальные статусы — задача бэкфилла через `rfc_transition` с ревизиями:
   агент собирает таблицу «RFC → статус → основание», владелец утверждает
   (Q-004). Там же — `plan_set_parent` для известных пар (RFC 25, 27, 28, 34).
4. `downgrade()` удаляет таблицу и колонку (нативный `DROP COLUMN`, как в
   0046); `document.status` остаётся последним записанным.
5. Документ без строки `rfc_meta` (RFC, заведённый до релиза) `rfc_service`
   читает как `draft` и создаёт строку при первом переходе.

## 5. Риски и что не делаем

| Риск | Мера |
|---|---|
| `rfc_status` и `document.status` разъезжаются | пишет только `rfc_service`; тест на соответствие таблице §3.1 |
| Импорт старого файла откатывает статус | переход из frontmatter через `RFC_TRANSITIONS`; откат принятого — ошибка |
| Гейт принятия мешает мелким RFC | гейт — три поля и вопросы; трек S/M без RFC — RFC 31 |

| Не делаем | Почему / где |
|---|---|
| Дельты RFC к capability и их вливание | RFC 32 |
| Бюджет у RFC, очередь «ждёт человека» | RFC 33 (использует `rfc_status=review`) |
| Сущность идеи и `idea_promote` в RFC | RFC 31 |
| Автопринятие ADR при принятии RFC | решение по ADR — отдельный акт человека |
| Блокировка правки тела принятого RFC | хватает пункта куратора; блокировка мешает правке опечаток |
| Отдельная таблица с телом RFC | секции, ссылки, drift и FTS уже работают для документов |

## 6. Оценка

План `rfc-lifecycle-2026-10`, префикс задач `RFL`, 14 задач, 2–3 недели.

| Секция | Задачи |
|---|---|
| **A. Данные** | RFL-001 миграция `rfc_meta` + `adr.source_doc_id` + бесспорный бэкфилл; RFL-002 домен и машина переходов |
| **B. Сервис и поверхности** | RFL-003 `rfc_service` и гейт; RFL-004 MCP; RFL-005 CLI; RFL-006 веб; RFL-007 frontmatter |
| **C. Связи** | RFL-008 родитель плана; RFL-009 ADR → RFC; RFL-010 `implemented` |
| **D. Каталог, куратор, скиллы** | RFL-011 каталог README; RFL-012 `rfc_health`; RFL-013 скиллы; RFL-014 ручной бэкфилл |

RFL-008 стартует сразу — от `rfc_meta` не зависит. Остальное идёт от
RFL-001 → RFL-002 → RFL-003; RFL-001 ждёт ответов на Q-001, Q-003, Q-005.

## 7. Открытые вопросы

Заведены сущностями, каждый блокирует принятие этого RFC (`relation=blocks`);
в тексте выше стоит рекомендуемый вариант.

| Вопрос | Тема | Рекомендация |
|---|---|---|
| Q-001 | колонки на `document` или `rfc_meta` | `rfc_meta` |
| Q-002 | `implemented` хранить или вычислять | хранить + сверка |
| Q-003 | ADR → RFC: колонка или ребро | `adr.source_doc_id` |
| Q-004 | бэкфилл 32 RFC | бесспорные — миграцией, остальные — владелец |
| Q-005 | номер из файла или счётчик | из файла + UNIQUE |
| Q-006 | статус `deferred` | нужен |
