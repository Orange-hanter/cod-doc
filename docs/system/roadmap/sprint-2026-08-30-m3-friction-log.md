---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-08-30
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-29-hardening-m3-kickoff.md
  - ../audit/2026-09-05-sprint-h1-hardening.md
  - ../audit/2026-09-11-sprint-m2-feedback-loop.md
---

# Sprint 2026-08-30 → 2026-09-06 — «M3: friction-log leftovers»

> **Назначение.** Закрыть остатки friction-лога ADO-005 — записи
> **#8 / #10 / #11 / #14** (findings F1/F2/F3/F4 аудита M2). Решение о
> перенацеливании M3 с RFC 16–21 на friction-лог принято владельцем в
> спринте H1 (ADO-056) и зафиксировано в audit-отчёте H1. Окно 1 неделя
> (2026-08-30 → 2026-09-06).
>
> **Не source of truth.** Статусы задач — в БД (план `adoption-2026-08`);
> приоритеты — [ROADMAP.md](ROADMAP.md).

## 0. Ground truth на старт спринта (сверено 2026-08-30)

- H1 закрыт досрочно: audit
  [2026-09-05-sprint-h1-hardening.md](../audit/2026-09-05-sprint-h1-hardening.md),
  финальный коммит `672659e`.
- Suite 1585 passed (`pytest tests/ --timeout=120`), mypy чист (317 файлов),
  drift 127/127 in_sync, дерево git чистое.
- Ratchet-база: `pyproject.toml [per-file-ignores]` — 6 записей.
- Friction-лог ADO-005: открыты ровно #8 (dry-run лимит 50 строк),
  #10 (битый path для `*.txt` → drift missing), #11 (скрытые каталоги —
  docs), #14 (чужой frontmatter `type:` → молчаливая подмена в module-spec).
- Стретч из H1: ADO-039 (enforce atomic checkout, high, секция D) и
  SYM-008 (петля E5-C) — обе задачи уже в БД, новых не создаём.

## 1. Цели

- **G1 — bug #10.** Импортёр нормализует `path` для не-md файлов
  (`.txt/.rst/.markdown`): drift больше не показывает их как `missing`.
- **G2 — UX #8.** `--dry-run` импорта: полный список кандидатов доступен
  (`--limit` / вывод в файл), дефолтный превью-лимит 50 сохраняется с
  явным хвостом-подсказкой.
- **G3 — импортёр-дисциплина #14 + #11.** Маппинг чужого frontmatter
  `type:` (diataxis/quadrant) в DocumentType с warning вместо молчаливой
  подмены (аналог ADO-015); скип скрытых каталогов задокументирован и
  виден в dry-run.
- **Стретч (по остатку темпа):** ADO-039, SYM-008.
- **Вне скоупа:** ADO-057 (ctx CLI), ADO-040 (большой write-path wrapper),
  ADO-042…051, реализация RFC-фич Трека B.

## 2. Контракты задач

Привязка к коду сверена 2026-08-30.

- **#10 (bug, high):** после `import docs` для `.txt/.rst/.markdown`
  `Document.path` указывает на реальный файл, а не `<doc_key>.md`;
  `doc drift` → in_sync. Корень: `_doc_key_for` стрипает расширение
  (`cod_doc/services/restate_importer.py:195-200`), path по умолчанию
  собирается как `<doc_key>.md`. Регрессионный тест падает на main без
  фикса (красный прогон — в task-doc 'verification').
- **#8 (feature, high):** `cod-doc import docs --dry-run --limit N`
  (0 = без лимита) и/или `--output FILE` для полного списка; дефолт 50 +
  строка-хвост «… and K more (use --limit 0)». Код: `_DRY_RUN_PREVIEW_LIMIT`
  (`cod_doc/cli/cmd_import.py:25`). Тест на синтетическом корпусе >50
  файлов.
- **#14 (bug, medium):** таблица маппинга чужих `type:` (наблюдаемые
  значения из пилотов: diataxis/quadrant) → `DocumentType` в
  `import_service`; неизвестный тип — warning в payload импорта, не
  молчаливая подмена в `module-spec`. Код: `_enum_or_default`
  (`cod_doc/services/import_service.py:140-147`). Прецедент: ADO-015
  (`f77bd75`). Регрессионный тест с красным прогоном.
- **#11 (docs, medium):** скип скрытых каталогов задокументирован (skill
  `project-onboarding` + help импорта); dry-run печатает счётчик/список
  пропущенных hidden-dirs. Код: `_SKIP_DIRS` и фильтр `p.startswith(".")`
  (`restate_importer.py:52-56,187`).
- **Стретч ADO-039:** решение Phase-2 enforce зафиксировано в task-doc
  'design' до кода; переход todo→in_progress только через `task_checkout`
  на всех поверхностях (MCP/web/CLI); AGENTS.md §5.3 синхронен с кодом.
- **Стретч SYM-008:** только если сверка подтвердит разблокировку.

## 3. Порядок исполнения

1. Оформление: этот sprint-док (doc create + import), указатель в
   ROADMAP.md, задачи #10/#8/#14/#11 в БД (секция C — прецедент
   ADO-030…033).
2. G1: friction #10 (bug → сначала красный прогон).
3. G2: friction #8.
4. G3: friction #14 (bug → красный прогон) → #11.
5. Стретч: ADO-039 → SYM-008.
6. Финал: полный suite + ruff + mypy + drift; audit-отчёт
   `docs/system/audit/2026-09-06-sprint-m3-friction.md`; чекбоксы DoD;
   закрытие спринта.

## 4. Риски

- **#10 может вскрыть рассинхрон существующих txt-строк в БД:** фикс path
  для уже импортированных записей — отдельный пункт задачи (backfill),
  не раздуваем скоуп.
- **#14 — риск перекоса маппинга под чужой стандарт:** маппим только
  наблюдаемые значения; всё неизвестное — warning, не угадывание.
- **Темп:** H1 закрылся за день; недельное окно — с большим запасом,
  стретч реален.

## 5. Definition of Done

Каждый пункт проверяем одной командой/одним артефактом — не «сделано», а
«доказано».

- [x] Каждая bug-задача (#10, #14) имеет регрессионный тест, который
      падает на main без фикса; красный прогон записан в task-doc
      'verification'.
- [x] Все задачи спринта прошли `task_checkout` → `task_complete` с
      `commit_sha`; «висячих» in-progress нет.
- [x] Каждая правка трекаемого `.md` закрыта `doc import` в том же
      коммите; финальный `doc drift --all` — 100% in_sync.
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` +
      `pytest tests/ --timeout=120` — зелёные на последнем коммите.
- [x] Ratchet не вырос: `per-file-ignores` ≤ 6 записей; новых
      `# noqa`/`# type: ignore` без обоснования — ноль (идиома
      тест-файлов `no-untyped-def` допустима).
- [x] Friction-лог ADO-005: 0 открытых записей.
- [x] Audit-отчёт `2026-09-06-sprint-m3-friction.md` — status active,
      в БД, со ссылками на все коммиты спринта.
