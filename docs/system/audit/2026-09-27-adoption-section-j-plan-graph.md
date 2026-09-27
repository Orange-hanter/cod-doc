---
type: audit-report
scope: adoption-section-j
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-27
last_updated: 2026-09-27
related_docs:
  - ../../../proposals/26-plan-graph-editing.md
  - ../capabilities/plan-management.md
  - ../DATA_MODEL.md
  - ../roadmap/ROADMAP.md
audience: [contributors, agents]
---

# Audit — Plan `adoption-2026-08`, Section J closure («Правка графа плана», RFC 26)

> **Контекст.** [RFC 26](../../../proposals/26-plan-graph-editing.md) рождён
> из инцидента 2026-09-22: на `Restate` заголовок и позиция секции плана и
> три ребра зависимости правились SQL-ом по живой базе — штатного
> интерфейса не было ни на одной поверхности. `plan_create` и
> `plan_section_create` писали из MCP-тула через репозиторий, мимо
> `services/`, и сканер паритета такой путь не видел по построению.

## 1. TL;DR

Секция J закрыта: 12/12 `done` (ADO-199…ADO-210), девять PR с #123 по
#136. Планы, секции и рёбра пишутся только через сервисы с ревизией и
activity event; у каждой мутации есть MCP-тул и CLI-команда; два гейта не
дают дыре вернуться. Постоянный надзор за графом — рутина `graph_health`,
заведена на `cod-doc` как `graph_health_daily` (01:00).

## 2. Задачи

| Задача | PR | Что сделано |
|---|---|---|
| ADO-199 валидаторы секции, единый слаг, advisory на HTML | #123 | `validate_section_slug`, `validate_plan_section_position` |
| ADO-200 скоуп проекта у поиска задачи | #124 | `_require_task`, `remove_dependency`, `blocked_by` не находят задачу чужого проекта |
| ADO-201 сервис секций плана | #125 | `plan_service/sections.py`: create/update/move/delete с ревизией и событием; `EntityKind.PLAN_SECTION`; `reason` обязателен в update/move/delete |
| ADO-202 `add_dependency` | #126 | `note` обязателен, upsert, `adopt`, `DependencyCycleError` до записи |
| ADO-203 MCP на сервисе + 4 тула | #127 | `plan_section_update/move/delete`, `task_add_dependency` |
| ADO-204 CLI | #127 | `plan create`, группа `plan section`, `task add-dep` |
| ADO-206 zsh-дополнение | #127 | источники значений новых команд, артефакт регенерирован |
| ADO-207 счётчики профилей | #127 | 6/21/150/154; `test_profile_counts_prose.py` сверяет прозу ~10 файлов |
| ADO-209 паритет над `plan_service` | #133 | `test_plan_mutation_surface_parity.py`; `freeze_projection` вне спеки |
| ADO-208 гейт «презентация не пишет ORM» | #134 | `test_presentation_no_orm_writes.py`, allowlist пуст; `legacy_tasks` на `plan_service` |
| ADO-205 рутина `graph_health` | #135 | см. §3 |
| ADO-210 документация | #136 | plan-management.md, DATA_MODEL.md, AGENTS.md §5.10, CLAUDE.md; `ctx drift` — 0 не `in_sync` |

## 3. `graph_health` на живой БД

Прогоны 2026-09-27 после `cod-doc update` до `5ea79a9`:

| Прогон | Находок | Состояние в `finding` |
|---|---|---|
| 1 | 40 | 40 `open`, `times_seen=1` |
| 2 (повтор) | 40 | 40 `open`, `times_seen=2` — строки не дублируются |
| 3 (после закрытия ADO-205/210) | 36 | 36 `open`, 4 `resolved` — рёбра закрытых задач сняты автоматически |

Все 40 видны в `curator_next` (`card.findings` и очередь `priority`) без
правки куратора. Состав: 18 немых рёбер у незакрытых задач, 22 слага
секций `adoption-2026-08` вне конвенции (легаси до ADO-199). Циклов,
мёртвых и кросс-плановых рёбер, кривых позиций — нет.

**Отступление от буквы acceptance.** «Рёбра без note» не срабатывают, если
зависимая задача `done`/`cancelled`: такое ребро уже ничего не держит.
Без фильтра — 169 находок, из них 129 немых рёбер закрытых задач забили
бы очередь куратора.

## 4. Остаточный долг

- `services/restate_importer.py` создаёт секцию напрямую, минуя
  `create_section`. Под гейт ADO-208 не попадает (он в `services/`); назван
  в докстринге гейта.
- 22 легаси-слага и 18 немых рёбер — находки `graph_health` в очереди, не
  задачи. Мотивация рёбер задним числом не выдумывается
  (`add_dependency(adopt=True)` требует `note`).
- ADO-225 (вне секции): `task_set_blocker` не выводит задачу из
  ready-выборки — соседняя дыра графа, найдена при проверке agent-fit.
- Паритет ловит отсутствие функции на поверхности, но не расхождение
  сигнатур — ограничение сканера, общее для всех спек.
