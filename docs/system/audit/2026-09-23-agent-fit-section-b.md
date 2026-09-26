---
type: audit-report
scope: agent-fit-section-b
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-09-23
last_updated: 2026-09-26
related_docs:
  - ../../../proposals/27-agent-fit.md
  - ../roadmap/ROADMAP.md
audience: [contributors, agents]
---

# Audit — Plan `agent-fit-2026-09`, Section B closure («Role by profile»)

> **Контекст.** [RFC 27](../../../proposals/27-agent-fit.md) F5/F6: на
> профиле `standard` coding-агент первым же вызовом получал запрет того
> протокола, который от него требуют CLAUDE.md и скилл `task-flow`. Секция
> B — две задачи: AFT-004 (роль в `agent_capabilities` по профилю) и
> AFT-005 (карточка `agent_pick` без тела `orchestrator`). Секция шла первой:
> противоречие инструкций дешёвое и бьёт по каждой сессии.

## 1. TL;DR

Обе задачи `done`, код в `main`, доставлен `cod-doc update` в рантайм
`1.4.1.post1.dev20+g4b0f746` и проверен на живых демонах. Противоречие
снято с двух сторон: payload `agent_capabilities` больше не объявляет
coding-агента куратором, а карточка `agent_pick` больше не несёт текст,
запрещающий её исполнять. Профиль `agent` (RFC 25) не изменился.

## 2. Задачи

| Задача | PR | Мерж | Что сделано |
|---|---|---|---|
| AFT-004 | [#101](https://github.com/Orange-hanter/cod-doc/pull/101) | `7b84235` | `agent_capabilities`: `agent` и неизвестный профиль → `doc-curator` с прежним `forbidden`; `minimal`/`standard`/`full` → `coder`, пустой `forbidden`, подсказка checkout → complete. Тесты payload и 4 КБ параметризованы по четырём профилям. CLAUDE.md, AGENTS.md, `docs/mcp-integration.md` уточнены. |
| AFT-005 | [#103](https://github.com/Orange-hanter/cod-doc/pull/103) | `4b0f746` | База карточки `agent_pick` — `task-standard`; `orchestrator` исключён, даже если его вернёт `recommend_for_tool`. `curator_next` по-прежнему начинается с `orchestrator`. |

## 3. Проверка на живых демонах (после `cod-doc update`, 2026-09-23)

| Демон | Профиль | `role` | `forbidden` |
|---|---|---|---|
| `:8801` | `standard` | `coder` | `[]` |
| `:8802` | `agent` | `doc-curator` | `agent_pick`, `task_checkout`, `task_complete` |

Карточку `agent_pick` на живой БД не вызывали намеренно: вызов захватывает
реальную задачу. Поведение закреплено тестом с подменой рекомендаций.

## 4. Как исполнялось

Код обеих задач написал рой ZAIrgRush на стенде `cod-doc-swarm`; все роли,
идущие через Claude (планировщик, исполнитель, ревьюер, подтверждение),
переведены на `claude-opus-5-5`. Оператор перенёс коммиты стенда
cherry-pick'ом на ветки от `main`, переписал сообщения по конвенции и
дописал документацию (CLAUDE.md и AGENTS.md рою запрещены границами стенда).

| Задача роя | Задача cod-doc | Раунды | Стоимость |
|---|---|---|---|
| k7p2 | AFT-004 | 2 итерации, 2 подтверждения | $3.04 (с планированием) |
| r8f3 | AFT-005 | 2 итерации, 2 подтверждения | $1.76 |

Судья и исполнитель — одна модель; второй взгляд давал только
подтверждающий раунд с `confirm_lens = "security"`. Рой отказался
выполнять живую проверку AFT-004 сам: `cod-doc update` из ветки стенда
заменил бы общий рантайм неревьюенным кодом и накатил миграции на 8
проектов. Отказ верный; проверку сделал оператор после мержа.

## 5. Находки по ходу (в RFC 27 не вошли)

| # | Находка | Где |
|---|---|---|
| N1 | Ребро `blocked_by` на уже созданную задачу продукт поставить не может: у `task_update` нет параметра, `add_dependency` нет. Это ADO-202 (RFC 26); AFT-007 пришлось блокировать статусом | `mcp/tools/task_tools.py` |
| N2 | `task_set_blocker` пишет только `blocked_reason`, статус не меняет, а ready-выборка на `blocked_reason` не смотрит — «заблокированная» задача остаётся готовой к работе | `services/task_service.py::set_blocker` |
| N3 | Ответ `task_set_blocker` / `task_update_status` отдаёт пустой `affects_files` при заполненных данных — сериализация без сессии | `services/serializers.py` |
| N4 | `task_create_many` эхом возвращает каждую задачу целиком (~10 КБ на батч из пяти) — тот же перерасход, что в секции A | `mcp/tools/task_tools.py` |
| N5 | `cod-doc import docs` видит 7 незарегистрированных документов репо (2 скилла, 3 аудита doc-curator, `deploy/launchd/README.md`, `docs/system/structure.md`) | корпус |

N1 закрывается RFC 26; N4 внесён в AFT-011 опционально; N2, N3, N5 — кандидаты
в задачи.

## 6. Остаток плана

Секция B — 2/2. Всего по плану 2/16 `done`; в работе AFT-001 (секция A);
AFT-007 `blocked` до ADO-203/207.
