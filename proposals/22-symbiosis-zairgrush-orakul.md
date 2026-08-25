# 22 — Symbiosis: cod-doc ↔ ZAIrgRush ↔ ai-review (Orakul)

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: proposal 04 (run-id), proposal 07 (routines / `on_finding`), proposal 09 (activity log); поглощает внешнюю часть proposal 16 (pair-hacker) и 17 (drift detector)

## 1. Контекст

cod-doc инженерно здоров (1356 тестов, ruff/mypy strict, 103 MCP-тула, 106
документов `in_sync`) и **не используется нигде, кроме самого себя** — вывод
[audit 2026-07-29](../docs/system/audit/2026-07-29-state-of-the-project.md):
«догфудинг нашёл за один вечер то, чего не нашли 1356 тестов, два LLM-ревью и
три аудита — потому что все они смотрели на код, а не пользовались им».

Этот RFC заменяет гипотетических «vibecoder'ов» из proposals 16/17 двумя
**реальными** потребителями с реальными болями:

- **ZAIrgRush** (`~/Git/_my/ZAIrgRush`) — мульти-агентная петля разработки
  (Executor Kimi ↔ Reviewer Claude ↔ Planner), stdlib-only Python, ~30.5k LOC,
  110 коммитов за две недели. У неё есть **названный и не начатый эксперимент
  E5 «Дрейф docs↔code»** (`06-knowledge-infra-experiments.md:239`) — ровно та
  ниша, которую cod-doc закрывает, — и нет ни реестра документов, ни хэшей, ни
  `decisions.jsonl`, который её собственный регламент §3 требует.
- **Orakul / ai-review** (`~/Git/_my/Mozarella/Orakul` + вынесенный движок
  `Mozarella/ai-reviewer@0.2.0`) — LLM-ревью PR без БД: находки живут в
  sticky-комментариях и CI-артефактах и умирают с ними. Их backlog P0 #18
  фиксирует невоспроизводимость находок (Jaccard 0.00–0.14 на 8 прогонах
  одного sha), P0 #19 требует выносить детерминированные классы дефектов из
  LLM в гейт. У Orakul **нет** проверки целостности ссылок/якорей и валидатора
  frontmatter — при 371 документе и жёсткой иерархии истины.

Симбиоз двунаправленный: cod-doc отдаёт спеки/ADR/контекст, петля и ревью
возвращают находки, коммиты и измерения. cod-doc остаётся независимым проектом:
оба потребителя подключаются добровольно и отключаются без последствий
(fail-open на их стороне, read-only на нашей).

Решения владельца (2026-08-24): менять можно все три репозитория; схему БД
трогать можно; первым идёт ZAIrgRush; `doc export` закрывается guard'ом сейчас,
полный round-trip-фикс позже; цель для ai-review — самостоятельный
`Mozarella/ai-reviewer`, не `ops/pr-review/`.

## 2. Текущее состояние (проверено по коду 2026-08-24)

### 2.1. Блокеры в cod-doc

| # | Факт | Где |
|---|---|---|
| B1 | `cod-doc project init` **не создаёт БД**: `project_service.init_project` (alembic + строка `project`) имеет одного вызывающего — web-роут; CLI зовёт только файловый `Project(entry).init()` | `api/web/pages/project.py:180`; `cli/cmd_project.py:64-80,96-109` |
| B2 | Нерабочую последовательность онбординга прописывают три документа | `skills/project-onboarding/SKILL.md:36-40`, `docs/adoption-playbook.md:100`, `docs/HANDBOOK.md:425-426` |
| B3 | `doc export` портит документы: view `document_body` клеит preamble к первому заголовку без разделителя, H1 теряется (ADO-010) | миграция `20260425_0006_views_and_defaults.py:56` |
| B4 | `DocumentType` не знает 5 из 6 типов ZAIrgRush; неизвестный тип молча → `MODULE_SPEC` | `domain/entities.py:13-26`; `import_service.py:140-147,345` |
| B5 | Аутентификации нет; `api_host` по умолчанию `0.0.0.0`; `POST /settings` пишет LLM-ключ | `config.py:152`; grep по `cod_doc/api` |
| B6 | Legacy REST пишет в `tasks.yaml`, не в БД | `api/routes.py:144-149` |
| B7 | SQLite без WAL (`journal_mode=delete`, `busy_timeout=0`) | `infra/db.py:37-50` |
| B8 | `import docs` одноразовый (`import_markdown`, не `import_or_update_markdown`); exclude-флага нет | `restate_importer.py:99-117,148-165` |
| B9 | `task.task_id` UNIQUE **глобально**, не `(project_id, task_id)` | schema `task`, `infra/models/plans.py:91` |
| B10 | FTS5-миграция без dialect-guard — Postgres не поднимется | `20260515_0023_fts5_index.py:33-44` |
| B11 | `~/.cod-doc/config.yaml` — один мёртвый проект-tmpdir; compose монтирует несуществующий `/Users/dakh/Git/cod-doc` | прямое чтение |
| B12 | Chroma L3 течёт между проектами: `_enrich_l3_semantic` не передаёт `project_root` | `services/context_service.py:456-464` |

