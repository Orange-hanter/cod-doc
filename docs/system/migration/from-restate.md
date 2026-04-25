---
type: migration-plan
scope: restate-to-cod-doc
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../DATA_MODEL.md
  - ../standards/task-plan.md
  - ../standards/frontmatter.md
---

# Migration — Restate (manual) → COD-DOC (managed)

> Пошаговое руководство по переносу текущего состояния `~/Git/Restate` в БД COD-DOC без потери истории и без простоя документации.

## 1. Исходное состояние (на 2026-04-19)

- `Docs/MASTER_DOCUMENTATION.md` — навигация + changelog.
- `Docs/AGENT_START.md` — agent entrypoint.
- `Docs/workspace-map.yaml` — machine-readable карта проектов.
- `Docs/obsidian/` — основной vault (~70 файлов, суммарно сотни килобайт markdown).
- `Docs/obsidian/Modules/<Module>/` — модульные спеки (split и inline).
- `Docs/obsidian/Modules/<Module>/<module>-task-plan.md` — execution plans.
- `Docs/obsidian/Modules/<Module>/tasks/section-*.md` — section files.
- `Docs/obsidian/Modules/<Module>/<module>-completed-tasks.md` — архивы.
- `Docs/obsidian/User Stories.md` — большой файл со всеми историями.
- `Docs/standards/{frontmatter,task-plan,module-spec}.md` — стандарты.
- `tools/task-plan-audit.mjs` + family — валидация.
- `tools/lightrag/` — RAG-индекс.

## 2. Принципы миграции

1. **Без downtime**: Obsidian-пользователи продолжают работать в vault, пока не завершён дни «freeze-and-import».
2. **Frozen projection сначала**: после импорта COD-DOC помечает все markdown как «projection-of-record» и не перезаписывает их до явного `cod-doc export`.
3. **Revisions from git**: история коммитов Restate проигрывается в revision-таблицу (author=`human:<git-author>`, commit_sha=настоящий).
4. **Никакой магии заголовков**: если задача не пройдёт валидацию — она импортируется с `status=draft` и warning, не ломая массовую загрузку.

## 3. Этапы

### Этап 0 — подготовка

```bash
cd ~/Git/cod-doc
cod-doc project new --slug restate --root ~/Git/Restate --title "Restate"
cod-doc project use restate
```

Создаёт запись `project`, резолвит `root_path`.

### Этап 1 — импорт стандартов

```bash
cod-doc import restate-standards
  # читает Docs/standards/*.md, создаёт document rows с type=standard
  # их frontmatter уже почти соответствует; легкая нормализация
```

Цель — standard-доки оказываются первыми, потому что они — словарь валидации для остальных.

### Этап 2 — импорт workspace-map

```bash
cod-doc import workspace-map --file Docs/workspace-map.yaml
  # создаёт module rows + module_code
  # entries становятся прото-модулями
```

Поля `depends_on` мэппятся в `module_dependency`. Поле `api_navigation` — в frontmatter модуля (будет использовано позже).

### Этап 3 — импорт модульных спек

```bash
cod-doc import docs \
  --glob "Docs/obsidian/Modules/**/*.md" \
  --type-infer \
  --fail-on-invalid=warn
```

Инференс типа:

- `<module>-task-plan.md` → `execution-plan`.
- `tasks/section-*.md` → `task-section`.
- `<module>-completed-tasks.md` → `execution-log`.
- Модульные папки → `module-spec` + subdocuments.
- Остальное → `module-subdoc` если лежит в папке модуля, иначе `guide`.

Поля `implemented_in`, `depends_on`, `api_navigation` забираются из frontmatter в соответствующие поля `module` / `document`.

### Этап 4 — импорт execution-plans и tasks

```bash
cod-doc import plans \
  --from-docs \
  --validate strict
```

- Парсит parent-plan + section-файлы.
- Пишет `plan`, `plan_section`, `task`, `dependency`, `affected_file`.
- Для каждой задачи пишет revision `author=human:<git-blame>`, `commit_sha=<hash>`, `at=<commit_date>`.
- Нарушения формата логгируются в `audit_log`, задача импортируется с `status=draft`, помечается `warning`.

### Этап 5 — импорт user stories

```bash
cod-doc import stories --file Docs/obsidian/User\ Stories.md
```

