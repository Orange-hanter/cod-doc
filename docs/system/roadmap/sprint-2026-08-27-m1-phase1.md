---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-27
last_updated: 2026-08-27
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
  - ../../adoption-playbook.md
  - ../../../cod_doc/skills/project-onboarding/SKILL.md
  - ../../../cod_doc/skills/audit-cadence/SKILL.md
---

# Sprint 2026-08-27 → 2026-09-10 — «M1 закрыт, Фаза 1 начата»

> **Назначение.** Двухнедельный спринт: закрыть милстоун M1 «Пилот работает»
> (ROADMAP) и заложить фундамент Фазы 1 Symbiosis (hub + findings).
>
> **Не source of truth.** Статусы задач — в БД (план `adoption-2026-08`);
> приоритеты — [ROADMAP.md](ROADMAP.md). Этот документ фиксирует
> договорённости спринта: цели, контракты, схемы.

## 0. Ground truth на старт спринта (сверено 2026-08-27)

- План `adoption-2026-08`: 31 задача, 12 done, 19 remaining.
- Фаза 0 почти добита: SYM-001/002/004, ADO-001/002/010/015/018–022 — done.
  Открыт из Фазы 0 только **SYM-003**.
- **Миграция `20260826_0026_document_type_recoercion` существует на ветке
  `worktree-swarm-ado022-ado015-sym004`, в `main` не влита.** Номера миграций
  Фазы 1 сдвинуты: `0027_shared_hub`, `0028_findings` (в RFC 22 — 0026/0027).
  Предусловие SYM-005B: влить worktree-ветку в main.
- Пилоты: ZAIrgRush — `/Users/dakh/Git/_my/ZAIrgRush`; Orakul —
  `/Users/dakh/Git/_my/Mozarella/Orakul`; ai-reviewer —
  `/Users/dakh/Git/_my/ai-reviewer` (не под Mozarella — расхождение с RFC 22 §1,
  при ingest-адаптере пинимся к `payload.version`, не к пути).

## 1. Цели

- **G1 — M1 «Пилот работает» закрыт.** SYM-003, ADO-016, ADO-017 → done:
  оба пилота заведены по 5 критериям `project-onboarding`, поверхность cod-doc
  безопасна для чужого репозитория.
- **G2 — Фундамент Фазы 1.** SYM-005 (все субзадачи) → done; SYM-006 —
  минимум SYM-006A/B (ingest-реестр + CLI).
- **G3 — Петля обратной связи запущена.** ADO-005 открыт, ≥5 наблюдений из
  живой работы; ADO-007 — routine `doc_drift` ходит по пилоту ZAIrgRush.

## 2. Контракты задач

### Волна 0 (ready сейчас)

| ID | Контракт (acceptance) |
|---|---|
| **SYM-003** | `cod-doc serve` слушает 127.0.0.1; `POST /settings` с не-loopback → 403; `COD_DOC_BIND=0.0.0.0` осознанно возвращает старое поведение. Файлы: `cod_doc/config.py:152`, `Dockerfile:41`, гейт в `cod_doc/api/web/pages/_helpers.py` |
| **ADO-016** | 5/5 критериев онбординга; `doc drift --all`: `edited_in_place==0`, `missing==0`; search по доменному термину находит; `git status` ZAIrgRush чист (кроме `.cod-doc/`); `module-spec` не доминирует. Runbook — task-doc `plan` |
| **ADO-017** | Те же 5 критериев для Orakul. Путь: `/Users/dakh/Git/_my/Mozarella/Orakul`. blocked_by ADO-016 |
| **ADO-011** (фон) | `cod-doc audit --web-routes` → 0 WR-1 и 0 WR-2 |

### SYM-005 → субзадачи (Фаза 1, hub + findings)

| ID | Содержание | Контракт | blocked_by |
|---|---|---|---|
| **SYM-005A** | Hub-инфра: `ProjectEntry.db_url`, `infra/db.db_for_entry()` (свести ~14 дублей resolve→engine→factory), CLI `cod-doc hub init`, сверка `alembic_version` → `schema_mismatch` | `hub init` идемпотентен; hub.db в WAL; unit-тесты `db_for_entry`; suite зелёный (embedded state.db без регрессий) | — |
| **SYM-005B** | Миграция `0027_shared_hub`: batch-rebuild `task` → `UNIQUE(project_id, task_id)` (факт B9). **Предусловие: merge `worktree-swarm-ado022-ado015-sym004`** | upgrade/downgrade симметричны; фикстура с данными переживает rebuild; однопроектные БД поведенчески не меняются | SYM-005A |
| **SYM-005C** | Миграция `0028_findings`: `finding` / `finding_source_run` / `external_ref` (SQL §4.2) + ORM `infra/models/findings.py` + `"finding"` в `_VALID_SCOPES` и `reindex_all` | upgrade/downgrade симметричны; UNIQUE(project_id, source, fingerprint) отклоняет дубль (тест IntegrityError); mypy strict | SYM-005B |
| **SYM-005D** | `services/finding_service/`: `fingerprint.py` (чистые функции, §4.3), `dedup.py` (upsert, `times_seen++`, `finding_source_run`), `promote.py` (словарь `on_finding` из routine) | повторный ingest → `created=0, updated=N, times_seen=2`; 8 конкурентных ingest без `database is locked`; property-тест стабильности fingerprint; activity event на promote | SYM-005C |