### 2.2. Пять фактов, определивших дизайн

1. **bm25 относителен корпусу.** `search_service` ранжирует
   `bm25(db_search_idx) ORDER BY score` (`search_service.py:220,228`), а
   `db_search_idx` уже несёт `project_id UNINDEXED`. В одной БД кросс-проектный
   поиск = `WHERE project_id IN (...)`; слияние выдач трёх независимых
   FTS5-таблиц даёт фальшивый рейтинг. → общая hub-БД, не федерация.
2. **Orakul уже выкладывает export ревью артефактом CI** —
   `pr-review-export-<PR>`, `if: always()`, retention 30 дней
   (`.github/workflows/pr-review.yml:215-226`). → ingest — pull через
   `gh run download`, ноль изменений в Orakul, ноль сетевой экспозиции cod-doc.
3. **Export теряет поле дедупликации.** `slimFinding`
   (`ai-reviewer/lib/export.mjs:45-65`) не отдаёт `fp` (код-фингерпринт,
   `lib/findings.mjs:166`), `verifierStatus`, `actionabilityScore`. Чинится
   ~10 строками upstream.
4. **В коммитах ZAIrgRush нет ID задач вообще** (`gitops.py:270-273`:
   `git commit -qm <одна фраза от LLM>`; подтверждено `git log`). Расширять
   regex `commit_link` бессмысленно — нужен git-trailer.
5. **«Stdlib-only» = без pip-зависимостей, не без подпроцессов.** `mempg.pg()`
   (`mempg.py:26-40`) — единственная дверь к `psql` через subprocess с
   предохранителем. → транспорт для петли — вызов CLI cod-doc, не HTTP/MCP
   (MCP у агентов петли выключен намеренно: `engines.py:140-144`
   `--strict-mcp-config`; ADR-008 уже отклонил навигационный MCP).

### 2.3. Что переиспользуем (не изобретать)

- `routine.on_finding ∈ {create_task, update_existing_task, comment_only}` +
  `routine_run.findings_count` (`infra/models/routines.py:35,58,90`) — словарь
  промоушена находок уже есть.
- `activity_event` (UUIDv7, `actor_kind/run_id/scope/payload`) — аудит внешних
  акторов; `approval` + `agent_report(kind='approval_request')` — human-in-the-loop.
- `commit_link` (`_TASK_REF_RE = \b([A-Z]{2,5}-\d{3}[A-Z]?)\b`), `dependency`,
  `affected_file`, `repo_file/repo_symbol`, `trace_service`, `run_scope()`.
- Профиль MCP `agent` (6 тулов, self-sufficient card) — контракт внешнего агента.
- `ProjectEntry` уже `extra="allow"` (`config.py:33`) — поле `db_url` ложится
  без ломки формата; `resolve_db_url` уже принимает `override` (`infra/db.py:19`).

## 3. Предложение

### 3.1. Hub-БД (гибрид)

`~/.cod-doc/hub.db` — одна SQLite с тремя строками `project`
(`zairgrush`, `orakul`, при желании — другие пилоты). Собственная БД cod-doc
в hub **не переезжает** (единственная рабочая инсталляция — контрольная группа).
У каждого репозитория остаётся своя `.cod-doc/state.db` как умолчание.

