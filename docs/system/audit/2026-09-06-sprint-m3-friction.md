---
type: audit-report
scope: sprint-m3-friction-log
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-08-30
related_docs:
  - ../roadmap/ROADMAP.md
  - ../roadmap/sprint-2026-08-30-m3-friction-log.md
  - 2026-09-11-sprint-m2-feedback-loop.md
audience: [contributors, agents]
---

# Audit — Спринт 2026-08-30 → 2026-09-06 «M3: friction-log leftovers»

> **Контекст.** Третий спринт по [ROADMAP.md](../roadmap/ROADMAP.md) (план
> `adoption-2026-08`). Цель — закрыть остатки friction-лога ADO-005:
> записи **#8 / #10 / #11 / #14** (findings F1–F4 аудита M2). Стретч:
> ADO-039 (enforce atomic checkout) и SYM-008 (петля E5-C в ZAIrgRush).
> План спринта:
> [sprint-2026-08-30-m3-friction-log.md](../roadmap/sprint-2026-08-30-m3-friction-log.md).

## TL;DR

Спринт закрыт досрочно (2026-08-30, день в день со стартом): все три гола
и оба стретча выполнены, friction-лог обнулён (0 открытых записей из 14).
7 коммитов в main cod-doc + 1 коммит в ZAIrgRush. Инженерное здоровье
зелёное: 1601 тест passed, ruff/mypy/drift чисто (130/130).

## 1. Deliverables

### G1 — friction #10 (ADO-058, bug)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-058 | `ee51e7d` | `path` проброшен через `import_markdown`/`import_or_update_markdown` → `docs.create`: импортированные `.txt/.rst/.markdown` получают реальный путь, drift больше не показывает их как `missing`. Backfill не понадобился (drift был чист). Красный прогон — в task-doc 'verification' |

### G2 — friction #8 (ADO-059, UX)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-059 | `b98b707` | `cod-doc import docs --dry-run --limit N` (0 = весь список, дефолт 50); хвост-подсказка «… ещё K (полный список: --limit 0)»; тесты на корпусе >50 файлов |

### G3 — friction #14 + #11 (ADO-060, ADO-061)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-060 | `523d2ba` | Маппинг чужого frontmatter: `_DIATAXIS_TYPE_ALIASES` (tutorial/how-to→guide, explanation→analysis, reference→module-spec) + fallback-ключи `diataxis`/`quadrant`; только create-путь, update тип сохраняет. Красный прогон — в task-doc 'verification' |
| ADO-061 | `5cbb57d` | Walker собирает пропущенные hidden-dirs; dry-run печатает «Скрытые каталоги пропущены (N): …»; skill `project-onboarding` синхронизирован |

### Стретч — ADO-039 (enforce atomic checkout)

| Задача | Коммит | Содержание |
|---|---|---|
| ADO-039 | `a2f3cfb` | Phase-2 enforce включён: `update_status` бросает `StatusTransitionError` на todo→in_progress без `via_checkout=True` независимо от strict-режима; web-фрагмент идёт через `checkout_service.checkout("human:web")`; CLI `task status` ловит ошибку; 16 мест в 12 тест-файлах переведены на checkout; AGENTS.md §5.3 синхронен |

### Стретч — SYM-008 (петля E5-C) + ADO-057

| Задача | Коммит | Содержание |
|---|---|---|
| SYM-008 / ADO-057 | `0282835` (cod-doc), `4110cc6` (ZAIrgRush) | cod-doc: CLI `cod-doc ctx docs\|drift\|search --json` — read-only (`commit=False`), контракт `{docs, links_at_risk, token_estimate}`, 7 cli-тестов. ZAIrgRush: `swarm/docctx.py` (subprocess + предохранитель), `promptbuilder.docs_block` (per-task кэш, байт-стабильность при off), `handoff(docs=)`, `[experiments] doc_context = off\|executor\|reviewer\|all`, трейлеры `Swarm-Task:`/`Cod-Doc-Task:`; `tools/swarm/check.sh` зелёный (1219 passed) |

