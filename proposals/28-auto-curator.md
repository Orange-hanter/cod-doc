# 28 — Автоматический куратор: находит и чинит документацию сам

> Категория: 🟡 Адаптация · Риск: средний · Зависимости: RFC 25 (роль
> куратора, `curator_next`), proposal 07 (routines), proposal 12 (approvals),
> proposal 04 (run-id), ADR-014 (куратор, не исполнитель)

## 1. Контекст

RFC 25 и ADR-014 (принят 2026-09-27) сделали дефолтного агента куратором
документации. Но куратор — это набор тулов и очередь `curator_next`, а не
исполнитель: он работает, только пока его ведёт внешний клиент (Claude Code
на `:8802`, человек в CLI). Без клиента работают одни cron-рутины, и они
только **находят**: пишут находки и activity-события, но ничего не чинят.

Владелец 2026-09-27: стандарт куратора должен стать автоматическим
инструментом — находить **и** дорабатывать документацию без человека за
рулём. Решения владельца, принятые до RFC:

- **Граница автономии.** Детерминированные исправления куратор применяет сам
  (с ревизией и откатом); всё, где нужно суждение или LLM, — только через
  approval с diff.
- **Частота.** Один прогон в сутки, ночью.

RFC 25 §5 держал в non-goals «автономный daemon как единственный runtime
куратора» и «новую таблицу под doc jobs». Этот RFC не нарушает ни то, ни
другое: прогон — это рутина на уже работающем `routine tick`, интерактивный
куратор остаётся, а предложения живут в существующей таблице `approval`.

## 2. Текущее состояние (проверено по коду 2026-09-27)

| # | Факт | Где |
|---|---|---|
| S1 | `curator_next` read-only, очередь из 8 видов с фиксированным рангом: drift `missing` → `edited_in_place` → `LINK-BROKEN` → hash `BROKEN` → hash `STALE` → `stale_export` → `unplaced` → finding | `services/curator_service.py:54-73, 341-368` |
| S2 | Детерминированно чинятся: `missing`/`stale_export` (`doc export`), `edited_in_place` (`doc import`), hash `STALE` (`hash update`), `unplaced` с совпавшим правилом (`classify --apply`). Суждения требуют: `LINK-BROKEN`, hash `BROKEN`, `unplaced` без правила, findings | `curator_service.py:244-338` |
| S3 | **Дрейф не видит конфликта.** Если изменились и БД, и файл, документ получает `stale_export` — первая ветка сравнивает только `projection_hash` с БД. Автоматический `export` такого документа затёр бы ручную правку в файле | `services/projection_service/drift.py:104-111`; `_types.py:13-17` |
| S4 | `export_document` пишет файл без `author`, без revision и без activity-события; `update_hashes` — тоже только файл. Обе записи нарушают ADO-040 («write-path оставляет след») и не ловятся `test_activity_write_path.py`, потому что пишут не в БД | `projection_service/export.py:200`; `core/hash_calc.py:128` |
| S5 | `on_finding` рутины — `create_task` / `update_existing_task` / `comment_only`; исправлять нечем. `trigger=event` валиден, но его никто не диспатчит — `tick` берёт только `cron` | `services/routine_service.py:53-54, 746-765` |
| S6 | Проверка рутины — `Callable[..., dict]` с `session`, `project_id`; мутирующие проверки уже есть (`approval_stale`, `doc_node_health`, `graph_health`) | `routine_service.py:371, 406, 436-448` |
| S7 | `approval`: типы `plan_review/risky_action/fm_escalation/budget/manual`, TTL 48 ч, `payload_json`. `resolve` отдаёт только `wake_hint` — **никто не исполняет действие после одобрения**. Поверхность — только MCP `approval_resolve`; в CLI и вебе её нет | `services/approval_service.py:42, 154, 296-345`; `mcp/tools/approval_tools.py:185` |
| S8 | Прецедент «LLM предлагает, человек применяет»: `comment_service.apply_open_with_ai` генерирует новое тело секции без записи, веб показывает превью, запись идёт через `patch_section` | `services/comment_service.py:272`; `api/web/pages/comments.py:189` |
| S9 | Прямые LLM-вызовы вне оркестратора — sync `openai.OpenAI` через `ai_text._call_lite_raw`; учёта токенов и бюджета нет нигде, колонки `agent_run.tokens_in/out` никто не инкрементирует | `services/ai_text.py:342`; `infra/models/revisions.py:63-86` |
| S10 | Legacy `Orchestrator` читает `tasks.yaml`, а не БД; его тулы пишут файлы мимо сервисов (`write_file`). Переиспользуемы только `run_context`, консоль прогонов и адаптеры | `agent/orchestrator.py:219-244`; `agent/tools.py:99`; `core/project.py:176-230` |
| S11 | `actor_kind_for_author("agent:curator")` → `agent` | `domain/entities.py:365` |
| S12 | Тик рутин на машине — пользовательский crontab раз в 15 минут, бинарь из editable `.venv` рабочего чекаута, а не из пиннованного `~/.cod-doc/runtime` | `crontab -l`; `cli/routine.py:108` |