```python
# cod_doc/config.py
class ProjectEntry(BaseSettings):
    ...
    db_url: str | None = None   # None → embedded <root>/.cod-doc/state.db

# cod_doc/infra/db.py — сведение ~14 дублей resolve→engine→factory
def db_for_entry(entry: ProjectEntry) -> tuple[sessionmaker[Session], Engine]: ...
```

`make_engine` для SQLite дополнительно: `PRAGMA journal_mode=WAL`,
`busy_timeout=5000`, `synchronous=NORMAL` (кроме `:memory:`). На открытии
сессии hub — сверка `alembic_version`; расхождение → `{ok: false,
code: "schema_mismatch"}`, не полуработа. Ограничения, фиксируемые в доке:
hub только на локальном диске (не iCloud/NFS); `alembic upgrade head` по hub —
при остановленных петлях.

### 3.2. Таблица находок (миграция `0027_findings`)

```sql
CREATE TABLE finding (
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    finding_uid VARCHAR(36) NOT NULL UNIQUE,          -- uuid7
    source VARCHAR(32) NOT NULL,                      -- 'ai_review' | 'zairgrush' | 'routine'
    source_ref VARCHAR(255),                          -- PR#, exp id, routine name
    fingerprint VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,                    -- нормализованная: critical|major|minor|info
    kind VARCHAR(32),                                 -- category / kind источника, как есть
    title TEXT NOT NULL, body TEXT, path TEXT, line INTEGER,
    status VARCHAR(16) NOT NULL DEFAULT 'open',       -- open|resolved|dismissed|promoted
    confidence FLOAT,
    first_seen_at DATETIME NOT NULL, last_seen_at DATETIME NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    promoted_task_id VARCHAR(32),
    payload JSON NOT NULL DEFAULT '{}',
    run_id VARCHAR(36),
    UNIQUE (project_id, source, fingerprint)
);
CREATE TABLE finding_source_run (                     -- одна строка на (находка, внешний прогон)
    row_id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES finding(row_id) ON DELETE CASCADE,
    source_run_id VARCHAR(128) NOT NULL,              -- sha+run для ai_review, run-id петли
    ts DATETIME NOT NULL, severity_at_run VARCHAR(16), raw JSON
);
CREATE TABLE external_ref (                           -- мост идентичностей
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    entity_kind VARCHAR(32) NOT NULL, entity_row_id INTEGER NOT NULL,
    system VARCHAR(32) NOT NULL, external_id VARCHAR(128) NOT NULL, url TEXT,
    UNIQUE (project_id, system, external_id)
);
```

Почему не `activity_event`: он append-only и без уникальности — дедупликация
требует индексированного ключа и UPDATE (`times_seen`, `last_seen_at`);
мутировать таблицу аудита = сделать аудит недостоверным. Почему не `task`:
большинство находок задачами не станут (Jaccard 0.00–0.14), а глобально
уникальный `task_id` сжигал бы идентификатор на каждую пересозданную находку.

Фингерпринты — чистые функции в `services/finding_service/fingerprint.py`,
**никогда в адаптерах**:

- `ai_review`: `sha256(source|path|fp|severity)`, где `fp` — код-фингерпринт
  движка (переживает сдвиг строк по построению). При пустом `fp` — деградация
  на `normalized_title` c пометкой `payload.fp_basis="title"`.
- `zairgrush`: `sha256(source|exp|variant|kind)` (`note` — проза, в ключ не идёт).
- `routine`: `sha256(source|check_name|scope_kind|scope_id)`.

`finding_source_run` делает Jaccard-стабильность вычислимой внутри cod-doc:
`cod-doc finding stability --project orakul --sha <sha>` отвечает на их P0 #18
сохранёнными данными вместо повторных прогонов.

### 3.3. Контракты: ingest, ctx, api/v1

