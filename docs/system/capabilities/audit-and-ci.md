---
type: capability
scope: audit-and-ci
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
---

# Capability — Audit & CI

> Сводный каталог проверок `cod-doc audit` и схема интеграции с git/CI.

## 1. Уровни запуска

| Уровень | Команда | Использование |
|---------|---------|---------------|
| **soft** | `cod-doc audit` | Возвращает warning + error, exit 0 |
| **strict** | `cod-doc audit --strict` | Любая ошибка → exit 1 (CI) |
| **staged** | `cod-doc audit --strict --staged` | Только изменённые в git stage файлы (pre-commit) |
| **deep** | `cod-doc audit --deep` | + verify external URLs, code-drift, embeddings freshness |

## 2. Каталог проверок

### 2.1 Frontmatter (см. [standards/frontmatter.md](../standards/frontmatter.md))

| ID | Severity | Описание |
|----|----------|----------|
| FM-001 | error | Обязательное поле отсутствует |
| FM-002 | error | `type` не из enum |
| FM-003 | error | `source_of_truth=false` без `canonical_source` |
| FM-004 | warning | `last_updated` старше 180 дней при `status=active` |
| FM-005 | warning | `last_updated` в будущем |
| FM-006 | warning | Отсутствует `sensitivity` (default internal) |

### 2.2 Task plan (см. [standards/task-plan.md](../standards/task-plan.md))

| ID | Severity | Описание |
|----|----------|----------|
| TP-001 | error | task ID не уникален |
| TP-002 | error | title не соответствует verb-pattern |
| TP-003 | error | type / priority / status вне enum |
| TP-004 | error | Цикл в зависимостях |
| TP-005 | error | `done` task имеет `pending` депенденс |
| TP-006 | error | section letter не соответствует frontmatter |
| TP-007 | warning | feature/bug/refactor без `affected_files` |
| TP-008 | warning | secion-файл > 400 строк |
| TP-009 | warning | inline-plan > 600 строк |
| TP-010 | warning | план ≥ 20 задач без `completed_log` |
| TP-011 | warning | Progress Overview расходится с БД |

### 2.3 Links (см. [standards/document-link.md](../standards/document-link.md))

| ID | Severity | Описание |
|----|----------|----------|
| LK-001 | error | Новая ссылка не резолвится (write-path) |
| LK-002 | warning | Существующая ссылка стала битой |
| LK-003 | warning | Fuzzy-matched ссылка (Lev ≤ 2) |
| LK-004 | warning | Plaintext-упоминание ID без ссылки |
| LK-005 | info  | Внешний URL не отвечает 200 (только `--deep`) |

### 2.4 Sensitivity (см. [standards/sensitive-data.md](../standards/sensitive-data.md))

| ID | Severity | Описание |
|----|----------|----------|
| SD-001 | error | Найден секрет-паттерн (regex + entropy) |
| SD-002 | error | `public` doc ссылается на `confidential` |
| SD-003 | warning | Документ без `sensitivity` |

### 2.5 Drift / freshness

| ID | Severity | Описание |
|----|----------|----------|
| DR-001 | warning | `implemented_in` указывает на несуществующий путь |
| DR-002 | warning | `last_updated` плана < `max(task.last_updated)` |
| DR-003 | warning | `projection_hash` не совпадает с диском |
| DR-004 | warning | `revision` для документа отсутствует > 180 дней при `status=active` |

## 3. Интеграция с git

```bash
cod-doc hooks install
```

Устанавливает:

- `pre-commit`: `cod-doc audit --strict --staged`
- `post-commit`: `cod-doc task sync_from_diff && cod-doc projection freeze`
- `prepare-commit-msg`: добавляет `[<TASK-ID>]` если staged-файлы матчат единственную ready-задачу

Удаление: `cod-doc hooks uninstall`.

## 4. Интеграция с CI

### 4.1 GitHub Actions (пример)

```yaml
name: COD-DOC audit
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install cod-doc
      - run: cod-doc audit --strict --json > audit.json
      - if: failure()
        run: cat audit.json
```

### 4.2 GitLab CI (пример)

```yaml
audit:
  image: python:3.11
  script:
    - pip install cod-doc
    - cod-doc audit --strict --json | tee audit.json
  artifacts:
    when: always
    paths: [audit.json]
```

## 5. JSON-формат отчёта

```json
{
  "project": "restate",
  "started_at": "2026-04-19T12:00:00Z",
  "checks_run": 28,
  "errors": [
    {"check":"TP-004","severity":"error","entity":"AUTH-025",
     "message":"Cycle: AUTH-025 → AUTH-020 → AUTH-025"}
  ],
  "warnings": [...],
  "info": [...],
  "exit_code": 1
}
```

Стабильный для машинной обработки (CI badges, Slack-уведомления).

## 6. Подавление false-positive

Локальное подавление — через frontmatter:

```yaml
audit_suppress:
  - check: FM-004
    reason: "Spec frozen pending compliance review; do not auto-stale"
    until: 2026-06-01
```

Подавление с истекшим `until` снова поднимает warning.
