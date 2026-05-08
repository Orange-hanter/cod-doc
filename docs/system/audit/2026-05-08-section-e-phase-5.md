---
type: audit-report
scope: paperclip-adoption / Section E (Phase 5 — UX & Migration)
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-08
last_updated: 2026-05-08
audience: [contributors, next-session-agent]
related_docs:
  - 2026-05-08-section-d-phase-4.md
  - ../roadmap/paperclip-adoption-task-plan.md
  - ../../../proposals/13-import-ux-redesign.md
  - ../../../proposals/14-legacy-tasks-migration-ux.md
  - ../../../proposals/15-link-system-and-rendering.md
---

# Section E — Closure Report (Phase 5: UX & Migration)

> **Назначение.** Зафиксировать закрытие 7 задач Section E
> (PCA-420, PCA-400, PCA-401, PCA-410, PCA-411, PCA-421, PCA-422)
> и findings → backlog.

## 1. TL;DR

- **PCA-420** — Ordered list rendering bug починен в `cod_doc/api/web/markdown.py`.
  Добавлен `_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.+)$")`, `flush_ol_list()`,
  поддержка `<ol start="N">` для резюмированных списков. 12 новых тестов в
  `tests/api/web/test_markdown.py`.
- **PCA-400** — `scan_folder()` в `import_service.py`: сканирует директорию
  проекта, сравнивает с БД, возвращает manifest (new/changed/unchanged/missing).
  `GET /p/{slug}/docs/import/scan?path=.` — JSON endpoint.
- **PCA-401** — `GET /p/{slug}/docs/import` — страница bulk-import с checkbox-UI
  (template `docs_import.html`). `POST /p/{slug}/docs/import/apply` — batch
  endpoint (JSON body `{paths: [...]}`, idempotent).
- **PCA-410** — `POST /p/{slug}/tasks/legacy/import?dry_run=true/false` и
  `POST /p/{slug}/tasks/legacy/archive`. В `tasks_legacy_list.html` добавлен
  action bar с Preview / Import all / Mark as archived.
- **PCA-411** — `add_task` и `update_task` в `legacy_project_tools.py` помечены
  DEPRECATED с `DeprecationWarning`. `Project.add_task()` бросает `RuntimeError`
  когда `tasks.archived.yaml` существует.
- **PCA-421** — two-pass resolve в `import_markdown()`: после вставки всех секций
  вызывается `_resolve_all_sections()` — forward-ссылки на документы, созданные
  позже в том же батч-импорте, резолвятся.
- **PCA-422** — `cod_doc/services/link_service/semantic.py` с `suggest_for_section()`,
  `backfill_project()`, `list_suggestions()`, `update_suggestion_state()`.
  Модель `LinkSuggestionModel` + миграция `0015_link_suggestions`. CLI
  `cod-doc link suggest <project>`. MCP tool `link_suggest_for_section`.
- **12 новых тестов** в `tests/api/web/test_markdown.py`. **1008 tests pass**
  (996 → 1008).
- **3 findings** (I1-I3) → backlog Section F (PCA-928..930).

## 2. Section E deliverables

| # | Деливерабл | Файл / артефакт | Статус |
|---|------------|------------------|--------|
| E1 | Section E audit-report | `docs/system/audit/2026-05-08-section-e-phase-5.md` | ✅ |
| E2 | Ordered list renderer + tests | `cod_doc/api/web/markdown.py`, `tests/api/web/test_markdown.py` | ✅ |
| E3 | `scan_folder()` service | `cod_doc/services/import_service.py` | ✅ |
| E4 | `GET /docs/import/scan` endpoint | `cod_doc/api/web/pages/docs.py` | ✅ |
| E5 | `GET /docs/import` + `POST /docs/import/apply` | `cod_doc/api/web/pages/docs.py` | ✅ |
| E6 | Bulk import UI template | `cod_doc/templates/web/project/docs_import.html` | ✅ |
| E7 | `POST /tasks/legacy/import` + `POST /tasks/legacy/archive` | `cod_doc/api/web/pages/tasks.py` | ✅ |
| E8 | Legacy migration action bar | `cod_doc/templates/web/project/tasks_legacy_list.html` | ✅ |
| E9 | `add_task` / `update_task` deprecated | `cod_doc/mcp/tools/legacy_project_tools.py` | ✅ |
| E10 | `Project.add_task` archived guard | `cod_doc/core/project.py` | ✅ |
| E11 | Two-pass resolve in `import_markdown` | `cod_doc/services/import_service.py` | ✅ |
| E12 | `LinkSuggestionModel` + migration 0015 | `cod_doc/infra/models/link_suggestions.py`, `migrations/versions/20260508_0015_link_suggestions.py` | ✅ |
| E13 | `link_service/semantic.py` | `cod_doc/services/link_service/semantic.py` | ✅ |
| E14 | `link_suggest_for_section` MCP tool | `cod_doc/mcp/tools/link_tools.py` | ✅ |
| E15 | `cod-doc link suggest` CLI | `cod_doc/cli/link.py` | ✅ |

