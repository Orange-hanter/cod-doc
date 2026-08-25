---
type: capability
scope: cloud-agent-plane
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-07-29
last_updated: 2026-07-29
related_docs:
  - ../ARCHITECTURE.md
  - ../VISION.md
  - agents-and-skills.md
  - doc-evolution.md
  - project-bootstrap.md
  - ../roadmap/cloud-agent-plane-task-plan.md
  - ../../../proposals/23-cloud-decentralized-agent-plane.md
---

# Capability — Cloud Agent Plane

> COD-DOC как облачный control plane документации: любой ИИ-агент
> (Cursor, Claude Code, встроенный orchestrator, CI) полностью ведёт
> docs/tasks через MCP, без общего локального диска. Агенты —
> децентрализованные воркеры; SoT — БД в облаке.

## 1. Проблема

Сегодня COD-DOC уже умеет task-centric agent surface (cycle-5:
`agent_pick` → work → `agent_complete`) и streamable-http MCP, но
остаётся **локально-связанным**:

| Факт сегодня | Почему мешает «ИИ ведёт docs в облаке» |
|--------------|----------------------------------------|
| MCP по умолчанию `stdio` на машине разработчика | Удалённый Cursor Cloud / другой хост не подключается |
| `streamable-http` слушает `127.0.0.1`, без Bearer auth | Нельзя безопасно выставить в сеть |
| Agent profile = 6 тулов **без записи документов** | Агент закрывает задачу, но не может патчить doc через тот же профиль |
| `DocService.patch_section` есть, MCP `doc_patch_*` нет | Capability [doc-evolution](doc-evolution.md) обещает MCP-patch; surface отсутствует |
| `project.root_path` + markdown projection на FS | Облачный узел требует монтирования чужих путей (`docker-compose` с host paths) |
| Per-project SQLite по умолчанию | Несколько агентов/клиентов не разделяют одну SoT без Postgres |
| Identity/authz описаны в ARCHITECTURE §12, кода нет | Любой, кто достучался до HTTP, имеет полный write |

Цель capability — закрыть эти разрывы без превращения COD-DOC в
multi-tenant SaaS (это по-прежнему non-goal, см. VISION §5 и
proposals/README «Что осталось за скобками»).

## 2. Целевая модель

```text
┌─────────────────────────────────────────────────────────────┐
│                 Cloud COD-DOC node (team)                   │
│  Postgres (SoT) · MCP streamable-http · REST/Web · routines │
│  Bearer → actor · atomic checkout · activity/run_id         │
└───────────────┬─────────────────┬─────────────────┬─────────┘
                │                 │                 │
        ┌───────▼──────┐  ┌───────▼──────┐  ┌───────▼──────┐
        │ Cursor Cloud │  │ Claude Code  │  │ Orchestrator │
        │ agent        │  │ / Copilot    │  │ daemon       │
        │ (MCP client) │  │ (MCP client) │  │ (in-process) │
        └──────────────┘  └──────────────┘  └──────────────┘
                ▲                 ▲                 ▲
                │    нет общего диска между агентами │
                └──────── децентрализованные воркеры ┘
```

**Децентрализация** здесь означает:

1. Много независимых ИИ-клиентов подключаются к одному облачному узлу.
2. Координация — через БД (checkout lock, status machine, activity), не
   через shared filesystem.
3. Markdown на диске — **опциональная проекция** (git sync / object
   storage / local mirror), не обязательное условие работы агента.
4. Один узел = одна команда/набор проектов (не федерация multi-master и
   не SaaS-тенанты). Федерация узлов — out of scope этой capability.

## 3. Контракт для ИИ («полностью через сервис»)

Агент **не** редактирует markdown файлы проекта напрямую как SoT.
Канонический цикл:

```text
1. agent_capabilities()
2. agent_pick(project, agent_id)          # lock + task card + skills
3. agent_apply / agent_get …              # мутации docs/tasks в БД
4. agent_complete | agent_report | agent_release
```

Минимальный **agent** surface после расширения (см. task-plan §B):

| Тул | Роль |
|-----|------|
| `agent_capabilities` | L0 bootstrap |
| `agent_pick` | checkout + context card |
| `agent_get` | deep fetch (`full_doc_body`, …) |
| `agent_apply` | **новое**: атомарные мутации doc/task в рамках checkout |
| `agent_report` | progress / blocker / approval |
| `agent_complete` | done + release |
| `agent_release` | drop lock |

