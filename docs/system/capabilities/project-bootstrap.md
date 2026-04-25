---
type: capability
scope: project-bootstrap
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
  - ../migration/from-restate.md
---

# Capability — Project Bootstrap

> Что происходит при `cod-doc project new`: записи в БД, скелетные документы, агенты, конфиг.

## 1. Команда

```bash
cod-doc project new \
  --slug <slug> \
  --root <path> \
  --title <title> \
  [--profile embedded|server] \
  [--from-template restate|generic|empty]
```

## 2. Что создаётся

### 2.1 Запись `project`
- `slug`, `title`, `root_path`, `created`, дефолтный `config_json`.

### 2.2 Дефолтные документы

| doc_key | type | sensitivity |
|---------|------|-------------|
| `master` | `vision` | internal |
| `architecture` | `architecture` | internal |
| `documentation-graph` | `guide` (auto-generated) | internal |
| `navigation` | `guide` | internal |
| `standards/frontmatter` | `standard` (clone from system) | internal |
| `standards/task-plan` | `standard` (clone from system) | internal |
| `standards/document-link` | `standard` (clone from system) | internal |

Шаблоны живут в `cod_doc/templates/projects/<template>/`. Это аналог Restate-стека «из коробки».

### 2.3 Дефолтные агенты

Импортируются из системного каталога ([agents-and-skills.md §2](agents-and-skills.md)): `task-steward`, `docs-reviewer`, `link-verifier`, `migrator`, `release-manager`.

### 2.4 MCP-регистрация

CLI спрашивает: `Register MCP for Claude Code? [Y/n]`. Если да — пишет конфиг в `~/.claude/mcp.json` или `.mcp.json` проекта.

### 2.5 Hooks

Опционально (`--with-hooks`):
- git pre-commit: `cod-doc audit --strict --staged`
- git post-commit: `cod-doc task sync_from_diff`

## 3. Профиль

| Параметр | embedded | server |
|----------|----------|--------|
| БД | `.cod-doc/state.db` (SQLite) | `COD_DOC_DB_URL` (Postgres) |
| REST API | off | on |
| Embeddings | sqlite-vss | pgvector |
| Auth | local user | token-based |

## 4. Идемпотентность

Повторный `project new --slug <existing>` — error. Для пересоздания: `cod-doc project drop <slug> --confirm`. Drop не удаляет markdown-проекцию (только запись в БД); `--purge` удаляет всё.

## 5. Импорт существующего проекта

`cod-doc project new --from-existing <root>`:
- сканирует `<root>` на markdown с frontmatter;
- не создаёт дефолтные документы (использует существующие);
- запускает аналог [migration/from-restate.md](../migration/from-restate.md) этапов 3-7.

## 6. Audit после bootstrap

Финальный шаг команды — `cod-doc audit --strict`. Проект не считается готовым, пока не вернёт 0 errors.
