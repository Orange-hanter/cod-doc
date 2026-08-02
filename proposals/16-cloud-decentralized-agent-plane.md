---
type: proposal
number: 16
title: Cloud decentralized agent plane
category: architecture
risk: high
status: draft
created: 2026-07-29
depends_on: [04, 06, 09, 12]
related_code:
  - cod_doc/mcp/server.py
  - cod_doc/mcp/profiles.py
  - cod_doc/mcp/tools/agent_tools.py
  - cod_doc/services/agent_service.py
  - cod_doc/services/doc_service.py
  - docker-compose.yml
---

# Proposal 16 — Cloud decentralized agent plane

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: 04 run-id,
> 06 checkout, 09 activity, 12 approvals

## Проблема

Cycle-5 сделал агентский UX task-centric (`agent_pick` → complete), но
**документация всё ещё не ведётся «только через сервис» в облаке**:

1. Agent profile не умеет писать документы (нет write-тула; CRUD
   `doc_*` скрыт профилем).
2. `DocService.patch_section` не экспонирован в MCP — gap относительно
   capability doc-evolution.
3. Remote MCP (`streamable-http`) есть, но без auth и с localhost bind.
4. Session factory привязана к per-project sqlite path на локальном FS.
5. Несколько ИИ (Cursor Cloud, Claude, daemon) не могут безопасно
   работать как независимые воркеры против одной облачной SoT.

## Предложение

Ввести профиль деплоя **`cloud`** и capability
[cloud-agent-plane](../docs/system/capabilities/cloud-agent-plane.md):

- Один team-узел COD-DOC в облаке (Postgres SoT).
- Децентрализованные агенты = MCP-клиенты с Bearer → `actor`.
- Расширить agent surface композитом **`agent_apply`** для doc/task
  мутаций под checkout (не тащить 80 CRUD-тулов в agent profile).
- Markdown projection сделать опциональной.

Это **не** multi-tenant SaaS и **не** P2P-федерация (явно out of scope).

## Дизайн `agent_apply`

```text
agent_apply(
  project: str,
  task_id: str,
  agent_id: str,
  ops: [
    {"op": "patch_section", "doc_key": "...", "anchor": "...",
     "new_body": "...", "base_revision_id": "...", "reason": "..."},
    {"op": "create_doc", "doc_key": "...", "doc_type": "module-spec", ...},
    ...
  ]
) -> {results: [...], run_id, revisions: [...]}
```

Инварианты:

- Caller должен держать checkout на `task_id` (иначе `CheckoutError`).
- Одна транзакция на вызов; все ops → один `run_id`.
- Optimistic lock на секциях через `base_revision_id`.
- Activity event на каждый successful op (proposal 09).
- Sensitivity/authz до записи (ARCHITECTURE §12.3).

Agent profile tool list становится:

`agent_capabilities`, `agent_pick`, `agent_get`, **`agent_apply`**,
`agent_report`, `agent_complete`, `agent_release` (7 tools).

## Этапы внедрения

См. [cloud-agent-plane-task-plan.md](../docs/system/roadmap/cloud-agent-plane-task-plan.md)
секции A–D (CAP-001…CAP-033).

Критический путь: Postgres shared session (CAP-005) → MCP patch
(CAP-010) → `agent_apply` (CAP-012) → Bearer (CAP-020) → no-FS mode
(CAP-030).

## Риски

| Риск | Решение |
|------|---------|
| Раздувание agent profile | Только композит `agent_apply`, не `doc_*` |
| Ломаем local DX | Auth optional для stdio/embedded |
| Агенты пишут мимо сервиса в git | Skill + handbook: FS не SoT в cloud |
| Scope → SaaS | Non-goals в capability §7 |

## Acceptance

- Remote агент без доступа к диску проекта проходит полный doc-цикл
  через MCP.
- Два агента на одном cloud node не коррептят один checkout.
- Спека и код identity совпадают (больше не «только ARCHITECTURE»).

## Связь с paperclip

Не отменяет Sections A–F paperclip-плана: heartbeat/wake/run_id/
approvals — топливо для cloud workers. Новые agent-features по
AGENTS.md → мыслить как продолжение task-centric surface (не
раздувание internal CRUD).
