# 04 — Run-id audit trail

> Категория: 🎯 Прямое · Риск: низкий · Зависимости: 03 (или независимо)

## Контекст: как у paperclip

Все мутирующие API-вызовы агентов несут заголовок:
```
X-Paperclip-Run-Id: <run-uuid>
```

Скилл явно требует:
> *"You MUST include `-H 'X-Paperclip-Run-Id: $PAPERCLIP_RUN_ID'` on ALL API requests that modify issues. This links your actions to the current heartbeat run for traceability."*

Что это даёт:
- **Аудит:** «что натворил агент на конкретном прогоне» — один SQL-запрос.
- **Откат:** при ошибочном прогоне можно откатить **все** связанные мутации.
- **Привязка к стоимости/токенам:** run_id связан с cost-event'ами.
- **Дебаг:** при странном изменении видно, в каком контексте оно произошло.

## Текущее состояние cod-doc

- Есть [revision_list / revision_get / revision_revert](cod_doc/mcp/tools/revision_tools.py) — но **по доку**, не по операции.
- Если на одном прогоне агент сделал `doc_create` + `update_master_hashes` + `task_update_status`, нет способа узнать, что эти мутации связаны.
- Нельзя ответить «откати всё, что агент сделал в прогоне Х».

## Предложение

1. **Завести `run_id`** при каждом запуске оркестратора (`uuid7` для сортируемости).
2. **Прокинуть через `ToolExecutor`** в [cod_doc/agent/tools.py](cod_doc/agent/tools.py) — все вызовы мутирующих MCP-тулов получают этот run_id неявно.
3. **Расширить схему ревизий.** В таблицу с ревизиями (или эквивалент в [cod_doc/mcp/tools/_db.py](cod_doc/mcp/tools/_db.py)) добавить колонку `run_id`. Аналогично — для статус-ченджей задач, изменений MASTER.md.
4. **Новая сущность `Run`:**
   ```sql
   CREATE TABLE agent_runs (
     run_id TEXT PRIMARY KEY,
     started_at TIMESTAMP,
     finished_at TIMESTAMP,
     wake_reason TEXT,            -- из WakeContext (см. 03)
     triggering_task_id TEXT,
     triggering_doc_ref TEXT,
     llm_calls INT,
     llm_tokens_in INT,
     llm_tokens_out INT,
     status TEXT,                 -- running | done | failed | cancelled
     summary TEXT                 -- финальный self_check
   );
   ```
5. **MCP-тулы:**
   - `run_list(since?, limit?)` — недавние прогоны.
   - `run_get(run_id)` — все мутации этого прогона: doc revisions, task status changes, master updates.
   - `run_revert(run_id, dry_run=true)` — откат: вызывает `revision_revert` по каждому связанному изменению.
6. **Веб-UI:** страница «Runs» с таймлайном; клик по run → дифф всего, что изменилось.

## Список мутирующих операций, требующих run_id

| Операция                            | Источник                                  |
| ----------------------------------- | ----------------------------------------- |
| `doc_create`, `doc_body` (write)    | [doc_tools.py](cod_doc/mcp/tools/doc_tools.py) |
| `update_master_hashes`              | [legacy_master_tools.py](cod_doc/mcp/tools/legacy_master_tools.py) |
| `task_update_status`, `task_complete` | [task_tools.py](cod_doc/mcp/tools/task_tools.py) |
| `task_create`, `task_set_blocker`   | [task_tools.py](cod_doc/mcp/tools/task_tools.py) |
| `link_sync`                         | [link_tools.py](cod_doc/mcp/tools/link_tools.py) |
| `story_*` мутации                   | [story_tools.py](cod_doc/mcp/tools/story_tools.py) |

## План внедрения

1. **Схема БД + миграция.** Колонка `run_id NULL`, таблица `agent_runs`.
2. **Контекст-проброс.** В [orchestrator.py](cod_doc/agent/orchestrator.py) генерация run_id; через `ToolExecutor` — в каждый MCP-вызов как неявный аргумент (в payload или contextvar).
3. **Запись.** Каждая мутирующая функция пишет run_id вместе с ревизией.
4. **MCP-тулы `run_*`.**
5. **UI.** Минимально — таблица + детальная страница.
6. **Откат.** `run_revert` — в Phase 2; начинаем с read-only аудита.

## Риски

- **Внешние мутации без run_id.** Если человек правит MASTER.md руками — run_id будет NULL. ОК — UI это явно показывает («human edit, no run»).
- **Партиал-успех.** Прогон может упасть на середине; run остаётся в `failed`, его мутации видны и могут быть откачены отдельно.
- **Откат с конфликтами.** Если последующие прогоны затронули те же артефакты, `run_revert` должен показать конфликты, а не «накатить тихо». Аналогично [revision_revert].

## Метрики успеха

- 100% мутирующих MCP-тулов пишут run_id.
- Возможен ответ на запрос «покажи всё, что агент сделал на прогоне X» через UI или CLI.
- В Phase 2: dry-run revert возможен, конфликты явно перечислены.

## Связанные

- 03 (wake-payload) — `WakeContext` рождает run_id; payload пишется в `agent_runs.wake_reason`.
- 09 (activity log) — run-id используется как correlation key в едином таймлайне.
- 12 (approvals) — approval хранит run_id запрашивающей операции.

## Замечания (контекст cod-doc)

- **NULL для legacy и human-edits.** Прошлые ревизии будут с `run_id IS NULL`. UI должен явно показывать это как «pre-runs era» или «human edit, no run», а не оставлять пустую ячейку — иначе у оператора создастся впечатление бага.
- **Брать вместе с [09](09-activity-log.md).** Если activity log делается следом — `run_id` нужен как correlation-key с момента создания таблицы событий, иначе придётся бэкфилить.
- **uuid7 — нужен ли?** Сортируемость даёт нативный ORDER BY без отдельной timestamp-колонки, но добавляет dep (или ручную реализацию). Альтернатива — uuid4 + явный `started_at` индекс.
- **Phase-1 read-only.** `run_revert` отложить. Сначала наблюдаемость («что натворил агент»), потом — обратимость. Иначе риск подсмотреть конфликт с текущим `revision_revert`.
- **Cost/токены.** Если LLM-адаптер ([10](10-adapter-pattern.md)) ещё не сделан, поля `llm_tokens_*` заполняются heuristic'ой текущего OpenAI-клиента. Это OK, но не блокировать на 10.

## Открытые вопросы

- **Q1.** uuid7 vs uuid4 + timestamp-колонка — какой вариант принят?
- **Q2.** Иерархия run'ов — если agent запускает sub-agent или routine fired внутри run'а, это плоский список или parent_run_id?
- **Q3.** TTL для `agent_runs` — хранить вечно (важно для аудита), или агрегировать старше N месяцев?
- **Q4.** `run_revert` с конфликтами — строгий abort с указанием конфликтующих ревизий, partial-revert с маркером, или интерактивный режим в UI?
- **Q5.** Что делать, если run упал и оставил мутации в полу-применённом состоянии (например, doc_create без update_master_hashes)? Авто-rollback по run_id или ручная починка?
- **Q6.** Видимость в CLI — нужен ли `cod-doc run list` и `cod-doc run get`, или достаточно UI + MCP?
