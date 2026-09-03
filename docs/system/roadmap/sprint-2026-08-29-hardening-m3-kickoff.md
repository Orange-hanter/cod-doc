---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-29
last_updated: 2026-08-29
closed: 2026-08-29 (досрочно)
audit: ../audit/2026-09-05-sprint-h1-hardening.md
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-28-m2-feedback-loop.md
  - ../audit/2026-09-11-sprint-m2-feedback-loop.md
  - ../audit/2026-08-29-contract-audit.md
---

# Sprint 2026-08-29 → 2026-09-05 — «H1: hardening + M3-кикофф»

> **Назначение.** Закрыть критикалы и high-находки contract-аудита
> (ADO-034) и провести аналитический кикофф M3 — выбор фичи по спросу без
> реализации. Решения владельца: формат «харденинг + M3-кикофф», окно 1
> неделя (2026-08-29 → 2026-09-05).
>
> **Не source of truth.** Статусы задач — в БД (план `adoption-2026-08`);
> приоритеты — [ROADMAP.md](ROADMAP.md).

## 0. Ground truth на старт спринта (сверено 2026-08-29)

- M2 закрыт досрочно: audit
  [2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md),
  коммит `30c43f7`.
- Contract-аудит ADO-034 закрыт: отчёт
  [2026-08-29-contract-audit.md](../audit/2026-08-29-contract-audit.md),
  коммит `fd84f0b`; 21 задача в backlog section D (ADO-035…055).
- Suite 1562 passed, drift 125/125 in_sync (CLI), дерево git чистое.
- Сверка SYM-005/006 проведена 2026-08-29: **обе задачи done** — код и
  тесты покрывают acceptance (hub init, миграции 0026/0027/0028, dedup
  times_seen, 8 конкурентных ingest, api/v1, MCP finding_*/ctx_*).
  Единственный gap — CLI `cod-doc ctx` — вынесен в ADO-057 (medium, backlog).
- Friction-лог ADO-005: открытые кандидаты #8 (dry-run лимит 50 строк),
  #10 (битый path для `*.txt`), #11 (скрытые каталоги — docs), #14
  (русскоязычный frontmatter / diataxis-quadrant) — вход для G3.

## 1. Цели

- **G1 — Критикалы аудита.** ADO-035 (loopback-гварда `PATCH /api/config`),
  ADO-036 (DocumentRepository round-trip: `frontmatter_raw`/`title_in_body`/
  `content_sha256_head`), ADO-037 (legacy `/api/projects/*` — решение
  «удалить vs на сервисы» в task-doc до реализации).
- **G2 — High-харденинг.** ADO-038 (`complete()` + `validate_transition`),
  ADO-055 (import не теряет секции молча), ADO-053 (audience-export не пишет
  в канонический путь), ADO-052 (доки профилей MCP ↔ код: default `agent`,
  counts 6/20/107/111).
