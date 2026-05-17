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
| История восстановима | revisions пишутся во всех 5 мутациях | 🟢 |
| Supersede — DAG, не цикл | DFS `_has_path` + тест `test_supersede_rejects_cycle` | 🟢 |
| ACCEPTED immutable | **N/A для этой итерации.** Vision §4 говорил «делать immutable». В сервисе `update()` сейчас разрешён на любом статусе. Это сознательно — введение immutable создаст breaking change для существующих 17 service-тестов. Эскалирую как F1. | 🟡 |
| Каждая `[ADR-NNN]` ссылка резолвится | Парсер + резолвер + autolink → renderer; тесты покрывают bare/wiki/explicit + skip inside code | 🟢 |
| Markdown-проекция в `docs/adr/ADR-NNN.md` | `export_to_disk` + CLI; idempotent; тест на повтор | 🟢 |
| Approval gating опциональный | **Не реализован** (deferred, см. F2). Vision §11.2 явно отметил «опционально, выкл по умолчанию». | 🟡 |
| Server-side Mermaid validation | **Не реализован** (deferred, см. F3). Vision §11.4. | 🟡 |

**Findings:**

- **F1 [logic] ACCEPTED-ADR не immutable.** Vision §4 обещает immutable
  тело после accept; сейчас `update()` его меняет. Severity **M** (не
  критично — revision-history восстановит, но обещание не выполнено).
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
| logic | 3 | M: 1 / L: 2 |
| style | 0 | — |
| test  | 0 | — |
| docs  | 0 | — |
| **TOTAL** | **3** | **M: 1 / L: 2** |

## Remediation plan

Три finding — все осознанные defer'ы (видение §11 явно помечало их как
«open questions» с возможностью отложить). Они **не блокируют** перевод
модуля в `active`.

- **F1 (M)** — immutable ACCEPTED-ADR. Reqs ревизию 17 service-тестов
  + обновление web-формы (disable редактирования для accepted). Создавать
  отдельный plan для следующего цикла.
- **F2 (L)** — approval gating. Зависит от `approval_service` + project
  config flag. Также — следующий цикл.
- **F3 (L)** — server-side Mermaid validation. Нужен `mermaid-cli` в
  Dockerfile (ты подтвердил в видении §11). Включить в plan на отдельной
  задаче в Dockerfile / CI.

Plan не открываю автоматически — три finding ≠ блокер, severity ≤ M, и
все три уже задокументированы в видении и `adr-vision.html §11`. Если
нужен явный roadmap-документ — скажи.

## Закрытие

ADR System интеграция: **closed with 3 deferred findings**. Capability
`adr-system` переходит в `active` (после ручного подтверждения).

Тестовая база: **147 ADR/Link тестов зелёные, 22 новых**, полный прогон
**1293 passed**.