Вывод: находить куратор уже умеет (S1, S5), чинить механически — тоже, но
только руками (S2). Не хватает трёх вещей: безопасного детектора конфликта
(S3), следа у файловых записей (S4) и конвейера «предложение → одобрение →
применение» (S7).

## 3. Предложение

### 3.1. Три уровня действия

Каждый вид пункта очереди получает фиксированный уровень. Уровень — свойство
вида, а не решение модели в рантайме.

| Вид пункта | Уровень | Действие прогона |
|---|---|---|
| drift `edited_in_place` | **auto** | `import_or_update_markdown` (без `--replace`: сироты не удаляются) |
| drift `missing` | **auto** | `export_document` |
| drift `stale_export` | **auto** | `export_document` |
| drift `conflict` (новый, §3.3) | report | в отчёт; не трогать ни файл, ни БД |
| hash `STALE` | **auto** | `update_hashes` |
| hash `BROKEN` | report | в отчёт: восстановить файл или убрать строку реестра решает человек |
| `unplaced`, правило совпало | **auto** | `doc_tree_service.assign` |
| `unplaced`, правило не совпало | **propose** | LLM выбирает раздел → approval |
| `LINK-BROKEN` | **propose** | LLM подбирает новую цель ссылки → approval |
| finding (любой) | report | в отчёт; продвижение в задачу — `finding_promote` человеком |

`auto` применяется сразу, `propose` становится approval с полным diff,
`report` попадает только в отчёт прогона. Генерация содержимого разделов
(NODE-THIN, пустые `intent`) в этот RFC не входит — §5.

### 3.2. Прогон `curator_sweep`

Одна функция сервиса, одна рутина, две поверхности.

```python
# cod_doc/services/curator_sweep_service.py
def sweep(
    session: Session,
    project_id: int,
    *,
    root_path: Path,
    apply: bool = True,          # False — сухой прогон: план действий без записи
    propose: bool = True,        # False — без LLM, только auto и report
    max_auto: int = 50,          # потолок auto-правок за прогон
    max_proposals: int = 10,     # потолок новых approval за прогон
    author: str = "agent:curator",
) -> SweepReport: ...

@dataclass(slots=True)
class SweepReport:
    run_id: str
    applied: list[SweepAction]      # auto, с revision_id каждой записи
    proposed: list[SweepAction]     # propose, с approval_id
    reported: list[SweepAction]     # report + всё, что упёрлось в потолок
    skipped: list[SweepAction]      # guard отказал (§3.3), с причиной
    llm_calls: int
    tokens_in: int
    tokens_out: int
```

- **Вход** — тот же `curator_service.next(limit=None)`, без отдельного
  детектора: прогон видит ровно то, что видит интерактивный куратор.
- **Рутина** — новая проверка `curator_sweep` в `CHECK_CATALOG`, по
  умолчанию `cron="0 2 * * *"`, после `doc_drift_daily` (00:00),
  `link_integrity_daily` (00:30) и `graph_health_daily` (01:00).
  Отдельного демона нет: рутину поднимает существующий `routine tick`.
- **CLI** — `cod-doc ctx sweep -p <slug> [--dry-run] [--no-llm] [--json]`.
- **MCP** — `curator_sweep(project, dry_run=True, propose=False)` на
  `standard`/`full`. В профиль `agent` не входит: он остаётся 6 тулов, а
  интерактивный куратор чинит пункты своими руками. Счётчики: `standard`
  150 → 151, `full` 154 → 155.