- **G3 — M3-кикофф (аналитика, без реализации фичи).** ADO-056: выбор RFC
  Трека B (16–20) по friction-логу M2 (#8/#10/#11/#14) и contract-аудиту;
  перепроверка секции «Текущее состояние» выбранной RFC по коду;
  обоснование выбора записью в ROADMAP; решение о `plan_create`.
  Отбраковка устаревших RFC → пометки в `proposals/README.md`
  (закрывает ADO-013).
- **Книжка:** сверка статусов SYM-005/006 с кодом (прогресс по коду vs БД),
  корректировка БД.

## 2. Контракты задач

### G1 — критикалы

- **ADO-035:** `PATCH /api/config` с нелокального адреса → 403; симметрия с
  `POST /settings` покрыта тестом (`routes.py:49-55` сейчас без
  `ensure_loopback_client`).
- **ADO-036:** round-trip через `DocumentRepository` сохраняет
  `frontmatter_raw`/`title_in_body`/`content_sha256_head` — тест читает
  строку БД напрямую, не dataclass (`document_repo.py:23-66`).
- **ADO-037:** решение (удалить / перевести на сервисы) зафиксировано в
  task-doc 'design' **до** кода; если удаление — эндпоинты отвечают 410/404
  и удалены из `web-frontend.md §3` (audit 0/0); если сервисы — Revision +
  activity event + тесты паритета.

### G2 — high-харденинг

- **ADO-038:** `complete()` на задаче в `cancelled`/`backlog` →
  StatusTransitionError; легальные переходы (`in_progress`→done и пр.)
  продолжают работать — параметризованный тест по ALLOWED_TRANSITIONS
  (`task_service.py:516-550`).
- **ADO-055:** искусственно сломанная секция видна в `ImportReport.warnings`,
  импорт не падает и не молчит — тест (`import_service.py:461-488` двойной
  `except: pass`).
- **ADO-053:** после `export(audience=...)` `doc drift` остаётся in_sync и
  `doc import` того же пути не меняет canonical body — интеграционный тест
  (`export.py:226-272`).
- **ADO-052:** runtime-замер counts воспроизводим одной командой (скрипт в
  task-doc 'verification'), числа в AGENTS.md/profiles.py/server.py help
  совпадают с замером (agent=6/minimal=20/standard=107/full=111);
  `test_mcp_lists_tools` или новый smoke ловит расхождение реестра и
  профилей.

### G3 — M3-кикофф

- **ADO-056:** выбор RFC содержит ссылки на конкретные записи friction-лога
  (ADO-005 journal) и/или findings контракт-аудита; секция «Текущее
  состояние» выбранной RFC перепроверена по коду с датой сверки; решение
  «реализуем / отбракованы все» зафиксировано в ROADMAP.md и task-doc
  'acceptance'. Каждая отбракованная RFC из 16–21 помечена в
  `proposals/README.md` с причиной в одну строку.

### Стретч (по остатку темпа)

- ADO-039 (enforce atomic checkout — Phase-2 решение зафиксировать).
- ADO-041 (agent_service ← mcp.tools._db импорт).
- ADO-054 (`on_finding='create_task'`).
- SYM-008 (петля E5-C) — только если сверка подтвердит разблокировку.

### Вне скоупа

ADO-040 (большой write-path wrapper — кандидат на отдельный спринт),
ADO-042…051, SYM-009/010/011, STB-023, ADO-014, реализация фичи M3.

## 3. Порядок исполнения

1. Оформление: этот sprint-док (doc create + import), указатель в
   ROADMAP.md, задача ADO-056 в БД, сверка SYM-005/006.
2. G1: ADO-035 → ADO-036 → ADO-037 (каждая: checkout → фикс + тест →
   ruff/mypy/pytest → коммит с подтверждением → task_complete с sha).
3. G2: ADO-038 → ADO-055 → ADO-053 → ADO-052.
4. G3: ADO-056 — research-субагент по RFC 16–20 + friction-лог; выбор
   фиксируем в ROADMAP; ADO-013 закрыть в рамках отбраковки.
5. Стретч в порядке списка.
6. Финал: полный suite + ruff + mypy + drift; audit-отчёт
   `docs/system/audit/2026-09-05-sprint-h1-hardening.md`; чекбоксы в
   ROADMAP.md; закрытие спринта.

## 4. Риски

- **ADO-037 — решение «удалить» ломает чьи-то интеграции:** legacy
  `/api/projects/*` публичен; митигация — сначала task-doc с решением
  владельца, deprecation-warning перед удалением.
- **ADO-036 может потянуть рассинхрон существующих строк:** поля уже в схеме
  (миграция 0025), меняются только маппинги — миграция не нужна; если
  вскроется рассинхрон — отдельная задача, не раздуваем.
- **G3 может не найти достойную RFC** (friction указывает на importer/docs,
  а не на RFC 16–20): тогда честный исход — «M3 пересобран под friction»,
  запись в ROADMAP, это не провал спринта.
- **Темп:** прошлые спринты закрывались за 1–2 дня; недельное окно — с
  запасом, стретч реален.

## 5. Definition of Done

Каждый пункт проверяем одной командой/одним артефактом — не «сделано», а
«доказано».

- [x] Каждая bug-задача G1/G2 имеет **регрессионный тест, который падает на
      main без фикса** (вывод `pytest <test>` на коммите до фикса — в
      task-doc 'verification').
- [x] ADO-037: решение в task-doc 'design' до кода (см. контракт выше).
- [x] ADO-056 + отбраковка RFC 16–21 в `proposals/README.md` (закрывает
      ADO-013).
- [x] SYM-005/006: статусы в БД совпадают с кодом (finding_tools.py и
      миграции 0027/0028 в main ↔ статусы задач) — сверка 2026-08-29, обе
      done, gap `cod-doc ctx` → ADO-057.
- [x] Все задачи спринта прошли `task_checkout` → `task_complete` с
      `commit_sha`; в истории нет «висячих» in-progress.
- [x] Каждая правка трекаемого `.md` закрыта `doc import` в том же коммите;
      финальный `doc drift --all` — 100% in_sync (126/126).
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` +
      `pytest tests/ --timeout=120` — зелёные на последнем коммите спринта.
- [x] Ratchet не вырос: `pyproject.toml [per-file-ignores]` — строк не
      больше, чем на старте; новых `# noqa`/`# type: ignore` без
      комментария-обоснования — ноль (grep-проверка по диффу спринта).
- [x] Audit-отчёт `docs/system/audit/2026-09-05-sprint-h1-hardening.md` —
      status active, в БД, со ссылками на все коммиты спринта.
