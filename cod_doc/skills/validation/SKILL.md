---
name: validation
description: |
  Когда применять structural-валидацию (raise) vs advisory-аудит (issues).
  FM-002 / FM-003 эскалируются как блокеры; FM-004 / FM-005 — advisory-комментарии.
  Триггеры: запись в MASTER.md, создание/обновление doc, изменение хэшей,
  frontmatter, sensitivity, validate, audit_*.
---

# Skill — Validation pattern

## Когда подгружается

Задачи и контексты, где идёт **запись** в БД (документ, таск, секция,
ссылка), особенно если задействован frontmatter, sensitivity или хэши.
Триггер-keywords: `validate`, `frontmatter`, `sensitivity`, `FM-002`,
`FM-003`, `audit_`, `audit-` , `structural`, `valid`, `verify`.

## Принципы

Двухуровневая валидация:

1. **Structural — `validate_*`**: жёсткая проверка инвариантов write-path.
   Любое нарушение → `ValidationError` (raise). Гейтит `DocService.create`,
   `TaskService.create`, `StoryService.create` и т. п. Здесь живут
   обязательные правила — без них объект **не должен** появиться в БД.

2. **Advisory — `audit_*`**: возвращает `list[ValidationIssue]` без raise.
   Используется для пред-коммитного `cod-doc audit`, future-CI и
   write-path как мягкая подсветка проблем. Не блокирует.

## FM-эскалации (frontmatter)

| Код | Уровень | Что делать |
|-----|---------|-----------|
| FM-002 | блокер | `validate_frontmatter` raise; запись отвергается |
| FM-003 | блокер | то же |
| FM-004 | advisory | `audit_frontmatter` возвращает issue; запись идёт |
| FM-005 | advisory | то же |
| FM-006 | advisory (sensitivity) | пишется как warning; не блокер |
| FM-007 | advisory | warning при отсутствии `sensitivity` для
        `module-spec/architecture/standard` |

## Алгоритм

1. Определить, **что записываем** — doc / task / story / section / link.
2. Найти соответствующий `validate_*` в `cod_doc/services/validation/`.
3. Если `ValidationError` — НЕ глотать; пробросить наверх с `error_code`
   (FM-NNN или TP-NNN) для вывода человеку.
4. После успешной записи — вызвать `audit_*` и приложить issues к ответу
   как мягкие предупреждения.

## Что НЕ делать

- Не превращать advisory в блокеры по своему усмотрению — это ломает
  pre-existing pipeline.
- Не пропускать `validate_*` "ради скорости" — write-path должен быть
  гейтированным.
- Не дописывать новые FM-коды в скилле; правила живут в
  `cod_doc/services/validation/_rules/` + `standards/frontmatter.md`.

## Связанное

- [standards/frontmatter.md](../../../docs/system/standards/frontmatter.md)
- [services/validation/](../../services/validation/)