- **Порядок внутри прогона** — по рангу очереди; `auto` одного документа
  выполняется до `propose` по нему же, чтобы LLM видел свежее состояние.

### 3.3. Инварианты безопасности

1. **Конфликт — отдельный статус.** `DriftStatus.CONFLICT`: БД изменилась
   после экспорта (`projection_hash != db_hash`) **и** файл изменился после
   экспорта (`file_hash != projection_hash`, и не равен
   `content_sha256_head`). Сейчас это `stale_export` (S3). Новый статус
   попадает в `curator_next` с рангом выше `edited_in_place` и уровнем
   `report`. Без этого пункта автоматика не включается.
2. **Каждая запись оставляет след.** `export_document` и `update_hashes`
   принимают `author` и эмитят activity-событие (`doc.exported`,
   `master.hashes_updated`) через `emit_for_write` (S4). Revision не нужна —
   содержимое БД не меняется, — но событие обязательно.
3. **Файлы трогаются только чистыми.** Перед `export`/`update_hashes` —
   `git status --porcelain -- <path>`: локально изменённый или
   неотслеживаемый файл — `skipped` с причиной `dirty_worktree`. Прогон
   **никогда не коммитит**: изменения файлов остаются в рабочем дереве, их
   список — в отчёте.
4. **Оптимистичная блокировка предложений.** Payload approval несёт
   `base_revision_id`; применение сверяет его с головой
   (`expected_parent_revision_id` у `patch_section`). Разошлось — approval
   переводится в `expired` с причиной `stale_base`, следующий прогон
   предложит заново.
5. **Идемпотентность.** Предложение имеет отпечаток
   `sha256(kind, ref, proposed_target)`; pending approval с тем же
   отпечатком не дублируется. Отклонённое (`denied`) предложение с тем же
   отпечатком не повторяется 30 дней.
6. **Выключатель.** `ProjectEntry.curator_auto: bool = False` в
   `~/.cod-doc/config.yaml`. Выключен — рутина работает как `apply=False`
   (сухой отчёт). Включается проектом явно; первый — `cod-doc`.
7. **Автор** — `agent:curator` (→ `actor_kind=agent`, S11). По нему
   фильтруется лента ревизий и откат прогона целиком.

### 3.4. Предложения: approval, который исполняется

- Новый тип approval **`doc_patch`** (`VALID_TYPES` + миграция не нужна —
  тип хранится строкой).
- Payload:

  ```json
  {"op": "link_retarget" | "doc_set_node",
   "args": {...},                    // ровно аргументы сервисной функции
   "base_revision_id": 123,
   "fingerprint": "sha256…",
   "diff": "--- before\n+++ after\n…",  // что увидит человек
   "rationale": "почему эта цель",       // ответ LLM, одна-две строки
   "run_id": "…"}
  ```

- **Applier** — `approval_service.resolve(..., decision="approved")` для
  типа `doc_patch` вызывает реестр `CURATOR_OPS[op](session, **args,
  author=<кто одобрил>)` в той же транзакции. Реестр закрытый: только
  перечисленные операции, только сервисные функции с revision и событием.
  Произвольный текст модели не исполняется.
- **Поверхности одобрения** (сейчас только MCP, S7):
  CLI `cod-doc approval list|show|approve|deny -p <slug>` и страница
  «Предложения куратора» в вебе с diff. Паритет CLI ↔ MCP — через
  расширение `_surface_parity` на `approval_service`.
- **LLM-предлагатели MVP:**
  - `link_retarget` — кандидаты для битой ссылки из `ctx_search` по тексту
    ссылки и её окружению (топ-5), LLM выбирает одного или отвечает «нет
    подходящего» (→ `report`). Модель не придумывает цель, а выбирает из
    найденного.
  - `doc_set_node` — для неразложенного документа LLM выбирает раздел из
    списка `doc_node` проекта по заголовку, типу и первым 2 КБ тела.
  - Оба зовут `ai_text._call_lite_raw`; вызовы и токены пишутся в
    `SweepReport` и в `agent_run` (S9 — колонки наконец начинают
    заполняться).

### 3.5. Отчёт прогона

- Прогон открывает `agent_run` (`wake_reason="curator_sweep"`) через
  `run_context`; `applied/proposed/reported/skipped` — `summary`, шаги — в
  `activity_event`. Консоль прогонов в вебе (`pages/run.py`) начинает
  показывать живые данные вместо истории legacy-оркестратора.
