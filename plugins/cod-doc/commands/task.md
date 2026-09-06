---
description: Взять задачу cod-doc в работу по протоколу (checkout → работа → complete со sha) или закрыть текущую
argument-hint: "next | <TASK-ID> | done <TASK-ID> <sha> | release <TASK-ID>"
---

Аргумент: `$ARGUMENTS`.

Определи слаг проекта сам (см. `/cod-doc:status`), если он не назван явно.
Работай тулами MCP-сервера cod-doc; CLI — запасной путь, ad-hoc SQL по
`state.db` на запись — запрещён (мимо ревизий и activity events).

**Разбор аргумента**

| Аргумент | Что делать |
|---|---|
| пусто или `next` | `task_next_ready` (или `plan_ready` по активному плану) → покажи 3–5 верхних и **спроси**, какую брать; сам не захватывай |
| `<TASK-ID>` | `task_get` → покажи описание и acceptance → `task_checkout(project, task_id, agent="claude-<тема>")` |
| `done <TASK-ID> <sha>` | `task_complete(project, task_id, commit_sha=<sha>, author=...)` |
| `release <TASK-ID>` | `task_release` — отпустить без закрытия |

**Протокол (нарушать нельзя)**

1. Переход `todo → in_progress` — только через `task_checkout`. Прямой
   `task_update_status` на этом переходе падает: checkout атомарен и ставит
   лок, чужой лок — конфликт, а не перетирание.
2. Пока задача в работе — прогресс пиши `task_log_progress`, а не в markdown.
3. Закрытие — `task_complete` со sha реального коммита. Он проверяет
   `blocked_by`, снимает лок, пишет revision + activity event. Закрытие
   правкой .md — не закрытие; статус живёт в БД.
4. Коммит — conventional + ID задачи: `feat(scope): TASK-ID — краткая суть`.
5. Закрыл последнюю задачу секции плана — напомни про audit-отчёт
   (`docs/system/audit/`, если проект следует этой конвенции).

Перед `task_complete` убедись, что гейт проекта зелёный (в cod-doc это
`/gate`), и что acceptance criterion выполнен буквально, а не «по смыслу».
Если acceptance не выполнен — не закрывай, доложи расхождение.