```
cod-doc ingest <adapter> --project SLUG --input FILE|- [--dry-run] [--json]
cod-doc ingest ai_review --project orakul --from-pr N      # gh run download → ingest
cod-doc ctx docs   --project SLUG --paths p1,p2 --budget-tokens N --json
cod-doc ctx drift  --project SLUG --changed-files f1,f2 --json
cod-doc ctx search --project SLUG "query" --json
cod-doc hub init                                            # создать/мигрировать hub.db
cod-doc finding stability --project SLUG --sha SHA
```

- Реестр адаптеров: `services/ingest_service/registry.py` (`INGEST_ADAPTERS:
  dict[str, Adapter]`, протокол `Adapter.parse(stream) -> list[RawFinding]`).
  Адаптеры: `ai_review.py` (диспетчер по `payload.version` — работает и с
  `ops/pr-review/`, и с вынесенным движком), `zairgrush_findings.py`,
  `zairgrush_tasks.py`. Это закрывает и B8: реестр импортёров наконец существует.
- Новый пакет `cod_doc/api/v1/` (router + pydantic-схемы) поверх тех же
  сервисов: `POST /api/v1/projects/{slug}/findings`, `GET .../context`,
  `GET /api/v1/search`. Legacy `/api/*` **заморожен** — не расширять.
- MCP: семьи `finding_*` (`finding_list/get/promote/dismiss`) и `ctx_*`
  (`ctx_docs/ctx_drift`) в профили `standard`/`full` — интерактивной сессии
  они нужны, но интеграция от них не зависит.
- Безопасность сейчас = **закрыть открытое**: bind по умолчанию `127.0.0.1`
  (opt-out `COD_DOC_BIND`), loopback-гейт на `POST /settings`. Bearer-токен —
  описан здесь как контракт (`COD_DOC_API_TOKEN`, ASGI-middleware только на
  `/api/v1`, constant-time compare, 401 JSON), включается при появлении первого
  удалённого вызывающего. `/api/v1` — единственная поверхность, которая его
  когда-либо получит.

### 3.4. Сторона ZAIrgRush (E5 вариант C)

| Файл | Изменение |
|---|---|
| `tools/swarm/swarm/docctx.py` | новый плоский модуль: одна дверь `codctx(config, args, timeout) -> tuple[bool, str]`, subprocess `["cod-doc", "ctx", ...]`, собственный предохранитель — калька с `mempg.pg()` |
| `tools/swarm/swarm/promptbuilder.py:136` | `docs_block(agents, task)` рядом с `memory_block`, тот же побайтово стабильный кэш `agents.docs_cache = (tid, block)` |
| `tools/swarm/swarm/promptbuilder.py:162` | `handoff(...)` получает `docs`, рендер после `unc`, перед `mp` |
| `tools/swarm/swarm/cli.py:81-84` | `KNOWN_EXPERIMENT_KEYS += {"doc_context"}`; `DOC_CONTEXT_MODES = {off, executor, reviewer, all}`, валидация как у `MEMORY_MODES` |
| `tools/swarm/swarm/gitops.py:270-273` | git-trailers после пустой строки: `Swarm-Task: r1cf`, `Cod-Doc-Task: ZRG-014`; тема не трогается |

Умолчание — `off`; при выключенном флаге `handoff` даёт **побайтово** прежний
промпт (отдельный тест) — иначе их E8/E10/E13 перестанут быть сравнимыми.
Оформление по их процессному закону: вариант C эксперимента E5 (машинная карта,
которую поддерживает инструмент, против рукописного `docmap.toml`), метрики E5
без изменений + две новых (задержка на раунд, доля открытий предохранителя),
строки в `experiments/findings.jsonl` (`exp:"E5", variant:"C-coddoc"`),
закрывающий `experiments/adr/NNN-doc-context-source.md`.

Идентичность: задачи ZAIrgRush зеркалятся как `ZRG-###` (существующий
`_TASK_REF_RE` ловит без правки), нативный `r1cf` живёт в
`external_ref(system='zairgrush')`. Зеркало — **только на чтение**: писать в
`.swarm/tasks.json` значит конкурировать с валидируемым plan-diff планировщика.

### 3.5. Сторона Orakul / ai-reviewer

