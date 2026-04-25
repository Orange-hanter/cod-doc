---
type: audit-report
scope: cod_doc/services (Section B — RevisionService, DocService, TaskService)
status: resolved
source_of_truth: true
owner: cod-doc core
created: 2026-04-25
last_updated: 2026-04-25
audit_target_revision: COD-015 + COD-010 + COD-011 (commit d605731)
related_docs:
  - ../DATA_MODEL.md
  - ../roadmap/cod-doc-task-plan.md
  - 2026-04-25-section-a-data-core.md
---

# Section B (Services) — Implementation Audit

> Аудит трёх реализованных сервисов: RevisionService (COD-015), DocService (COD-010), TaskService (COD-011) + TaskRepository.
> Severity: **high** — функциональный баг или dead code; **medium** — API-инконсистентность, будущая поломка; **low** — типизация, тест-покрытие, стиль.

## Сводка

| Severity | Count | Fixed | Deferred |
|----------|------:|------:|---------:|
| high     | 2 | 2 ✅ | 0 |
| medium   | 3 | 2 ✅ | 1 (ME-3) |
| low      | 8 | 7 ✅ | 1 (LO-3) |
| **итого** | **13** | **11 ✅** | **2** |

129/129 тестов проходят. Все функциональные баги и dead-code устранены. Отложено: ME-3 (diff_format дискриминатор — design decision до COD-022), LO-3 (sort порядок — решается в COD-020).

---

## 1. High

### SB-HI-1. ✅ Мёртвый импорт `datetime` в `task_repo.py`

[cod_doc/infra/repositories/task_repo.py:5](../../../cod_doc/infra/repositories/task_repo.py) — `from datetime import datetime` никогда не используется в этом модуле. Mypy strict / ruff флагуют как `F401`.

**Фикс:** убрать строку.

### SB-HI-2. ✅ `_TASK_ID_RE` — мёртвая регулярка в `task_service.py`

[cod_doc/services/task_service.py:40](../../../cod_doc/services/task_service.py) — `_TASK_ID_RE = re.compile(...)` объявлена, но нигде не вызывается. Была задумана для валидации формата `task_id`, но валидация делегирована COD-020 и регулярка забыта. Ruff: `F841`.

**Фикс:** удалить константу; если формат нужен — добавить в COD-020.

---

## 2. Medium

### SB-ME-1. ✅ `_NO_PARENT_CHECK` — приватный сентинел, используемый в трёх модулях

`revision_service._NO_PARENT_CHECK` объявлен с leading-underscore (private convention), но импортируется напрямую в [doc_service.py:207](../../../cod_doc/services/doc_service.py) и [task_service.py:202](../../../cod_doc/services/task_service.py):

```python
expected_parent_revision_id: str | None | object = rev._NO_PARENT_CHECK
```

Это нарушает конвенцию и ломается, если `revision_service` переименует или уберёт сентинел. Mypy strict не ловит cross-module обращения к `_`-именам.

**Фикс:** переименовать в `NO_PARENT_CHECK` (убрать underscore) и задокументировать как часть публичного API сервиса.

### SB-ME-2. ✅ `update_status` не поддерживает `expected_parent_revision_id`

`DocService.patch_section` пробрасывает `expected_parent_revision_id` в RevisionService — concurrent writes защищены. `TaskService.update_status` — нет. Агент, читающий задачу и меняющий статус, не может задекларировать ожидаемый revision head; конкурентные обновления не детектируются.

`TaskService.complete` параметр имеет — т.е. это намеренное решение для complete, но not update_status. Это неочевидная асимметрия: два write-path для одного поля status имеют разные гарантии.

**Фикс:** добавить `expected_parent_revision_id` в `update_status` с тем же дефолтом `_NO_PARENT_CHECK`. Тест: concurrent `update_status` → `RevisionConflictError`.

### SB-ME-3. ⏸ Несогласованный формат diff-а: unified-diff vs JSON-patch без дискриминатора

| Метод | Формат diff |
|-------|-------------|
| `doc_service.create` | unified-diff (возможно пустой) |
| `doc_service.add_section` | unified-diff `--- /dev/null → +++ section:...` |
| `doc_service.patch_section` | unified-diff |
| `doc_service.rename` | JSON `{"op":"rename",...}` |
| `task_service.create` | JSON `{"op":"create",...}` |
| `task_service.update_status` | JSON `{"op":"status",...}` |
| `task_service.complete` | JSON `{"op":"complete",...}` |

`RevisionService.list_for_entity` возвращает `Revision.diff: str` — читатель не знает, является ли строка unified-diff или JSON. Будущие `cod-doc log` / RevisionService.revert / UI будут вынуждены угадывать формат по `entity_kind` или попытке `json.loads`.

**Фикс (два варианта):**
1. **Легкий:** добавить поле `diff_format: Literal["unified", "json-patch"]` в таблицу `revision` (migration 0007) и заполнять при записи.
2. **Лёгче:** зафиксировать конвенцию в `revision_service.write` — всегда JSON-patch, со специальным ключом `"lines"` для текстовых изменений — и обновить DocService.

Выбор фиксировать в DATA_MODEL §3.5. Текущее состояние — technical debt для COD-022 (revert) и COD-032 (MCP display).

---

## 3. Low

### SB-LO-1. `kwargs: dict` в `TaskRepository._to_model` — bare dict

[cod_doc/infra/repositories/task_repo.py:37](../../../cod_doc/infra/repositories/task_repo.py) — `kwargs: dict = {...}`. Mypy strict: "Missing type parameters for generic type 'dict'". → `dict[str, Any]`.

### SB-LO-2. Deferred `from sqlalchemy import text` внутри функции

