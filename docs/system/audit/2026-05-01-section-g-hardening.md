---
type: audit-report
scope: cod_doc/* (Section G — hardening & DevX completion)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-05-01
last_updated: 2026-05-01
audit_target_revision: post COD-024a + COD-014a + COD-025 + COD-026
related_docs:
  - ../MASTER.md
  - ../roadmap/cod-doc-task-plan.md
  - 2026-04-28-section-c-capabilities.md
---

# Section G (Hardening & DevX) — Completion Report

> Закрывает 5 задач секции: CI gates (COD-024, ранее), strict lint/type debt cleanup (COD-024a), markdown-relative rename cascade (COD-014a), sensitive-data infrastructure (COD-025), TUI smoke tests (COD-026).

## Сводка

| ID | Title | Status | Tests | Notes |
|----|-------|--------|------:|-------|
| COD-024  | CI workflow (pytest+mypy+ruff) | done (prev) | — | mypy теперь blocking |
| COD-024a | clean ruff/mypy debt | done | suite green | OpenAI overload + FastMCP API + TUI BindingType |
| COD-014a | markdown-relative rename cascade | done | +6 | path_map в LinkService, посекционный rewrite |
| COD-025  | Sensitive-data infrastructure | done | +36 | scanner+FM-007+SD-001 audit+SD-002 redaction+clearance helper |
| COD-026  | TUI smoke tests | done | +9 | bug в WizardScreen surfaced и пофикшен |

**Итого тестов добавлено:** +51 (с 351 до 402). **Suite на конец секции:** 402/402 ✅. **CI gates:** ruff/mypy/pytest все blocking ✅.

## Детальные результаты

### COD-024a — strict-mode debt cleared

Стартовое состояние: 407 ruff lint + 59 unformatted + 101 mypy strict в 28 файлах. После двух частичных коммитов осталось 6 ruff + 32 mypy. Закрытие:

- **OpenAI SDK overload** в `agent/orchestrator.py:154,234` — добавлен `# type: ignore[call-overload,misc]`. Альтернативы (cast в TypedDict-типы из `openai.types.chat`) — слишком инвазивны для двух call-site'ов.
- **`tool_calls` union** — фильтр `if tc.type != "function": continue` (новая union-альтернатива `ChatCompletionMessageCustomToolCall` в openai SDK).
- **FastMCP API drift** в `cli/cmd_serve.py` + `mcp/server.py` — `host`/`port`/`stateless_http`/`transport` literal перенесены на `mcp.settings`. SDK обновился, kwargs убрали.
- **TUI `BINDINGS` invariance** — `list[Binding]` → `list[BindingType]` (covariant union из `textual.binding`). Унифицировано во всех 5 экранах + `app.py`.
- **`_StepBar._render` override** в `wizard.py` — переименован в `_refresh_label`, чтобы не конфликтовать с `Static._render` → `Visual`.
- **bare `dict`** (~10 мест) — `dict[str, Any]`; `cast(dict[str, Any], …)` для JSON-загрузки.
- **`collections.abc` TC003** — Callable/Coroutine/AsyncGenerator переехали в `TYPE_CHECKING`.
- **73 файла** auto-форматированы `ruff format`.

CI: `continue-on-error` снят с mypy job'а. ruff job был snim ранее.

### COD-014a — markdown-relative cascade

Дизайн:
- `rename_cascade(..., path_map: dict[str, str] | None)` — dict для forward-compat (bulk rename), single-doc передаёт `{old_path: target_path}`.
- Helpers: `_resolve_md_href` (нормализует через `posixpath.normpath` относительно source-dir), `_make_relative_href`, `_rewrite_markdown_relative_refs`.
- **Расширение поиска affected sections.** `parse()` теряет `../` префиксы при derive `to_doc_key`, поэтому для вложенных папок `to_doc_key` НЕ равен canonical. Когда `path_map` задан — pull all sections с `LinkKind` ∈ {MARKDOWN, SECTION} и фильтруем по фактической смене body. Trade-off: O(N) sections, но O(1) на body-rewrite (большинство body не меняются).
- `link.raw` для markdown-rows тоже перезаписывается — re-resolve остаётся согласован.
- `DocService.rename` строит path_map когда `new_path` отличается, плюс кэскадирует при path-only change (когда doc_key не меняется).

Тесты: markdown-rewrite через path_map, path-only rename, anchor preservation, idempotency, URL/anchor skip, end-to-end DocService.rename.

### COD-025 — sensitive-data (3 из 4 компонентов inline; clearance — preview)

Доставлено:
- **SD-001 SensitivityScanner** — `services/sensitivity_scanner.py`. 5 high-conf паттернов (`aws_access_key`, `github_pat`, `slack_token`, `pem_private_key`, `jwt_token`) + generic high-entropy (cap 25/doc) + PII окно email+phone. Snippets частично замаскированы.
- **SD-001 audit** — `validation.audit_sensitivity` (advisory): high-conf секреты в public/internal → error, в confidential/restricted → warning; PII всегда warning.
- **SD-002 Redaction** — `ProjectionService.render_markdown(audience=...)` + `export_document(audience=...)`. Audience tier ranking; отказ replace body на `> [content redacted: <level> — see DB]`. Audience-specific export НЕ обновляет `projection_hash`.
- **SD-003 helper** — `sensitivity_scanner.clearance_meets(actor, doc)`. Pure helper; единая семантика для будущей ContextService и audit/redaction.
- **FM-007** — warning при отсутствии `sensitivity` для `module-spec`/`architecture`/`standard`.

**Deferred:**
- `ContextService.get(actor, …)` filter — таблица `agent_definition` ещё не существует (Section D). `clearance_meets` — ready API когда service будет.
- Migration `0007_agent_clearance.py` — отложена до Section D вместе с `agent_definition`.
- CLI `cod-doc audit --sensitivity` — в COD-031 (CLI audit gate).

### COD-026 — TUI smoke

9 тестов: 3 boot (wizard/dashboard routing + `q`-quit), 6 screen mounts (WizardScreen, DashboardScreen + 2 dialogs, AgentRunScreen + refresh action). Использует `App.run_test()` + `Pilot.pause()`.

**Баг surfaced:** WizardScreen использовал `id=f"model-{model_id}"` где `model_id` содержит `/` и `.` (например `anthropic/claude-sonnet-4-6`). Textual `BadIdentifier`. Добавлен `_model_widget_id()` helper. Это именно тот класс багов, для которого smoke-тесты и существуют — без них баг бы прошёл в продакшен и сломал wizard у первого же пользователя.

## Раскрытые риски (не бликующие)

1. **OpenAI SDK overload** — `# type: ignore` на двух call-site'ах. Если в будущем openai SDK поменяет messages/tools shape, type-ignore замаскирует регрессию. Mitigation: 5 тестов `test_orchestrator.py` запускаются в каждом CI и проверяют структуру при mock'ах.
2. **chromadb generic variance** — `# type: ignore[arg-type]` на `embedding_function`. Стабы chromadb более жёсткие чем runtime; известный gap, баг open в upstream.
3. **`affected_section_ids` в COD-014a** при path_map — O(N) пробег по всем секциям с MD/SECTION links. На проектах <10K секций — приемлемо. При росте — индекс по `link.raw LIKE '%.md%'` или separate path-target column.

## Метрики

- Файлов изменено: 18 (cod_doc), 4 (tests), 2 (CI/roadmap).
- Тестов добавлено: 51 (5 link-cascade, 36 sensitivity, 9 TUI; +1 mock-fix в test_orchestrator).
- LOC: +740 / -120 (≈ services/sensitivity_scanner 220 LOC + tests 480 LOC + delta link/projection/validation).
- Покрытие: TUI exited the "0 tests" zone; sensitivity flow покрыт e2e (scanner → audit → redaction).

## Закрытие секции

Все 5 задач G-Hardening закрыты. Roadmap counters обновлены: G 5/5 done, TOTAL 20/31. Следующая фокусная зона — Section D (MCP & CLI), которая уже частично сделана COD-030/031/032 (3 из 4 задач), и Section E (Retrieval) — там же будет восстановлен ContextService и закрыта SD-003.
