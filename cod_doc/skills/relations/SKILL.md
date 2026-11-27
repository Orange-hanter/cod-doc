---
name: relations
description: |
  Linking entities: pick a relation type.
  Триггеры: связь, связать, relate, relation, relations, relation_types,
  relates_to, depends_on, see_also, зависит от, дубль, граф связей,
  kind:key, rel add, relation_type_define.
---

# Skill — Связи между сущностями

## Когда подгружается

Нужно связать две сущности (задачу с историей, вопрос с ADR, сценарий с
кодом) или понять, что с чем связано. Прозаическая ссылка в тексте секции
связью не является: она только читается (`mentions`), писать её через
`relate` нельзя.

## Порядок

1. Прочитай `relations(ref)` — связь может уже быть.
2. Подбери тип: `relation_types(from_kind=…, to_kind=…)` — допустимые типы
   пары, обратные имена и число рёбер. Не угадывай имя: опечатка вернёт
   список допустимых и пример вызова.
3. `relate(from_ref, type, to_ref, note=…)`. Повтор того же ребра — no-op;
   обратное имя даёт то же ребро с переставленными концами.
4. Снять — `unrelate` с теми же аргументами.

## Формат `kind:key`

| kind | key | пример |
|---|---|---|
| `task` | ID задачи | `task:ADO-012` |
| `story` | ID истории | `story:US-007` |
| `criterion` | история `#` позиция | `criterion:US-064#2` |
| `document` | doc_key | `document:docs/system/MASTER.md` |
| `section` | doc_key `#` якорь | `section:docs/x.md#registry` |
| `adr` | ID решения | `adr:ADR-007` |
| `question` | ID вопроса | `question:Q-003` |
| `scenario` | ID сценария | `scenario:SCN-001` |
| `plan` / `plan_section` | scope / scope`#`буква | `plan_section:rfc-39#A` |
| `finding`, `module` | uid находки / имя модуля | `module:core` |
| `code` (только цель) | путь `::` символ | `code:cod_doc/services/task_service.py::complete` |
| `url` (только цель) | http(s)-адрес | `url:https://example.com/spec` |

Источником бывает только сущность; `code:` и `url:` — только целью.

## Какой тип

Бери самый узкий тип пары: `implemented_by` (история → задача),
`specified_in` (история/сценарий → документ), `verifies`, `addressed_by`,
`resolved_by`, `decided_in`, `duplicates`, `splits_into`, `depends_on`
(история → история). Направление читай по имени: `A depends_on B` — A
зависит от B; `required_by` — то же с концами наоборот.

`relates_to` (алиас `see_also`) — запасной вариант, когда смысла точнее не
сказать; всегда пиши `note`, почему они связаны. Если для пары допустим
только он, а нужен свой смысл — заведи тип: `relation_type_define(name,
from_kinds, to_kinds, inverse=…)`; `relation_type_deprecate` запрещает новые
рёбра, старые остаются. Встроенные типы переопределить нельзя.

## Типы, у которых есть свой тул

`relate` их пока отвергает и называет тул:

| Что связываешь | Тул |
|---|---|
| задача блокирует задачу (`blocked_by` / `blocks`) | `task_add_dependency` (без циклов, ready-множество) |
| ADR уточняет / зависит от ADR (`amends`, `depends_on`) | `adr_relate` |
| ADR заменяет ADR (`supersedes`) | `adr_supersede` (меняет статус) |

В ленте `relations` они видны вместе с остальными.

## Целостность

Ссылка вперёд (цели ещё нет) допустима только для `about` и `blocks` у
вопроса. `relation_verify` проверяет `code:`/`url:` и достраивает такие
ссылки. Ссылки живут по `uid`: переименование документа или якоря их не
рвёт.