`agent_apply` композирует существующие сервисы (`DocService.patch_section`,
`doc_create`, rename, …) и пишет revision + activity + `run_id` в одной
транзакции. Admin CRUD (`doc_*`, `task_*`) остаётся в `--profile
standard|full`.

### 3.1 Что считается «успехом полного ведения»

ИИ-агент на чистом проекте, подключённый только к remote MCP:

1. Берёт ready-задачу через `agent_pick`.
2. Читает/патчит документы, создаёт связанные docs/tasks.
3. Эскалирует через `agent_report(kind='approval_request')` при FM.
4. Закрывает задачу через `agent_complete`.
5. Не монтирует `root_path` проекта и не пишет в git working tree
   (проекция — отдельный export-job).

Это усиливает VISION §6.4 («цикл только на COD-DOC-контракте») до
облачного remote-клиента.

## 4. Профиль деплоя `cloud`

Расширение [ARCHITECTURE.md §8](../ARCHITECTURE.md):

| Параметр | `embedded` | `server` (сегодня) | `cloud` (цель) |
|----------|------------|--------------------|----------------|
| БД | SQLite | Postgres | Postgres (+ pgvector later) |
| MCP | stdio | stdio / http localhost | streamable-http + TLS + Bearer |
| REST/Web | optional | on | on, за reverse-proxy |
| Auth | implicit OS user | token (спека) | token enforced |
| Projection | обязательный FS mirror | FS volume | optional (export job / git sync) |
| Agents | 1 local | N local clients | N remote workers |
| Identity | `human:<os>` | `actor` + token_hash | то же + project-scoped tokens |

Переключение: `COD_DOC_DB_URL=postgresql://…` +
`COD_DOC_MCP_TRANSPORT=streamable-http` +
`COD_DOC_AUTH=required`.

## 5. Безопасность и identity

Реализует уже описанный контракт ARCHITECTURE §12:

1. Каждый remote-вызов несёт `Authorization: Bearer <token>`.
2. Токен → `actor(project_id, kind, handle)`.
3. Authz: allowed_tools / sensitivity_clearance.
4. Все write → `revision` + `activity` + `run_id` (proposals 04, 09).
5. Checkout TTL + idempotent `agent_pick` защищают от гонок между
   воркерами (proposal 06 / уже в коде).

Web UI по-прежнему может стоять за reverse-proxy; MCP — отдельный
порт/path с тем же token store.

## 6. Проекции и git (не SoT)

- **SoT** = Postgres.
- **Projection** = артефакт:
  - on-demand `doc_export` / batch export в volume или object storage;
  - optional git-sync worker (commit projection → repo), не блокирует
    agent loop.
- Drift detection остаётся для узлов, где projection включена; в
  pure-cloud режиме drift против FS выключен.

## 7. Non-goals

- Multi-tenant SaaS с биллингом и org-chart (proposals/README).
- Peer-to-peer федерация нескольких COD-DOC узлов.
- Замена Plane/Jira / runtime бизнес-логики пользовательского проекта
  (VISION §5).
- Обязательный shared NFS между агентами.

## 8. Acceptance (capability-level)

- [ ] Remote MCP client (не на том же хосте) проходит цикл
      pick → apply(doc patch) → complete против Postgres.
- [ ] Два параллельных агента с разными `agent_id` не получают один и
      тот же checkout; второй видит idempotent replay или другую задачу.
- [ ] Без валидного Bearer write отклоняется (`AuthDeniedError`).
- [ ] Агент не требует `root_path` на диске сервера для мутации body.
- [ ] Activity/run_id связывают все мутации одного heartbeat.
- [ ] Документирован рецепт подключения Cursor Cloud / Claude к
      `https://<host>/mcp`.

## 9. Связанные артефакты

- Kickoff: [roadmap/cloud-agent-plane-kickoff-2026-07-29.md](../roadmap/cloud-agent-plane-kickoff-2026-07-29.md)
- Plan: [roadmap/cloud-agent-plane-task-plan.md](../roadmap/cloud-agent-plane-task-plan.md)
- RFC: [proposals/23-cloud-decentralized-agent-plane.md](../../../proposals/23-cloud-decentralized-agent-plane.md)