- **Ingest — pull-моделью** (факт 2.2.2): `--from-pr` качает артефакт
  `pr-review-export-<PR>`, локальный поллер `scripts/ingest-orakul-reviews.sh`
  живёт в cod-doc. Изменений в Orakul — ноль.
- **Один upstream-PR в `ai-reviewer`** (~10 строк): `fp`, `verifierStatus`,
  `actionabilityScore` в `slimFinding`, bump `EXPORT_VERSION`. Обоснование в их
  терминах: export строго слабее sticky-футера, потребитель вынужден
  пересчитывать стабильность, которую движок уже посчитал (их P0 #18).
- **Sink на уровне профиля отвергнут**: `lib/profile.mjs` нормализует только
  строки/regex/пути; поле команды-хука позволило бы профилю исполнять
  произвольный код в движке, который `git archive`-ится из base-ветки —
  регрессия цепочки поставки.
- **Обратное направление (drift-гейт)**: `cod-doc ctx drift` отдаёт находки в
  форме движка (`prescan: true`, `model: "cod-doc/drift"`); v1 доставляется
  собственным PR-комментарием cod-doc под своим marker-namespace. Уникальная
  ценность: у Orakul нет гейта целостности ссылок/якорей и валидатора
  frontmatter — а `link_service.verify_section` + `doc_drift` это ровно тот
  детерминированный класс, который их P0 #19 велит вынести из LLM.
  Критерий: на PR с переименованным якорем, на который ссылаются 3 места,
  cod-doc называет все три на 8 прогонах одного sha (Jaccard 1.00 против
  0.00–0.14 у LLM).

### 3.6. Кросс-проектность (последняя фаза)

- `search_service.search(..., project_ids: list[int])` → `IN (...)`;
  `cod-doc search --projects a,b` и `GET /api/v1/search`.
- `link_service`: `[[doc:<slug>:<key>]]` с резолвом в пределах проектов,
  делящих `db_url` — отложенная заметка `link_service/__init__.py:22-28`
  становится реализацией.
- B12: `_enrich_l3_semantic` передаёт фильтр проекта — утечка Chroma
  превращается из бага в намеренный кросс-проектный режим.
- `agent_pick`/`agent_capabilities` получают необязательный `projects`;
  блокировки `task_checkout` перепроверяются под hub.

## 4. Миграция / обратная совместимость

- **`0026_shared_hub`**: batch-rebuild `task` — `UNIQUE(task_id)` →
  `UNIQUE(project_id, task_id)`. Для существующих однопроектных БД
  поведенчески ничто не меняется. **`0027_findings`**: три таблицы §3.2 +
  `"finding"` в `_VALID_SCOPES` и `reindex_all` (FTS).
- `DocumentType` — StrEnum поверх VARCHAR, расширение (`design, audit, journal,
  plan, analysis, research, capability, audit-report`) миграции БД не требует;
  `import_service` меняет тихую подмену на предупреждение в payload.
- Dialect-guard в `20260515_0023_fts5_index.py`: не-SQLite → явный
  `NotImplementedError`, не загадочное падение.
- `doc export`: `--dry-run` (unified diff) + отказ писать в проект с
  `root_path` ≠ корень cod-doc без `--force-write`. Полный byte-identical
  round-trip (ADO-010) остаётся в бэклоге и становится обязательным в день,
  когда понадобится писать наружу.
- Legacy `/api/*` не трогаем и не расширяем; существующие MCP-профили не
  меняются (новые тулы только в `standard`/`full`).
- В рабочие деревья ZAIrgRush/Orakul cod-doc **не пишет** (кроме `.cod-doc/`,
  `MASTER.md`, 3 строк `.gitignore` при init и осознанного PR с трейлерами
  в `gitops.py`).

## 5. Риски и что не делаем

**Non-goals:**

1. **Postgres** — недели работы ради задачи, которую решает WAL. Только guard.
2. **Запись в `docs/` Orakul — никогда.** `check-doc-version-bump.sh` без
   обхода намеренно; генератор обязан сам считать `Версия:`
   (`gen-api-routes-doc.mjs:10-19`), иначе main краснеет нечинимо.
3. **cod-doc, достижимый из GitHub Actions** — pull-модель делает экспозицию
   ненужной.
4. **MCP-исключение в ZAIrgRush** — не тратить их сильнейший архитектурный
   инвариант (`--strict-mcp-config`) на удобство.
5. **Плагинизация `CHECK_CATALOG` сейчас** — второго потребителя нет, API
   проектировался бы по выборке из одного.
6. **Двусторонняя синхронизация задач с ZAIrgRush** — только зеркало на чтение.
7. **Одна агентская петля на три репозитория** — общий *контекст* да,
   исполнение остаётся по репозиториям (три гейта, три языка, три культуры ревью).

**Риски:**

- Темп ZAIrgRush (110 коммитов/2 недели, 5 worktree): патч петли — один
  небольшой PR за один присест, долгоживущая ветка не выживет.
- Слот экспериментов занят (E9-EXEC inconclusive, E14 активен): замер E5-C
  встаёт в очередь; код пишется параллельно, «один фактор за прогон»
  соблюдается.
- Две копии движка ai-review: пиннимся к `payload.version`, не к пути.
- Одноразовость `import docs` (B8): обновления через `doc import <file>` /
  web-scan закладываются в ежедневный цикл с первого дня.
- «Пилоты заведены и заброшены» — M2 ROADMAP не закрывается без friction-лога
  из живой работы.

## 6. Оценка

Фазы (детальный план: `~/.claude/plans/our-goal-is-prepate-stateless-frost.md`):

| Фаза | Содержание | Объём |
|---|---|---|
| 0 | Самопочинка cod-doc: B1/B2/B4/B5/B7/B8/B11 + guard экспорта + dialect-guard | 4–6 дней, ~10 задач |
| 1 | Hub, `finding`-таблицы, реестр адаптеров, CLI/api-v1/MCP, ADR-мост ZAIrgRush | 5–8 дней, ~10 задач |
| 2 | `ctx docs` + патч петли ZAIrgRush (E5-C) + трейлеры | 6–10 дней кода, ~6 задач |
| 3 | Ingest ai-review pull-моделью + upstream-PR + `finding stability` | 4–6 дней, ~5 задач |
| 4 | `ctx drift` → PR-комментарий (гейт ссылок/frontmatter для Orakul) | 6–10 дней, ~5 задач |
| 5 | Кросс-проектный поиск, `[[doc:slug:key]]`, фикс B12, `agent_pick --projects` | 8–15 дней, ~6 задач |
| 6 | Переназначение плана adoption, переписывание onboarding-скилла | 2–3 дня, ~3 задачи |

Итого: **~45 задач, 6–10 недель** реализации + календарное время замеров
(E5-очередь), которое сжать нельзя. Фазы 0–1 не дают видимого результата —
это цена безопасного входа в чужой горячий репозиторий.

Декомпозиция: секция C плана `adoption-2026-08` переназначается на новые
пилоты (Фаза 0 ≈ ADO-001/002/015 + новое), для Фаз 1–5 — новая секция **E
«Symbiosis»** со своим диапазоном ID (через `plan_section_create`, не в чужие
секции). Закрытие каждой фазы — audit-отчёт по скиллу `audit-cadence`.

## 7. Источники

- ZAIrgRush: `05-agent-swarm.md` (v0.55), `06-knowledge-infra-experiments.md`
  (E5 §239, статус-таблица §8), `tools/swarm/swarm/{engines,helpers,mempg,promptbuilder,gitops,cli,verdicts}.py`, `experiments/adr/008-hybrid-code-intelligence.md`.
- Orakul: `.github/workflows/pr-review.yml`, `docs/08-technical/37-ai-review-backlog.md`
  (P0 #18/#19), `ops/gen-api-routes-doc.mjs`, `ops/check-doc-version-bump.sh`.
- ai-reviewer: `lib/export.mjs` (`slimFinding`), `lib/findings.mjs`
  (`findingFingerprint`), `lib/profile.mjs`, `docs/integration.md`, `docs/profiles.md`.
- cod-doc: [audit 2026-07-29](../docs/system/audit/2026-07-29-state-of-the-project.md),
  [ROADMAP](../docs/system/roadmap/ROADMAP.md), proposals 04/07/09/16/17.