Split одного большого файла по паттерну `## US-<NNN> — <title>`. Acceptance criteria парсятся из `- [ ]` / `- [x]`. Связи `implemented_by` — если в истории явно указаны task-ID.

### Этап 6 — резолвинг ссылок

```bash
cod-doc link reindex --project restate
cod-doc link verify --project restate
```

- Перебирает все section bodies.
- Извлекает wiki-links и markdown-relative-links.
- Резолвит против БД.
- Проставляет `resolved` / `broken_reason`.

Ожидаемо: несколько десятков битых ссылок на уже удалённые legacy-файлы. Они помечаются и попадают в backlog задач `type=docs`.

### Этап 7 — импорт revisions из git

```bash
cod-doc import git-history --project restate --since 2026-01-01
```

- `git log` по файлам документов.
- Каждый коммит на документ → revision.
- `diff` = `git show` для файла.
- `author` из `user.email` → `human:<email-slug>`.
- `commit_sha` настоящий.

Это долгий этап (может занять час-два для полной истории). Можно ограничить периодом (`--since`).

### Этап 8 — валидация

```bash
cod-doc audit --project restate --strict
```

Эквивалент `node tools/task-plan-audit.mjs --strict` + `--linkable` + `--stale` + `--orphans`. Ожидаемый результат: несколько warning, 0 error. Если error — они описываются в отчёте, можно фиксить адресно.

### Этап 9 — frozen projection

```bash
cod-doc projection freeze --project restate
```

Все документы получают `projection_hash = hash(current disk content)`. Ручные правки markdown теперь детектируются: любое расхождение hash → `cod-doc doc diff <key>` покажет что поменялось, `cod-doc doc accept <key>` подтвердит в БД.

### Этап 10 — включение MCP-стека

```bash
cod-doc mcp install --client claude-code
cod-doc mcp install --client vscode-copilot
```

Регистрирует cod-doc MCP-сервер для агентов. С этого момента агенты работают через `task.*`, `doc.*`, `context.*` тулы, а не через терминал.

## 4. Обратный переход (rollback)

Пока frozen projection не разморожен (`cod-doc export` не запущен массово), откат к ручному режиму — это просто:

```bash
cod-doc project pause restate
# COD-DOC не трогает файлы Restate
# автор продолжает в Obsidian как раньше
```

Состояние БД сохраняется. Можно возобновить в любой момент.

## 5. Сосуществование с Restate-тулингом

- `tools/task-plan-audit.mjs` можно оставить работающим — он читает ту же markdown-проекцию.
- `tools/task-plan/mcp-server.mjs` можно отключить в пользу cod-doc MCP, когда все агенты мигрированы.
- `tools/lightrag/` сохраняется на переходный период; embeddings cod-doc включаются рядом, потом Restate-RAG отключается.

## 6. Риски и митигации

| Риск | Митигация |
|------|-----------|
| Нарушение формата в legacy-секциях ломает импорт | Импорт непрерывистый, warnings — не errors; конкретные задачи с draft-status можно дорабатывать после |
| Ссылки на Obsidian aliases теряются | `link.raw` сохраняется, alias парсится отдельно, в проекции восстанавливается |
| Эмбеддинги задолжат память в embedded-профиле | Pg-профиль рекомендован для Restate-scale |
| Автор не привык работать через CLI | TUI-дашборд + `wizard doc new/task new` покрывают 80% сценариев; Obsidian доступен как read-only view |
| Revisions раздуют БД | compact-job (`revision compact --older-than 365d`) + snapshot |

## 7. Контроль успеха миграции

Через 2 недели после этапа 10:

- ✅ `cod-doc audit --strict` по Restate — 0 ошибок.
- ✅ Любой task-create проходит за ≤ 1 сек, без ручной правки markdown.
- ✅ `link verify` — 0 регрессов (только известный backlog битых на момент импорта).
- ✅ Агент выполняет цикл «разобраться → создать план → закрыть задачу» с ≤ 30% сегодняшнего ручного вмешательства.

## 8. После миграции

- Ведётся только COD-DOC; markdown генерируется.
- Раз в неделю — `cod-doc audit --strict` в CI.
- Раз в месяц — `cod-doc export-changelog` для публичного CHANGELOG.
- Obsidian использоваться как read-only pane / viewer, если кому-то удобнее.
