---
type: audit-report
scope: sprint-h1-2026-08-29
status: active
owner: cod-doc core
created: 2026-08-29
audience: [next-session-agent, contributors]
related_docs:
  - ../roadmap/sprint-2026-08-29-hardening-m3-kickoff.md
  - ../roadmap/ROADMAP.md
  - 2026-08-29-contract-audit.md
---

# Audit — Sprint H1 «hardening + M3-кикофф» (2026-08-29 → 2026-09-05, закрыт досрочно 2026-08-29)

## TL;DR

Все три гола закрыты за один день, плюс 2 из 4 стретч-задач. 11 задач done
(10 спринтовых + ADO-013 попутно), 11 коммитов `ae46936`…`7e2b9ec`. Suite
зелёный, drift 126/126 in_sync. Главный стратегический итог — M3-кикофф
(ADO-056): **все RFC 16–21 отбракованы** (ни одна не закрывает спрос M2),
M3 перенацелён решением владельца на остатки friction-лога (#8/#10/#11/#14).

## Deliverables

### G1 — критикалы аудита

- **ADO-035** (`147a1c5`) — loopback-гварда `ensure_loopback_client` переехала
  в `api/deps.py`, `PATCH /api/config` защищён; тест `tests/api/test_api_config_loopback.py`.
- **ADO-036** (`17cd6e0`) — `Document` dataclass + маппинги репозитория
  сохраняют `frontmatter_raw`/`title_in_body`/`content_sha256_head`; round-trip
  тест `tests/infra/test_document_repo_roundtrip.py`.
- **ADO-037** (`f340fc3`) — решение владельца (task-doc 'design'): перевести на
  task_service. Legacy `/api/projects/{name}/tasks` (3 эндпоинта) работают через
  `task_service`/`cod_doc/api/legacy_tasks.py`: Revision + статус-машина +
  activity events, 409 без `state.db`; тесты `tests/api/test_api_tasks_legacy_db.py`.

### G2 — high-харденинг

- **ADO-038** (`c59026e`) — `task_service.complete()` валидирует переход
  статус-машиной; для todo/pending — явная checkout-нога; `cancelled`/`backlog`
  →done отклоняются. Полный срез services+api+integration на момент задачи:
  1199 passed.
- **ADO-055** (`a8baa34`) — двойной `except: pass` в update-path импорта
  заменён: легальный fallback только на `SectionNotFoundError`, прочие падения
  patch/add секции → `logger.warning` + `CoercedField` в `ImportReport.warnings`.
- **ADO-053** (`7a2ef96`) — audience-экспорт пишется в производный путь
  `<stem>.<audience>.md`; канонический файл и `projection_hash` нетронуты,
  drift остаётся in_sync, re-import не тащит redacted body. Смена контракта
  пути задокументирована в docstring `export_document`.
- **ADO-052** (`a5926ae`) — доки профилей MCP синхронизированы с кодом:
  default=`agent`, counts 6/20/107/111 (AGENTS.md §5.9, profiles.py, server.py
  help, mcp-integration.md); smoke-тест `test_profile_counts_match_documented_values`
  ловит будущий дрейф реестра.

### G3 — M3-кикофф

- **ADO-056** (`955eb1b`) — сверка RFC 16–21 по коду (2026-08-29) + сопоставление
  с friction-логом M2 (ADO-005 #8/#10/#11/#14) и findings контракт-аудита →
  решение «отбракованы все», зафиксировано в ROADMAP.md и task-doc 'acceptance';
  пометки в `proposals/README.md`. **ADO-013 закрыта тем же коммитом.**
  Решение владельца: M3 перенацелён на остатки friction-лога M2.

### Стретч

- **ADO-041** (`61873b9`) — `task_to_dict` в `cod_doc/services/serializers.py`;
  services больше не импортирует mcp; AST-гейт `tests/services/test_services_layering.py`.
- **ADO-054** (`7e2b9ec`) — routine `on_finding='create_task'` реализован
  (новая задача на finding, без dedup).
- ADO-039 (enforce checkout — policy-решение) и SYM-008 (Symbiosis фаза 2)
  сознательно перенесены в следующий спринт.

### Книжка

- Сверка SYM-005/006 (на старте спринта, `ae46936`): обе done, gap
  `cod-doc ctx` CLI → ADO-057.

## Findings

- **F1. M3-by-RFC не состоялся как концепция.** Friction-лог M2 указывает на
  импорт-конвейер (#8 dry-run лимит, #10 path у `*.txt`, #11 скрытые каталоги,
  #14 frontmatter-маппинг), а hackathon-RFC 16–20 писались под гипотетических
  vibecoder'ов. Правило «фича по спросу» сработало как задумано — спроса на
  них нет. Урок: новые RFC заводить только с привязкой к friction-записям.
- **F2. Контрактные находки аудита подтвердились реальными багами.** ADO-053
  и ADO-055 — обе с демонстрацией порчи данных на красном прогоне (redacted
  body в БД; молчаливая потеря секций). Пилот ai-reviewer (ADO-034) окупился.
- **F3. Доки дрейфуют быстрее кода.** ADO-052: default-профиль и counts
  устарели в четырёх местах одновременно. Митигация принята: counts
  зафиксированы тестом, в failure-message перечислены все места правки.
- **F4. FTS-savepoint в import_service** (вторичная часть finding ADO-055)
  сознательно оставлен вне скоупа — кандидат в backlog следующего спринта.

## Acceptance (DoD спринта)

- [x] Каждая bug-задача G1/G2 имеет регрессионный тест, падающий на main
      (вывод красных прогонов — в task-doc 'verification' каждой задачи;
      паттерн: `git stash push <фикс>` → pytest → `git stash pop`).
- [x] ADO-037: решение в task-doc 'design' до кода («перевести на
      task_service» — решение владельца).
- [x] ADO-056 + отбраковка RFC 16–21 в `proposals/README.md` → ADO-013 done.
- [x] SYM-005/006: статусы БД ↔ код сверены 2026-08-29; gap → ADO-057.
- [x] Все задачи прошли `task_checkout` → `task_complete` с `commit_sha`;
      висячих in-progress нет.
- [x] Каждая правка трекаемого `.md` закрыта `doc import` в том же коммите;
      финальный drift — 126/126 in_sync.
- [x] `ruff check` + `ruff format --check` + `mypy cod_doc/` + полный
      `pytest tests/ --timeout=120` — зелёные на последнем коммите спринта.
- [x] Ratchet не вырос: `per-file-ignores` не трогали; новых `# noqa` —
      ноль; новые `# type: ignore[no-untyped-def]` только по существующей
      идиоме тест-файлов (fixture/test-сигнатуры, как у соседних тестов в
      тех же файлах); единственный re-export помечен `as task_to_dict` —
      идиома re-export, не noqa.
- [x] Этот audit-отчёт: status active, в БД, со ссылками на все коммиты.

## Next step

Спринт M3 (перенацеленный): задачи по friction-логу M2 — #8 (лимит dry-run),
#10 (path у `*.txt`), #11 (документировать скип скрытых каталогов), #14
(маппинг `diataxis`/`quadrant` → DocumentType); каждый фикс проверяется на
пилотном корпусе Orakul (405 доков). Кандидаты в тот же спринт: ADO-039
(enforce checkout — требует решения владельца о включении), FTS-savepoint
(F4), ADO-057 (`cod-doc ctx` CLI), стретч SYM-008.