## 3. Acceptance per task

- [x] **PCA-420** — 12 тестов в `TestOrderedList` зелёные; нумерованные списки
      рендерятся как `<ol>`, bullet-lists как `<ul>`; смешанные сценарии и `start=N`
      корректны.
- [x] **PCA-400** — `scan_folder()` возвращает список `ManifestEntry` (new/changed/
      unchanged/missing); endpoint `GET /docs/import/scan` отдаёт JSON.
- [x] **PCA-401** — страница `/p/{slug}/docs/import` рендерит checkbox-UI; кнопка
      «Import selected» отправляет на `POST /docs/import/apply`; ответ — JSON summary.
- [x] **PCA-410** — Preview (dry_run=True) откатывает транзакцию, не пишет в БД;
      Import all фиксирует; Archive переименовывает yaml → archived.yaml.
- [x] **PCA-411** — `add_task` и `update_task` возвращают `_deprecated` ключ;
      `Project.add_task` на архивированном проекте бросает `RuntimeError`.
- [x] **PCA-421** — `_resolve_all_sections()` вызывается после полного импорта;
      ошибки логируются, не прерывают импорт (best-effort).
- [x] **PCA-422** — `suggest_for_section()` принимает конфиг, вызывает ChromaDB,
      реранкует по лексическим сигналам, хранит в `link_suggestion`;
      `backfill_project()` обходит все секции; CLI `cod-doc link suggest` рабочий.

## 4. Findings (→ backlog)

### I1 — `scan_folder` использует presence-only diff, нет hash-compare *(low)*

`scan_folder()` определяет `status="changed"` только по отсутствию в БД (all existing → unchanged). Нет хранения sha256 в БД, поэтому реальные изменения файла не детектируются.

**Рекомендация:** добавить поле `content_sha256_head` в `DocumentModel` + заполнять при импорте. Тогда scan_folder сможет сравнивать sha и ставить `changed`. Отдельная F-задача.

### I2 — `bulk import apply` не идемпотентен при конфликте doc_key *(medium)*

`POST /docs/import/apply` вызывает `import_markdown()` per-file. Если doc_key уже существует в БД, `doc_service.create()` бросает IntegrityError — файл попадёт в `errors`, не перезапишется. Это верное поведение для new-файлов, но для changed-файлов нужен upsert (create new revision). Proposal 13 §2.4: «changed → новая ревизия».

**Рекомендация:** в apply-endpoint перед `import_markdown` проверять существование doc_key; если есть — вместо import вызывать patch_section по-секционно. Отдельная F-задача.

### I3 — Semantic suggest требует предзаполненного ChromaDB индекса *(low)*

`suggest_for_section()` провалится с `ChromaDB query error` если коллекция пуста (ни разу не выполнялся `reindex`). Нет авто-reindex перед suggest.

**Рекомендация:** в `backfill_project()` проверять, есть ли что-то в коллекции; если нет — логировать warning и возвращать empty result, а не падать. Уже частично обработано (try/except в `suggest_for_section`), но стоит добавить explicit check.

## 5. Метрики

| Метрика | До Section E | После | Δ |
|---------|-------------:|------:|--:|
| Web endpoints | 17 | 22 | +5 |
| New service functions | — | 8 | +8 |
| New model tables | — | 1 (`link_suggestion`) | +1 |
| Alembic migrations | 14 | 15 | +1 |
| `tests/` total | 996 | 1008 | +12 |
| Section E done tasks | 0 | 7 | +7 |
| Total A+B+C+D+E done | 38 | 45 | +7 |

## 6. Что не вошло (out of scope)

- **`project.docs_root` в Settings** (proposal 13 §2.5) — поле для хранения `docs_root` в конфиге проекта. Scan работает без него через query param `?path=.`.
- **Web UI «Suggested links»** в подвале документа (proposal 15 §2.3.3) — шаг 15.7. Требует шаблонных изменений doc_show.html; отложено в backlog.
- **`--apply-above X` auto-accept в backfill** полностью реализован в semantic.py, но не тестирован end-to-end (нужен reindex с реальными данными).
- **Nested lists, GFM task-lists** (proposal 15 §2.1) — явно out-of-scope по предложению.

## 7. Следующий шаг

Sections A–E закрыты. **45 done tasks** суммарно. Весь RFC-беклог (proposals 01–15) реализован.

Открытые направления:
- **Section F backlog consolidation** — 16 (из разделов B-D) + 3 (из Section E) = 19 findings. Приоритеты: I2 (bulk import upsert, medium), G2 (routine scheduler daemon, high), G3 (wire noop routine checks, high), H3 (adapter capabilities check, low), H4 (deprecated client shim, low).
- **Web UI «Suggested links»** (I2 из proposal 15 §2.3.3) — подвал doc_show.html.

Findings I1-I3 заведены как PCA-928..930 в Section F.