- `curator_next` получает строку `pending_proposals: N` в `meta.counts` и
  пункт очереди «разобрать предложения куратора», когда N > 0.

## 4. Миграция / обратная совместимость

- `DriftStatus.CONFLICT` меняет классификацию документов, которые сейчас
  числятся `stale_export` при изменённом файле. Потребители `ctx_drift`
  (drift-гейт PR, RFC 22) увидят новое значение статуса; drift-гейт должен
  считать `conflict` блокирующим, как `edited_in_place`.
- `export_document(author=...)` — новый обязательный keyword-аргумент;
  все три вызова (CLI `doc/cmd_export.py:68`, MCP `doc_tools.py:566`,
  `scenario_service/export.py:153`) правятся одним коммитом.
- Схема БД не меняется: `approval.type` — строка, `agent_run` уже есть.
- Crontab (S12) остаётся, но тик переводится на пиннованный рантайм
  (`~/.cod-doc/runtime/bin/cod-doc routine tick`): ночной прогон, который
  пишет в документы, не должен зависеть от состояния рабочего чекаута.
- Legacy `cod-doc agent run` и `agent_enabled` не трогаются; в HANDBOOK §9 —
  ссылка на `curator_sweep` как на замену автономного режима.

## 5. Риски и что не делаем

**Риски**

- **Массовая правка при первом включении.** На `cod-doc` сейчас 171
  неразложенный документ. Митигация: `max_auto`, первый прогон — `--dry-run`
  вручную, `curator_auto` включается после просмотра отчёта.
- **Затёртая ручная правка.** Митигация — инварианты 1 и 3; тест: файл и БД
  изменены одновременно → `conflict`, прогон файл не трогает.
- **Шум предложений.** Митигация — потолок `max_proposals`, отпечатки,
  30-дневная память об отказе.
- **Грязное рабочее дерево по утрам.** Прогон оставляет незакоммиченные
  файлы. Это осознанно: коммит — решение человека. Если мешает — выключить
  файловую часть флагом, оставив DB-часть.

**Non-goals**

- Генерация и переписывание текста документов (NODE-THIN, `intent`,
  устаревшие описания) — следующий RFC, после того как конвейер approval
  обкатан на ссылках и раскладке.
- Правка кода, git-коммиты, пуш, PR.
- Новый демон или переписывание legacy `Orchestrator`.
- Автоматическое закрытие или продвижение findings.
- Событийный триггер (`trigger=event`) — прогон по cron достаточен.
- Бюджетные hard-stop на токены — только учёт и потолки на число действий.

## 6. Оценка

План `auto-curator-2026-09`, префикс задач `ACU-`, 5 секций, ~15 задач,
2–3 недели.

| Секция | Суть | Задач |
|---|---|---|
| A. Безопасность | `DriftStatus.CONFLICT` + ранг в `curator_next` + drift-гейт; `author` и событие у `export_document`/`update_hashes`; git-clean guard | 3 |
| B. Детерминированный прогон | `curator_sweep_service.sweep` (auto + report), рутина, CLI `ctx sweep`, MCP `curator_sweep`, `agent_run`-отчёт, выключатель `curator_auto` | 4 |
| C. Конвейер предложений | тип `doc_patch`, `CURATOR_OPS` + applier в `resolve`, CLI `approval`, веб-страница предложений, паритет | 4 |
| D. LLM-предлагатели | `link_retarget`, `doc_set_node`, учёт токенов в `agent_run` | 2 |
| E. Включение | тик на рантайм, `curator_auto` для `cod-doc`, документация (HANDBOOK §9, mcp-integration, счётчики), аудит закрытия | 2 |

Порядок: A → B → E(частично: включение auto) → C → D → E. Секция B без A
не мержится: A — предусловие автономной записи.

**Критерий успеха**

- Ночной прогон на `cod-doc` чинит `edited_in_place`, `stale_export`,
  `STALE`-хэши и раскладку по правилам без участия человека; каждое
  действие откатывается `revision revert` или видно в `activity_event`.
- Документ, изменённый и в БД, и в файле, прогон не трогает (тест
  воспроизводит сценарий S3 до фикса).
- Одобрение предложения из CLI применяет правку; отказ не порождает то же
  предложение на следующую ночь.
- `curator_next` утром показывает только то, что требует суждения.
