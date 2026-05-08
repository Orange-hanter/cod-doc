---
status: implemented
type: ux-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · legacy-tasks · migration
related:
  - cod_doc/api/web/pages/tasks.py
  - cod_doc/templates/web/project/tasks_list.html
  - cod_doc/templates/web/project/tasks_legacy_list.html
  - cod_doc/cli/cmd_import.py
  - cod_doc/services/restate_importer.py
  - cod_doc/core/project.py
  - cod_doc/mcp/tools/legacy_project_tools.py
---

# Proposal 14 · Миграция legacy-задач из YAML в БД через UI

> 🎯 Цель: закрыть «двухслойное» хранилище задач. Сегодня одни и те же
> проекты держат задачи и в `.cod-doc/tasks.yaml` (старый YAML), и в
> БД-таблице `task` (COD-032). Импорт уже есть, но только в CLI — UI
> только показывает «Legacy YAML tasks (N), not yet migrated» и не
> предлагает ничего нажать. Нужна одна кнопка «Импортировать в БД» +
> правила, исключающие повторное появление YAML-задач.

## 1. Что не так сейчас

### 1.1. Симптомы

[`tasks_list.html:10-15`](cod_doc/templates/web/project/tasks_list.html#L10-L15) и
[`tasks_legacy_list.html:15-18`](cod_doc/templates/web/project/tasks_legacy_list.html#L15-L18):

| Симптом | Причина |
| --- | --- |
| На `/p/{slug}/tasks` стоит «В этом проекте пока нет задач», а сверху висит ссылка «Legacy YAML tasks (34)» | Страница читает только DB; legacy — параллельный мир в `tasks.yaml` |
| Чтобы перенести 34 задачи в БД, нужно открыть терминал и запустить `cod-doc import legacy-tasks <slug>` | UI вообще не знает про эту команду — нет ни эндпоинта, ни кнопки |
| Banner «not yet migrated» висит даже после того, как пользователь уже всё проверил и считает, что мигрировать не надо | Нет состояния «принято решение не мигрировать»; нет dry-run, нет diff |
| `add_task` / `update_task` через MCP всё ещё пишут в YAML ([`legacy_project_tools.py:122-185`](cod_doc/mcp/tools/legacy_project_tools.py#L122-L185)) | Старые тулзы зарегистрированы рядом с новыми (`task_tools.py`) и не помечены deprecated → агенты иногда выбирают legacy |
| Legacy-страница показывает только id/title/status/priority/updated, без description/result | Read-only превью без полного содержимого — пользователь не видит, что именно мигрируется |

### 1.2. Почему получается

Хронология (по `git log` и архитектурным меткам):

1. **До COD-032** — единственное хранилище задач было `tasks.yaml`.
   Класс [`Project`](cod_doc/core/project.py#L120-L207) и legacy MCP-тулзы
   (`add_task`, `update_task`, `next_pending_task`) работают с ним
   напрямую. Все 37 текущих записей в `.cod-doc/tasks.yaml` — наследие
   этого периода.
2. **COD-032+** — заведена реляционная схема (`task` + `plan` +
   `plan_section` + ревизии). Параллельно появились `task_tools.py`,
   web-UI `/tasks`, агентский конвейер (`agent/orchestrator.py`).
   Старый код **не удалили** — он продолжил обслуживать существующие
   потоки, чтобы не ломать привычные сценарии.
3. **COD-051** — добавили `cod-doc import legacy-tasks` для bulk-переноса
   ([`cmd_import.py:84-117`](cod_doc/cli/cmd_import.py#L84-L117) +
   [`restate_importer.py:243-337`](cod_doc/services/restate_importer.py#L243-L337)).
   Это сняло срочность миграции, но создало стабильное «болото»: импорт
   есть → нет повода удалять YAML, YAML есть → legacy-тулзы продолжают
   писать туда же. UI отразил болото в виде отдельной вкладки.
4. **Сейчас** — в репо `cod-doc` сам по себе: 37 задач в YAML, 0 в БД
   (для проекта GatewayDemo на скриншоте). И так почти у всех проектов,
   которые завели до COD-051.

Корневая причина одна: **импорт реализован, но не предъявлен пользователю
как «нормальное» действие**. Пока кнопка не нажимается из браузера, она
не нажимается вовсе.

## 2. Предлагаемая модель

### 2.1. Один цикл: Preview → Import → Freeze

На странице `/p/{slug}/tasks/legacy` добавить блок действий **сверху**
таблицы:

```
[ 🔍 Preview import ]   [ 📥 Import all (N) ]   [ ❄ Mark as archived ]
```

- **Preview import** (dry-run) — открывает diff-модал: «будет создано
  N задач в plan `imported-legacy`, K из них уже похожи на DB-задачи
  (по title hash) → пометим как duplicates». Никаких изменений в БД.
- **Import all** — без флажков, без выбора подмножества. Импортирует
  всё содержимое YAML одной транзакцией, показывает summary
  (`imported / skipped / errors`). После успеха — кнопка превращается
  в «✓ Imported on YYYY-MM-DD HH:MM».
- **Mark as archived** — для случая «не надо мигрировать, оставьте
  как есть». Просто переименовывает `tasks.yaml` →
  `tasks.archived.yaml` и убирает baner с `/tasks`.

Никаких частичных импортов, никакого выбора чекбоксами — массовый перенос
данных, не повседневный workflow. Если что-то пошло не так — `git revert`
БД-миграцию (ревизии уже это умеют через `restate-import:*` reason).

### 2.2. HTTP-эндпоинты

Добавить в [`api/web/pages/tasks.py`](cod_doc/api/web/pages/tasks.py):

| Method | Path | Назначение |
| --- | --- | --- |
| `POST` | `/p/{slug}/tasks/legacy/import?dry_run=1` | Возвращает HTML-фрагмент с diff (план импорта). Без записи. |
| `POST` | `/p/{slug}/tasks/legacy/import` | Реальный импорт. Возвращает HTML-фрагмент с summary + ссылкой «Open imported plan». |
| `POST` | `/p/{slug}/tasks/legacy/archive` | Переименовывает yaml. Идемпотентно. |

Все три — htmx-эндпоинты (HTML, не JSON). Защита: `X-CSRF-Token` или
`SameSite` cookie — проверить, что используется на других POST-формах
(например, на `/p/{slug}/daemon/start`).

Реализация ровно поверх уже существующего
[`restate_importer.import_legacy_tasks`](cod_doc/services/restate_importer.py#L243-L337):
для dry-run — `session.rollback()` и сериализация `summary`; для
архива — `yaml_path.rename(yaml_path.with_suffix(".archived.yaml"))`.

### 2.3. Состояние «migrated» как признак, а не как факт

После успешного импорта:

- На странице `/p/{slug}/tasks` baner с «Legacy YAML tasks (34) — not
  yet migrated» меняется на «📦 Legacy YAML tasks (34) — imported
  YYYY-MM-DD into plan `imported-legacy`». Это нужно прочитать из
  БД (плана с scope `imported-legacy` и кол-ва задач в нём), а не
  хранить отдельный флаг.
- На странице `/p/{slug}/tasks/legacy` сверху появляется блок
  «✓ Уже импортировано N → M; повторный импорт создаст дубли». Кнопка
  «Import all» становится секондари + требует подтверждения.

### 2.4. Закрыть write-путь в YAML

Это критично — иначе после импорта снова накопится разрыв.

1. В [`legacy_project_tools.py`](cod_doc/mcp/tools/legacy_project_tools.py)
   у `add_task` / `update_task` менять docstring на
   `"DEPRECATED — use mcp__cod-doc__task_create instead"`.
2. На уровне реализации `Project.add_task` ([`core/project.py:188-192`](cod_doc/core/project.py#L188-L192))
   при `tasks.yaml` уже архивированном → бросать `RuntimeError("legacy
   tasks.yaml archived; use DB-backed task_create")`. Не «молча писать
   в новый YAML» — это гарантирует, что архивирование = окончательное.
3. После закрытия первой партии проектов (3-4 штуки) — выкинуть
   write-методы `Project.add_task / update_task` целиком, оставить
   только read-side (`get_tasks`, `_load_tasks`) для legacy-страницы.

### 2.5. Расширенное превью на legacy-странице

Сейчас [`tasks_legacy_list.html:48-69`](cod_doc/templates/web/project/tasks_legacy_list.html#L48-L69)
показывает 5 колонок. Перед «нажми import» пользователь должен видеть,
что именно мигрируется. Минимум:

- Раскрывающийся `<details>` на каждой строке: description + result.
- Колонка «target plan-section» — для ясности, что всё попадёт в
  один синтетический plan (`imported-legacy / Imported (legacy)`).

## 3. Что не делаем

- **Двусторонний sync YAML↔DB.** Это рассинхронизация, а не миграция.
  YAML — источник для одноразового импорта, дальше read-only → archive.
- **UI-редактор YAML.** Если задачу хочется поправить — мигрируй проект
  в БД и редактируй там.
- **Авто-импорт при первом заходе на страницу.** Пользователь должен
  явно нажать кнопку — у него могут быть веские причины не мигрировать
  (например, экспериментальный проект на удаление).
- **Selectable rows / частичный импорт.** Это разовая операция; UX
  с чекбоксами увеличивает поверхность багов, а ценность нулевая.

## 4. План работ

| Шаг | Задача | Файлы |
| --- | --- | --- |
| 1 | `POST /tasks/legacy/import` (htmx-эндпоинт + dry-run) | `api/web/pages/tasks.py`, `templates/.../tasks_legacy_list.html` |
| 2 | `POST /tasks/legacy/archive` | те же |
| 3 | Обновить `tasks_list.html` baner: «imported / not yet imported / archived» по состоянию БД и `tasks.archived.yaml` | `templates/.../tasks_list.html`, `api/web/pages/tasks.py:tasks_list` |
| 4 | Раскрывающееся превью description/result на legacy-странице | `templates/.../tasks_legacy_list.html` |
| 5 | Пометить deprecated `add_task`/`update_task` MCP, поднять `RuntimeError` на write при archived state | `mcp/tools/legacy_project_tools.py`, `core/project.py` |
| 6 | Тест: legacy → import → repeat-import = no-op (через duplicate detection в `task_service.create`) | `tests/web/test_tasks_page.py`, `tests/services/test_restate_importer.py` |
| 7 | После 3-4 успешных миграций — удалить write-методы legacy полностью | `core/project.py`, `mcp/tools/legacy_project_tools.py` |

Шаги 1-4 — одна задача (`COD-XXX: legacy tasks UI import`). Шаги 5-7 —
отдельная следом, чтобы не смешивать UX и deprecation в один PR.

## 5. Риски и контрмеры

| Риск | Контрмера |
| --- | --- |
| Повторный нажим Import → дубли в БД | `restate_importer` уже использует `task_service.create(allow_duplicate=True, reason="restate-import:<id>")`; нужно поменять на `allow_duplicate=False` + skip-by-title. После архивации YAML повторный запуск физически невозможен |
| Архивирование удаляет данные, которые ещё могут понадобиться | `tasks.yaml → tasks.archived.yaml` — переименование, не удаление; в `git history` файл всё равно лежит |
| Пользователь нажал Import, но пайплайн уронил половину задач | `import_legacy_tasks` уже использует savepoint per-row (COD-071), частичный успех видно в summary; ошибки логируются с title-ключом |
| MCP-агент в фоне всё ещё пишет в YAML параллельно с импортом | Рекомендация: выполнять импорт при остановленном daemon (новая UI-кнопка stop/start уже есть — `b07a97e`); долгосрочно — RuntimeError на write после archive |

## 6. Критерии приёмки

- На странице `/p/{slug}/tasks/legacy` есть кнопка «📥 Import all», по
  которой за один клик 37 задач из YAML появляются в БД с планом
  `imported-legacy`.
- Повторный клик не создаёт дублей.
- После успешного импорта пользователь может перейти на `/p/{slug}/tasks`
  и увидеть импортированные задачи в общем списке.
- После «Mark as archived» legacy-баннер с `/tasks` исчезает; страница
  `/tasks/legacy` отдаёт 404 или «archived 2026-…».
- `add_task` через legacy MCP на архивированном проекте падает с
  понятным сообщением, а не пишет в новый файл.
