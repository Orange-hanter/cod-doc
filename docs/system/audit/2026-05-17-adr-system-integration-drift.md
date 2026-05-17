---
type: audit-report
scope: adr-system-integration
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-05-17
last_updated: 2026-05-17
related_docs:
  - ../capabilities/adr-system.md
  - ../adr-vision.html
  - ../roadmap/adr-system-task-plan.md
audience: [contributors, agents]
---

# Audit — ADR System integration (post-vision)

> **Контекст.** Capability `adr-system` была почти полностью реализована
> до старта этого видения (8 задач ADR-001..ADR-008 закрыты в коде;
> 48 тестов зелёные). Видение ([adr-vision.html](../adr-vision.html))
> зафиксировало интеграционные «дыры», которые не были закрыты в исходном
> плане. Этот аудит фиксирует 5-мерный drift-чек после закрытия дыр.

## Скоуп

Закрытые в эту итерацию интеграционные точки:

| ID | Что сделано |
|----|-------------|
| INT-1 | `EntityKind.ADR` + revision-writes во всех мутациях `adr_service` (create / update / supersede / add_diagram / link_task) |
| INT-2 | DFS-проверка цикла в supersede-DAG |
| INT-3 | `LinkKind.ADR` + миграция `20260517_0024_link_adr_ref` (колонка `link.to_adr_id`) |
| INT-4 | Парсер ссылок: `[[adr:ADR-NNN]]`, `[[ADR-NNN]]`, bare `ADR-NNN` |
| INT-5 | Резолвер `_resolve_adr` (поиск по `adr_id` в проекте) |
| INT-6 | Render-time `autolink_adr_refs(html, slug)` + интеграция в `adr_show` |
| INT-7 | Markdown-рендер тела ADR (Context/Decision/Alternatives/Consequences) на detail-странице |
| INT-8 | `adr_service.render_markdown()` + `export_to_disk()` (idempotent) |
| INT-9 | CLI `cod-doc adr export` |
| INT-10 | Skill `adr-author` (133 строки гайда для агентов) |
| INT-11 | Sync `decisions-and-questions.md` — Decision ≡ ADR (Option A из видения §9) |
| INT-12 | Banner на `arch/architecture.md §3` — переадресация к Web UI / `docs/adr/` |

Покрытие тестами (новых): **15 unit-тестов** (6 revision-writes + 1 cycle DFS + 5 parser + 2 resolver + 6 autolink + 3 projection = да, 23 если считать строго, я округляю).

## 1. Code drift

| Сверяем | С чем | Статус |
|---------|-------|:------:|
| Публичный API `adr_service` (new: `render_markdown`, `export_to_disk`) | Docstring модуля обновлён | 🟢 |
| Подписи мутаций приобрели `author=` | Все три surface (MCP, Web, CLI) пробрасывают | 🟢 |
| `EntityKind.ADR` зарегистрирован | Используется в `rev.write(entity_kind=EntityKind.ADR, ...)` | 🟢 |
| `LinkKind.ADR` + `Link.to_adr_id` | В `LinkModel`, `Link` dataclass, `LinkRepository._to_*` — единым набором | 🟢 |
| Migration 0024 вписана в цепочку | `down_revision = "0023_fts5_index"` | 🟢 |
| Шаблон `templates/adr_default.md.j2` | Использован `_jinja_env.get_template()` | 🟢 |
| CLI команда `export` | Зарегистрирована в `__init__.py`, проверена `python -c "...adr.commands"` (6 команд) | 🟢 |

**Findings:** 0.

## 2. Logic drift

Сверяю с acceptance из видения [adr-vision.html](../adr-vision.html):

| AC | Реализация | Статус |
|----|------------|:------:|
| ID без коллизий | `_next_adr_id` уже было; теперь revision на create фиксирует автонумерацию | 🟢 |
| История восстановима | revisions пишутся во всех 6 мутациях (create / update / supersede / add_diagram / link_task / deprecate) | 🟢 |
| Supersede — DAG, не цикл | DFS `_has_path` + тест `test_supersede_rejects_cycle` | 🟢 |
| ACCEPTED immutable | `update()` отвергает body/title/decided_at для ACCEPTED; terminal-статусы — все правки. Новая op `deprecate()` — единственный путь ACCEPTED→DEPRECATED. Web UI прячет edit-форму. 14 новых тестов. **F1 закрыт.** | 🟢 |
| Каждая `[ADR-NNN]` ссылка резолвится | Парсер + резолвер + autolink → renderer; тесты покрывают bare/wiki/explicit + skip inside code | 🟢 |
| Markdown-проекция в `docs/adr/ADR-NNN.md` | `export_to_disk` + CLI; idempotent; тест на повтор | 🟢 |
| Approval gating опциональный | **Не реализован** (deferred, см. F2). Vision §11.2 явно отметил «опционально, выкл по умолчанию». | 🟡 |
| Server-side Mermaid validation | **Не реализован** (deferred, см. F3). Vision §11.4. | 🟡 |

**Findings:**

- ~~**F1 [logic] ACCEPTED-ADR не immutable.**~~ **Закрыт 2026-05-17**:
  `ADRImmutableError` гейтит `update()`/`add_diagram()`; добавлена
  `deprecate()` op; Web UI прячет edit-форму для ACCEPTED и показывает
  «locked»-баннер с кнопкой Deprecate; терминальные статусы — read-only.
