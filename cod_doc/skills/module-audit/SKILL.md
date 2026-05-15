---
name: module-audit
description: |
  Обязательный 5-мерный drift-аудит при закрытии модуля или крупной
  задачи: code / logic / style / tests / docs. CI зелёный ≠ модуль
  готов. Финдинги пишутся в audit-report; при ≥ 1 finding — открывается
  remediation plan.
  Триггеры: модуль готов, закрыть модуль, finish module, complete module,
  module done, large task done, milestone, end of phase, post-merge,
  готово, завершён, выкатили, drift check, drift, audit drift, дрифт,
  module audit, completion audit.
---

# Skill — Module audit (drift check)

## Когда подгружается

При закрытии **модуля** (any `docs/modules/<m>.md` с активным статусом)
или **крупной задачи** (multi-day, multi-file, multi-AC). Также при
ручном запросе «проведи аудит» / «check drift».

Не путать со `audit-cadence`: тот про закрытие фаз / consolidation
cycles проекта целиком; этот — про **дрифт-чек по 5 измерениям** для
одного модуля.

## Принцип

Закрытие модуля БЕЗ 5-мерного аудита — pencil-whip. CI зелёный ≠
модуль готов. CI проверяет **только** code+test, но не logic / style /
docs. Дрифт между этими слоями накапливается тихо и обнаруживается
позже как «странные баги» / «документация не совпадает с кодом» /
«а это вообще ещё работает?».

Аудит делается ВСЕГДА в одном и том же порядке (1→5), даже если
кажется, что измерение «не применимо». Если измерение не применимо —
явно пиши «N/A» с причиной.

## 5 измерений дрифта

### 1. Code drift — реализация vs спецификация

Проверь, что то, что НАПИСАНО в коде, соответствует тому, что
ОБЪЯВЛЕНО в спецификациях.

| Сверяем | С чем |
|---------|-------|
| Публичные функции / методы / endpoints | `docs/modules/<m>.md` § Signatures / Endpoints |
| Data shapes (структуры, JSON-схемы, ORM-модели) | `DATA_MODEL.md`, миграции |
| Файловый layout, dependency-направления | `ARCHITECTURE.md` compose / layer-диаграмма |
| Имена сущностей (тип, поле, переменная) | Везде, где они объявлены |

Инструменты: `doc_drift`, `check_stale_refs`, `hash_file`, ручной diff.

Симптом code-drift: «функция называется по-другому, чем в module-spec»,
«в DATA_MODEL колонка `email`, а в коде `mail`».

### 2. Logic drift — поведение vs user-story / AC

**Это самое незаметное измерение.** CI его не ловит. Только мысленный
walk-through и интеграционный прогон.

- Перечитай **acceptance criteria** каждой задачи модуля. Реализовано
  ли _именно это_, или «похожее»?
- Перечитай связанные **user-stories**. Сценарий проходит от начала до
  конца? Алтернативные ветки (refused / error) тоже покрыты?
- Edge cases из stories покрыты или silently dropped?
- Smoke-flow: пройди руками 1 happy + 1 error путь.

Симптом logic-drift: «всё работает, но не _то_, что просили», «мы
реализовали запись в БД, а в AC сказано: «после записи отправить
notification» — забыли».

### 3. Style drift — стиль кода / документов

- **Lint**: `ruff` / `mypy` / formatter — нулевой output. Если warning'и
  накопились — это уже drift.
- **Naming**: snake_case в Python, kebab в slug'ах, согласованность
  однотипных идентификаторов (`auth_handler` vs `auth_h`).
- **Docs prose**: см. skill `doc-style` — язык, ссылки, frontmatter,
  заголовки, гибридные refs.
- **Code structure**: dependency injection вместо globals, нет import
  cycles, нет TODO без owner'а.

### 4. Test drift — тесты здоровы

- CI зелёный.
- Новые модули / функции имеют тесты — **особенно error paths и edge
  cases**, не только happy.