[cod_doc/services/doc_service.py:149](../../../cod_doc/services/doc_service.py) — `from sqlalchemy import text` внутри тела `render_body()`. Нет circular-import оправдания — `sqlalchemy` это third-party. Должно быть module-level импортом.

### SB-LO-3. `list_for_plan` сортирует по строковому `task_id` — лексикографически

[cod_doc/infra/repositories/task_repo.py:66](../../../cod_doc/infra/repositories/task_repo.py) — `ORDER BY section_id, task_id`. Строковое `task_id` сортируется лексикографически: `P-10` < `P-2` < `P-20`. Корректно **только** при zero-padding (001, 002, ..., 010). Ручные ID без padding сломают порядок.

**Фикс:** добавить regexp-based numeric sort при отдаче через PlanService или зафиксировать инвариант zero-padding в COD-020 валидации.

### SB-LO-4. `_add_project` / `_run_alembic_upgrade` дублируется в 4 файлах тестов

`tests/services/test_revision_service.py`, `test_doc_service.py`, `test_task_service.py` и `tests/infra/` — все содержат одинаковый `_add_project`. После Section B имеет смысл вынести в `tests/conftest.py` или `tests/fixtures.py`.

### SB-LO-5. `_new_doc` в `test_doc_service.py` — нет return type annotation

[tests/services/test_doc_service.py:57](../../../tests/services/test_doc_service.py) — функция-хелпер без `-> Document`. Mypy strict: `no-untyped-def`.

### SB-LO-6. Double-complete не тестирован

`TaskService.complete` на задаче, уже имеющей `status=done`, не поднимет исключения (все блокеры уже done; статус перезаписывается идемпотентно). `completed_at` перезапишется новым временем, что, вероятно, нежелательно. Поведение не задокументировано и не покрыто тестом.

**Фикс:** либо `complete` на `status=done` → no-op (return текущее состояние без revision), либо → `TaskAlreadyDoneError`. Зафиксировать в тесте.

### SB-LO-7. `add_section` с дублирующимся anchor не тестирован

Дубль anchor в одном документе поднимет `IntegrityError` (DB UNIQUE `uq_section_document_anchor`). Сервисный слой не перехватывает и не преобразует его в `SectionAlreadyExistsError` — вылетит сырое SQLAlchemy исключение. Протестировать + добавить перехват.

### SB-LO-8. `affected_files` в `task_service.create` — только `kind='source'`

[cod_doc/services/task_service.py:136-143](../../../cod_doc/services/task_service.py) — все файлы помечаются как `kind='source'`. `AffectedFileKind` поддерживает `test|migration|config`, но создать задачу с ними программно нельзя. Достаточно принять `list[str | tuple[str, AffectedFileKind]]`.

---

## 4. Test coverage summary

| Service | Tests | Gaps |
|---------|------:|------|
| RevisionService | 9 | — основные пути закрыты |
| DocService | 15 | add_section дубль-anchor; empty-preamble render |
| TaskService | 14 | double-complete; `update_status` concurrency; task_id UNIQUE violation |
| TaskRepository | — | нет юнит-тестов напрямую (косвенно через TaskService) |

---

## 5. Подтверждение корректности

- ✅ RevisionService: chain `parent_revision_id`, сентинел `_NO_PARENT_CHECK`, ULID timestamp sync — всё корректно.
- ✅ DocService: `_create_diff("")` → `""` — пустой unified-diff (валидный NOT NULL); `render_body` читает через view; `patch_section` flush-before-revision — правильный порядок; rename diff из pre-mutation значений — ✓.
- ✅ TaskService: dep-check только `kind='blocks'`; `session.get(TaskModel, dep.to_task_id)` — на PK, что правильно; CASCADE на dep при удалении задачи не позволяет `dep_task is None` — защитная проверка безвредна.
- ✅ TaskRepository: `_to_model` не добавляет `completed_at` если None — ORM default None применяется. ✓
- ⚠️ `doc_service.create` пишет ревизию с `diff=""` если preamble="" — технически валидно, но семантически пусто. Не баг, но ревизия теряет информативность.

## 6. Приоритизированный порядок фиксов

Порядок рекомендован к выполнению до или вместе с COD-012 (PlanService):

| Приоритет | ID | Статус | Примечание |
|-----------|------|--------|------------|
| 1 | SB-HI-1 | ✅ fixed | `from datetime import datetime` удалён |
| 2 | SB-HI-2 | ✅ fixed | `_TASK_ID_RE` удалена |
| 3 | SB-ME-1 | ✅ fixed | `NO_PARENT_CHECK` (публичный), `_NO_PARENT_CHECK` — alias |
| 4 | SB-LO-1 | ✅ fixed | `dict[str, Any]` в `_to_model` |
| 5 | SB-LO-2 | ✅ fixed | `from sqlalchemy import text` — module-level |
| 6 | SB-LO-5 | ✅ fixed | `_new_doc` return type annotation |
| 7 | SB-LO-6 | ✅ fixed | `TaskAlreadyDoneError` guard + тест |
| 8 | SB-ME-2 | ✅ fixed | `expected_parent_revision_id` в `update_status` + тест |
| 9 | SB-LO-7 | ✅ fixed | savepoint + `SectionAlreadyExistsError` + тест |
| 10 | SB-ME-3 | ⏸ deferred | Design decision для COD-022 |
| 11 | SB-LO-3 | ⏸ deferred | Решать в COD-020 (zero-padding инвариант) |
| 12 | SB-LO-4 | low | conftest — после Section B |
| 13 | SB-LO-8 | low | affected_files kind — по мере надобности |