Отклонения SYM-008 от RFC 22 §3.4: блок документов подмешивается только в
handoff исполнителя (reviewer не проведён); трейлер `Cod-Doc-Task:` —
детерминированная заглушка `ZRG-{id}` (поля зеркальной задачи в tasks.json
пока нет).

Оформление спринта — коммит `9b596a6` (кикофф-док, задачи ADO-058…061,
указатель в ROADMAP).

## 2. Инженерное здоровье (на конец спринта)

| Проверка | Результат |
|---|---|
| `pytest tests/` | 1601 passed |
| `ruff check` / `format --check` | чисто |
| `mypy cod_doc/` | чисто (318 файлов) |
| `doc drift -p cod-doc --all` | 130/130 in_sync |
| Ratchet `per-file-ignores` | 6 записей (база H1), роста нет |

## 3. Findings

- **F1 (низкий, backlog).** `ctx docs --paths` фильтрует по `path`/`doc_key`
  документов, а не по файлам кода: для выборки «документы про tools/swarm»
  нужен glob по md-путям. При употреблении в ZAIrgRush это значит, что
  `task.paths` (`.py`-файлы) надо отображать на doc-пути — кандидат на
  маппинг в `docctx.py` при реальном включении `doc_context`.
- **F2 (низкий).** Наивная токен-эвристика `len(utf-8)//4` завышена для
  кириллицы (~2 байта/символ). Для бюджетирования контекста достаточно,
  но в docs стоит зафиксировать, что это верхняя оценка.
- **F3 (перенос).** F3 аудита M1 (single-file upload не обновляет hash) и
  F5/F7 аудита M2 остаются в backlog Section F — в скоуп не брались.
- **F4 (процесс, положительный).** Досрочное закрытие второго спринта
  подряд: недельное окно оказалось избыточным для friction-скоупа.
  На планировании M4 окно стоит сокращать или скоуп — расширять.
- **F5 (открытый чек M3).** ROADMAP-критерий «каждый фикс проверен на
  корпусе Orakul (405 доков)» в этом окружении не выполним: проект
  `orakul` не зарегистрирован в конфиге. Фиксы верифицированы на живом
  корпусе ZAIrgRush (dry-run показывает warning'и неизвестных `type:`
  и счётчик hidden-dirs). Чек остаётся открытым в ROADMAP до
  перерегистрации Orakul.

## 4. Acceptance по голам

| Гол | Критерий | Итог |
|---|---|---|
| G1 — friction #10 | path нормализован, drift in_sync для не-md | ✅ `ee51e7d`, красный прогон записан |
| G2 — friction #8 | `--limit N` + хвост-подсказка, дефолт 50 | ✅ `b98b707` |
| G3 — friction #14/#11 | маппинг diataxis/quadrant + видимый скип hidden | ✅ `523d2ba`, `5cbb57d` |
| Стретч ADO-039 | enforce checkout на всех поверхностях | ✅ `a2f3cfb` |
| Стретч SYM-008 | байт-стабильность off + 3 fail-open теста + check.sh | ✅ `0282835` + `4110cc6` |

DoD спринта: красные прогоны у багов записаны в task-doc 'verification';
все задачи — checkout → complete с `commit_sha`; правки трекаемых md
импортированы в тех же коммитах; drift 130/130; ratchet не вырос;
friction-лог ADO-005 — 0 открытых записей.

## 5. Next step

- **M4** по [ROADMAP.md](../roadmap/ROADMAP.md): назначение определяет
  владелец на планировании; кандидаты — backlog Section F (F3 M1, F5/F7
  M2, F1/F2 настоящего аудита) и Трек B (RFC 16–21).
- **ZAIrgRush:** включение `doc_context = "executor"` на реальной задаче —
  отдельный эксперимент E5-C с замером (метрики по RFC 22 §3.4);
  довести `Cod-Doc-Task:` до настоящего зеркала (поле в tasks.json).