### SYM-006 → субзадачи (Фаза 1, ingest/ctx поверхности)

| ID | Содержание | Контракт | blocked_by |
|---|---|---|---|
| **SYM-006A** | `services/ingest_service/registry.py`: `INGEST_ADAPTERS: dict[str, Adapter]`, `Adapter.parse(stream) -> list[RawFinding]`; адаптеры `ai_review.py` (диспетчер по `payload.version`), `zairgrush_findings.py`, `zairgrush_tasks.py`. Закрывает B8 | каждый адаптер парсит golden-fixture из реального экспорта; неизвестный `payload.version` → явная ошибка; registry-тесты | SYM-005D |
| **SYM-006B** ← спринт-минимум G2 | CLI: `cod-doc ingest <adapter> --project SLUG --input FILE\|- [--dry-run] [--json]`; `ingest ai_review --from-pr N` (pull `gh run download`, артефакт `pr-review-export-<PR>`); `cod-doc finding stability --project SLUG --sha SHA` | `--dry-run` ничего не пишет; повторный `--from-pr` дедупится (`created=0`, `times_seen` растёт); `finding stability` на 8 прогонах одного sha → Jaccard-таблица (ответ на P0 #18 Orakul); cli-тесты | SYM-006A |
| **SYM-006C** (стретч) | `cod_doc/api/v1/`: router + pydantic; `POST /api/v1/projects/{slug}/findings`, `GET .../context`, `GET /api/v1/search`. Legacy `/api/*` заморожен | api-тесты v1; legacy-роуты не тронуты; `/api/v1` — единственная поверхность будущего Bearer-гейта (RFC 22 §3.3) | SYM-006B |
| **SYM-006D** (стретч) | MCP `finding_list/get/promote/dismiss` + `ctx_docs/ctx_drift`; только профили `standard`/`full` | `test_mcp_lists_tools` обновлён; профили `minimal`/`agent` побайтово прежние; activity events на write-тулах (proposal 09) | SYM-006B |

### Трек C — петля обратной связи

| ID | Контракт на спринт |
|---|---|
| **ADO-005** | Friction-лог (task-doc `journal`) открыт с первого дня пилота; ≥5 наблюдений из живой работы. Полный критерий ≥10 — M2, следующий спринт |
| **ADO-007** | Routine `doc_drift` зарегистрирован в проекте ZAIrgRush, отрабатывает по cron, findings не дублируются |

### Вне скоупа

SYM-007…011 (зависят от пилотов/SYM-006), ADO-006/012 (M2), STB-023 (держать
закрытым), Трек B (hackathon RFC). ADO-013/014 — опортунистично.

## 3. Runbook онбординга пилотов

ADO-016 (ZAIrgRush), по skill `project-onboarding`:

```bash
cod-doc project add zairgrush --root /Users/dakh/Git/_my/ZAIrgRush
cod-doc project init zairgrush
cod-doc import docs --project zairgrush --dry-run \
  --exclude 'experiments/stand*' --exclude 'experiments/repomap' \
  --exclude '.claude/worktrees'
# решение по архивным каталогам — до снятия --dry-run
cod-doc import docs --project zairgrush <те же exclude>
cod-doc doc drift --project zairgrush --all   # edited_in_place==0, missing==0
cod-doc search --project zairgrush "<доменный термин>"
```

Инварианты: в рабочее дерево пишется только `.cod-doc/`, `MASTER.md`, 3 строки
`.gitignore`; `git status` пилота чист; `module-spec` не доминирует
(`select type, count(*) from document`) — ADO-015 снял тихую подмену.
ADO-017 (Orakul, 371 док, Diátaxis) — тот же runbook; exclude-лист после
осмотра репозитория.

## 4. Схемы БД

### 4.1. `0027_shared_hub` — batch-rebuild `task`

```sql
-- SQLite batch rebuild (alembic batch_alter_table):
CREATE TABLE task_new (
    -- все колонки как в task, ограничение заменено:
    CONSTRAINT uq_task_project_taskid UNIQUE (project_id, task_id)
);
INSERT INTO task_new SELECT * FROM task;
DROP TABLE task;
ALTER TABLE task_new RENAME TO task;
-- + пересоздание индексов/FK; downgrade зеркален.
```

### 4.2. `0028_findings` — дословно из RFC 22 §3.2

```sql
CREATE TABLE finding (
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    finding_uid VARCHAR(36) NOT NULL UNIQUE,          -- uuid7
    source VARCHAR(32) NOT NULL,                      -- 'ai_review' | 'zairgrush' | 'routine'
    source_ref VARCHAR(255),                          -- PR#, exp id, routine name
    fingerprint VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,                    -- critical|major|minor|info
    kind VARCHAR(32),
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
CREATE TABLE finding_source_run (   -- одна строка на (находка, внешний прогон)
    row_id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES finding(row_id) ON DELETE CASCADE,
    source_run_id VARCHAR(128) NOT NULL,              -- sha+run для ai_review
    ts DATETIME NOT NULL, severity_at_run VARCHAR(16), raw JSON
);
CREATE TABLE external_ref (         -- мост идентичностей
    row_id INTEGER PRIMARY KEY,
    project_id INTEGER NOT NULL REFERENCES project(row_id) ON DELETE CASCADE,
    entity_kind VARCHAR(32) NOT NULL, entity_row_id INTEGER NOT NULL,
    system VARCHAR(32) NOT NULL, external_id VARCHAR(128) NOT NULL, url TEXT,
    UNIQUE (project_id, system, external_id)
);
```

Обоснование «не activity_event / не task» (RFC 22): дедуп требует
индексированного ключа и UPDATE (`times_seen`, `last_seen_at`) — мутировать
таблицу аудита нельзя; большинство находок задачами не станут, а глобальный
`task_id` сжигал бы ID на каждую пересозданную находку.

### 4.3. Фингерпринты (`finding_service/fingerprint.py`, чистые функции — никогда в адаптерах)

| source | формула | деградация |
|---|---|---|
| `ai_review` | `sha256(source\|path\|fp\|severity)`; `fp` — код-фингерпринт движка (переживает сдвиг строк) | пустой `fp` → `normalized_title`, пометка `payload.fp_basis="title"` |
| `zairgrush` | `sha256(source\|exp\|variant\|kind)` | `note` (проза) в ключ не идёт |
| `routine` | `sha256(source\|check_name\|scope_kind\|scope_id)` | — |

### 4.4. Конфиг hub

```python
class ProjectEntry(BaseSettings):
    db_url: str | None = None   # None → embedded <root>/.cod-doc/state.db

def db_for_entry(entry: ProjectEntry) -> tuple[sessionmaker[Session], Engine]: ...
```

Ограничения: hub только на локальном диске (не iCloud/NFS); `alembic upgrade
head` по hub — при остановленных петлях; собственная БД cod-doc в hub **не
переезжает** (контрольная группа).

## 5. Definition of Done спринта

- [x] G1: SYM-003, ADO-016, ADO-017 → `done` в БД через `task_complete` с `commit_sha` (2026-08-28: ae5911e / 1a66aaa / 1a66aaa).
- [ ] G2: SYM-005A–D → `done`; SYM-006A/B → `done`.
- [ ] G3: ADO-005 ≥5 наблюдений в journal; ADO-007 routine работает на пилоте.
- [ ] `ruff check`, `ruff format --check`, `mypy cod_doc/`, `pytest --timeout=120` — зелёные.
- [ ] Activity events на всех новых write-тулах (proposal 09).
- [ ] ROADMAP.md + MASTER.md обновлены (чекбоксы M1, changelog).
- [ ] Audit-отчёт `docs/system/audit/2026-09-10-sprint-m1-phase1.md` (skill `audit-cadence`).

## 6. Риски

- **Ветка `worktree-swarm-ado022-ado015-sym004` не влита** — SYM-005B стартует
  только после merge, иначе две миграции 0026 в дереве.
- **Две копии движка ai-review** (`ops/pr-review/` в Orakul и
  `/Users/dakh/Git/_my/ai-reviewer`) — адаптер пиннится к `payload.version`,
  не к пути.
- **SYM-006C/D могут не влезть** — сознательный стретч; перенос не провал G2
  при закрытых SYM-006A/B.
- **Импорт затащит архивный markdown** — `--dry-run` обязателен, решение по
  архивам до импорта (playbook).
- **Пилоты «заведены и заброшены»** — G3 в этом спринте, не отложено в M2.
- **Темп ZAIrgRush** (110 коммитов/2 недели) — любые патчи их петли (SYM-008,
  не в этом спринте) — один небольшой PR за присест.