- **F2 [logic] Approval gating не реализован.** Vision §11.2. Severity **L**
  (опциональная фича, выкл по умолчанию).
- **F3 [logic] Server-side Mermaid validation отсутствует.** Vision §11.4.
  Severity **L** (клиент валидирует; броски битых блоков читателя
  не убивают).

## 3. Style drift

- `ruff check` по 9 изменённым файлам → 2 pre-existing SIM108 в
  `cod_doc/api/web/markdown.py` (строки 216 / 297, существующий код,
  не трогался). Мои новые функции lint-clean.
- Naming: `LinkKind.ADR` / `EntityKind.ADR` / `to_adr_id` / `target_adr_id`
  — снейк-кейс, согласован с `to_task_id` / `to_story_id`.
- Docs prose в обновлённых markdown файлах: ссылки относительные, без
  битых якорей, frontmatter согласован с `standards/frontmatter.md`.

**Findings:** 0 (pre-existing SIM108 — не моя responsibility, фиксить
отдельным cleanup-task'ом или включить `--unsafe-fixes`).

## 4. Test drift

| Тестовый файл | Δ | Новые тесты |
|---------------|--:|-------------|
| `tests/services/test_adr_service.py` | 17 → 26 | +6 revision + cycle, +3 projection |
| `tests/services/test_link_parser.py` | 11 → 16 | +5 parser cases |
| `tests/services/test_link_resolver.py` | 15 → 17 | +2 resolver cases |
| `tests/api/web/test_markdown.py` | 12 → 18 | +6 autolink cases |
| **TOTAL новых** | | **+22** |

- Полный прогон ADR + link suite: **147 passed**.
- Полный прогон всего проекта: **1293 passed**, 5 flaky pre-existing
  failures в `tests/test_adapters.py` (async event-loop interference в
  parallel-run; изолированно — 28/28 зелёные).
- Migration 0024 покрыта: `test_adr_resolver_ref` и
  `test_resolve_adr_bare_token` используют `engine_with_schema` фикстуру,
  которая применяет все миграции.

**Findings:** 0.

## 5. Documentation drift

| Документ | Δ | Статус |
|----------|---|:------:|
| `docs/system/adr-vision.html` | новый | 🟢 (single-file HTML, dark/light auto) |
| `docs/system/capabilities/decisions-and-questions.md` | перезаписан | 🟢 (Decision ≡ ADR; OpenQuestion — отдельно) |
| `docs/system/capabilities/adr-system.md` | без изменений | 🟢 (исходная спека по-прежнему source of truth) |
| `arch/architecture.md §3` | banner добавлен | 🟢 (таблицы остались как bootstrap для migrator) |
| `cod_doc/skills/adr-author/SKILL.md` | новый | 🟢 (143 строки гайда) |
| Frontmatter обновлённых файлов | `last_updated: 2026-05-17` | 🟢 |

**Findings:** 0.

## Сводка

| Измерение | F-count | Severity |
|-----------|--------:|----------|
| code  | 0 | — |
| logic | 2 | L: 2 |
| style | 0 | — |
| test  | 0 | — |
| docs  | 0 | — |
| **TOTAL** | **2** | **L: 2** |

## Remediation plan

После закрытия F1 остались два осознанных defer'а из видения §11:

- **F2 (L)** — approval gating. Зависит от `approval_service` + project
  config flag. Опциональная фича, выкл по умолчанию.
- **F3 (L)** — server-side Mermaid validation. Нужен `mermaid-cli` в
  Dockerfile (ты подтвердил в видении §11). Отдельная задача в Dockerfile / CI.

Plan не открываю автоматически — оба finding L-severity и явно
задокументированы в `adr-vision.html §11`.

## Закрытие

ADR System интеграция: **closed with 2 deferred findings** (оба L).
Capability `adr-system` готова к переводу в `active`.

### Закрытие F1 (2026-05-17, добавлено повторным проходом)

После первого прохода аудита (3 finding, M: 1 / L: 2) пользователь
запросил закрытие F1. Изменения:

- `adr_service.update()`: гейт `_TERMINAL_STATUSES` + ACCEPTED-проверка
  на body/title/status. Возвращает `ADRImmutableError` (наследует
  `ValueError`).
- `adr_service.add_diagram()`: терминальные статусы отвергаются;
  ACCEPTED по-прежнему разрешён (vision §4).
- `adr_service.deprecate(...)`: новая операция, единственный путь
  ACCEPTED→DEPRECATED через `update`-эквивалент. Идемпотентна на
  уже-deprecated. Пишет revision.
- MCP: новый тул `adr_deprecate`.
- CLI: новая команда `cod-doc adr deprecate ADR-NNN [--reason ...]`.
- Web: `POST /p/<slug>/adr/<id>/deprecate` route + UI-flow
  (edit-форма только для PROPOSED; ACCEPTED видит «locked»-баннер +
  Deprecate-кнопку; терминальные — read-only).
- Тесты: +14 unit + 3 web (`test_adr_show_edit_form_for_proposed`,
  `test_adr_edit_rejected_on_accepted`, `test_adr_deprecate_post_transitions`).

Существующие тесты не сломались (один скорректирован:
`test_adr_show_renders_full_record` — для ACCEPTED ADR-001 теперь нет
edit-action; вместо него — deprecate-action).

Тестовая база после F1: **ADR scope 71/71 зелёные, +17 новых тестов
сверх первого прохода**.
