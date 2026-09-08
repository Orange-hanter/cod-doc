---
type: capability
scope: test-scenarios
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-09-08
last_updated: 2026-09-08
related_docs:
  - ../../../proposals/24-structure-contracts-scenarios.md
  - user-stories-graph.md
  - doc-evolution.md
  - audit-and-ci.md
---

# Capability — Test scenarios

> Записать, **что должно быть верно** и как это проверить: предусловия, шаги,
> ожидаемый результат — как сущность в БД, с проекцией в
> `docs/system/scenarios/`. Доказательство того, что тест это подтверждает,
> сюда не входит по конструкции.

## 1. Что система умеет

Сформулировать сценарий («The system can record a test scenario and project it
next to the capability it belongs to»), привязать его к capability-документу,
связать с задачей, стори или acceptance-критерием и выгрузить группу
сценариев в markdown, который остаётся проекцией, а не источником.

## 2. Кто пользуется и зачем

- **Автор capability** — фиксирует граничные случаи, пока они в голове, а не
  после инцидента.
- **Агент, берущий задачу** — читает сценарии рядом с возможностью, вместо
  того чтобы догадываться о критериях приёмки.
- **Ревьюер** — видит, какие виды сценариев для возможности не описаны вовсе
  (`scenario coverage`).

## 3. Поверхности

| Поверхность | Вход |
|---|---|
| CLI | `cod-doc scenario new｜list｜show｜update｜retire｜steps｜link｜unlink｜export｜coverage` |
| MCP | `scenario_create`, `scenario_get`, `scenario_list`, `scenario_update`, `scenario_retire`, `scenario_set_steps`, `scenario_link`, `scenario_export`, `scenario_coverage` (профили `standard`/`full`) |
| Скилл | `cod_doc/skills/scenario-author/SKILL.md` |
| Проекция | `docs/system/scenarios/<capability>.md` |

## 4. Как устроено

Три таблицы (`scenario`, `scenario_step`, `scenario_link`, миграция
`0031_scenarios`; см. [DATA_MODEL §3.16](../DATA_MODEL.md)) и сервис
`cod_doc/services/scenario_service/`. Вид сценария берётся дословно из
[RFC 24 §9](../../../proposals/24-structure-contracts-scenarios.md):
`happy_path`, `error_path`, `boundary_value`, `invariant`, `integration`.

Экспорт собирает `Document` типа `scenario-set` + `Section` на сценарий и
отдаёт их `projection_service`, поэтому файл ведёт себя как любая другая
проекция: повторный экспорт без изменений ничего не пишет, ручная правка
ловится гвардом и видна в `cod-doc doc drift` как `edited_in_place`.

## 5. Границы и известные пробелы

- **Покрытие тестами не считается здесь.** `scenario coverage` отвечает на
  вопрос «сколько описано», а не «сколько проверено». Вердикты
  `covered | partial | missing | unverifiable` — доказательства producer'а в
  ai-reviewer; они появятся в `scenario_assessment` (STR-002) и соединятся с
  этими строками по `scenario.row_id`. Попытка выставить вердикт как статус
  отклоняется валидатором `SCV-003`.
- **Связи не проверяются на существование.** `to_ref` валидируется по форме:
  сценарий обычно пишется раньше задачи, которая его реализует.
- **`story_coverage` пока не учитывает сценарии.** Связь
  `criterion → SCN-NNN` для этого уже есть, само измерение — отдельная работа.
- **Модули не используются как якорь.** Таблица `module` в реальных проектах
  пуста; якорь — capability-документ.

## 6. Смежные возможности

- [user-stories-graph.md](user-stories-graph.md) — acceptance-критерии, к
  которым сценарий может быть привязан.
- [doc-evolution.md](doc-evolution.md) — правила проекции и дрейфа.
- [audit-and-ci.md](audit-and-ci.md) — совещательные проверки.