- Coverage не упал относительно baseline (если есть).
- Удалённые / переименованные фичи → удалены / переименованы и их
  тесты. Никаких «orphaned» тестов на несуществующие функции.
- Имена тестов описывают _что_ тестируется, не _как_ (`test_login_with_expired_token_returns_401`, не `test_login_2`).
- Flaky тесты помечены `@pytest.mark.flaky` с задачей на устранение.

### 5. Documentation drift — документы отражают реальность

- **Гибридные ссылки**: `link_verify` / `check_stale_refs` — все
  статусы 🟢. Ни одного 🔴 STALE / BROKEN.
- **Хэши**: `update_master_hashes` после любых правок исходников,
  которые упомянуты в MASTER.md.
- **Frontmatter `status`**: модулям активного релиза → `active`. Если
  модуль deprecated — переведи статус и упомяни в MASTER.md.
- **MASTER.md**: добавь / обнови раздел модуля, если public API менялся.
- **ADR / decision-log**: новые архитектурные решения зафиксированы.

## Выходной артефакт: audit-report

**`audit-report`** в `docs/system/audit/<YYYY-MM-DD>-<module>-drift.md`.

Формат заголовка / frontmatter — см. skill `audit-cadence`.

В разделе **Findings** перечисли проблемы по каждому измерению с
префиксом `[code]` / `[logic]` / `[style]` / `[test]` / `[docs]`:

```
F1 [code] PublicAPI `auth.refresh_token()` объявлена в module-spec,
   отсутствует в коде. Либо реализовать, либо удалить из спеки.
F2 [logic] AC задачи AUTH-007 «при revoke токена отправить webhook»
   не выполнен — webhook отсутствует в логе вызовов.
F3 [style] Идентификатор `auth_h` (3 места) inconsistent с
   `auth_handler` (12 мест). Унифицировать.
F4 [test] Нет тестов для error-path `raise OnMissingToken` в
   `auth/middleware.py:42`.
F5 [docs] `auth.md` § Endpoints stale: 3 endpoints переименованы в
   коде, в доке остались старые имена.
```

В конце таблица сводки:

| Измерение | F-count | Severity (C/M/L) |
|-----------|---------|---------------------|
| code  | 1 | C: 1 |
| logic | 1 | C: 1 |
| style | 1 | L: 1 |
| test  | 1 | M: 1 |
| docs  | 1 | M: 1 |

## Если findings ≥ 1

Открой **remediation plan** через `plan_create`:

```
scope: "<module>-drift-remediation-<YYYY-MM-DD>"
principle: "Закрыть все F# из audit-report до перевода модуля в active"
```

В план добавь по одной задаче на каждый finding с
`type=bug/refactor/docs/test`, `priority` по severity, `description`
со ссылкой на F# в audit-report.

Только после закрытия remediation plan модуль переводится в `active`.

## Анти-паттерны

- ❌ **«Прошло CI → готово».** Только Code+Test покрыто; Logic /
  Style / Docs — нет.
- ❌ Аудит только своего слоя (например, только code-drift) — это
  «частичный» аудит, не drift-check. Все 5 измерений обязательны.
- ❌ Закрыть модуль с открытыми `F1..FN` без remediation plan.
- ❌ audit-report без сводной таблицы по типам.
- ❌ Findings без severity (`C` / `M` / `L`) — невозможно
  приоритезировать remediation.
- ❌ «Аудит сделал, нашёл 0 проблем» без явного перечисления
  проверенных пунктов. Иначе невозможно отличить «всё ок» от «не
  проверил».

## Связанное

- Skill `audit-cadence` — формат audit-report (общий шаблон + куда
  кладётся файл).
- Skill `drift-handling` — что делать со STALE / BROKEN ссылками,
  hash-mismatch.
- Skill `doc-style` — конвенции style-drift для docs prose.
- Skill `task-standard` — формат задач, которые попадут в remediation
  plan.
- `docs/system/standards/task-plan.md` § «Closing a module».
