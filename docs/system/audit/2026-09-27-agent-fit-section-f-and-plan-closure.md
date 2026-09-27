---
type: audit-report
scope: agent-fit-section-f
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-09-27
last_updated: 2026-09-27
related_docs:
  - ../../../proposals/27-agent-fit.md
  - ./2026-09-23-agent-fit-section-b.md
  - ./2026-09-27-agent-fit-sections-a-c-d-e.md
  - ../roadmap/ROADMAP.md
audience: [contributors, agents]
---

# Audit — Plan `agent-fit-2026-09`, Section F closure («Skills and integration») + plan closure

> **Контекст.** [RFC 27](../../../proposals/27-agent-fit.md) F14–F16: две
> разошедшиеся копии скиллов, `sqlite3` как штатный путь в инструкциях
> плагина, scout без MCP, двойное напоминание `doc import`, метрика
> встраивания. Секции A–E закрыты
> ([B](./2026-09-23-agent-fit-section-b.md),
> [A/C/D/E](./2026-09-27-agent-fit-sections-a-c-d-e.md)).

## 1. TL;DR

Секция F закрыта кодом; план — 17/18 `done`, последняя задача AFT-018
(повторный замер) сознательно в `backlog` до двух недель после v1.5.0. По
ходу закрытия всплыла причина, по которой правки плагина три недели не
доходили до сессий: установленный плагин — кэш версии 0.1.3, а в репо стояла
0.1.0, ниже установленной. Исправлено (#130), плагин доставлен 0.2.0.

## 2. Задачи

| Задача | PR | Проверка |
|---|---|---|
| AFT-013 канон `task-flow`/`doc-sync` — плагин | #128 | локальные `.claude/skills/*` удалены; тесты канона и статусов плагина |
| AFT-014 инструкции без `sqlite3`, scout на MCP | #128, #112 | grep пуст; скрипты — с маркером и замером (388 мс CLI vs 5 мс); scout ответил на два вопроса acceptance без `sqlite3`; таблица «вопрос → тул» в глобальном скилле (`~/.claude`, 5a15cc7) |
| AFT-015 один хук напоминания | локально + #130 | репо-хук удалён; хук плагина 0.2.0: tracked `MASTER.md` → одно напоминание, файл вне БД → тишина. `completed_commit` не записан по ошибке оператора — ближайший sha #130 (`c0f5d51`) |
| AFT-016 `scripts/agent_usage_report.py` | #110 | baseline на сохранившихся сессиях: MCP 366, прямой SQL 377 |
| AFT-017 `cod-doc project list --json` | #112 | 8 проектов, ключи `slug/root_path/db_url` |
| AFT-018 повторный замер | — | `backlog` |

## 3. Доставка плагина

`claude plugin` грузит плагин не из репо, а из кэша
`~/.claude/plugins/cache/cod-doc/cod-doc/<version>/`, снятого при установке.
Кэш 0.1.3 (2026-09-06) содержал старые скиллы (`review`/`deferred`), scout
без MCP-тулов и `mcp.json`/`mcp-launch.sh`, давно удалённые из репо. Версия в
`plugins/cod-doc/.claude-plugin/plugin.json` была 0.1.0 — `plugin update` её
не брал. Правило на будущее: правка `plugins/cod-doc/**` поднимает `version`;
доставка — pull основного чекаута → `claude plugin marketplace update cod-doc`
→ `claude plugin update cod-doc@cod-doc` → новая сессия.

## 4. Итог плана

| Метрика замера 2026-09-23 | Стало (v1.5.0) |
|---|---|
| `curator_next` ≈25 КБ | 6918 байт |
| роль на `:8801` — `doc-curator`, checkout запрещён | `coder`, протокол checkout → complete |
| `task_create` падал в 11/52 | префикс из плана, занятый ID → `next_free_id` без SQL |
| пять типовых вопросов → SQL | каждый — один вызов MCP и одна команда CLI |
| инструкции учат `sqlite3` | только хук-скрипты, с обоснованием и гейтом |

Цель RFC 27 §5 «прямых чтений `state.db` меньше, чем MCP-чтений» проверяется
замером на реальных сессиях — AFT-018.

Исполнение: рой ZAIrgRush, $142.50 Opus-части на весь план и цепочку RFC 26
(ADO-199…204, 206, 207), Kimi K3 — на пяти простых задачах. Попутно сделана
большая часть RFC 26; в нём остаются ADO-205, 208, 209, 210.
