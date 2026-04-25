---
type: standard
scope: sensitive-data
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
---

# Sensitive Data Standard

> Документы могут содержать PII, секреты, бизнес-чувствительную информацию.
> Стандарт ограничивает, что и кому отдавать, и как помечать.

## 1. Поле `sensitivity` в Document

```yaml
sensitivity: public | internal | confidential | restricted
```

| Уровень | Что значит |
|---------|-----------|
| `public` | Можно публиковать вовне (open-source, лендинг) |
| `internal` | Внутри организации; default |
| `confidential` | Только перечисленные `audience` |
| `restricted` | Доступ только по явному `actor allow-list` |

## 2. Запрещённый контент

В body документов (любого уровня) запрещены:

- API-ключи, токены, пароли в открытом виде.
- Полные PII (полные имена + контакты + адреса) клиентов в больших списках.
- Дампы БД, превышающие 1000 строк.

Detection — сервис `SensitivityScanner` (regex + entropy для секретов; сэмпл-чек для PII).

## 3. Поведение `context.get`

- Если `actor=mcp:<external>` — секции с `sensitivity ≥ confidential` не возвращаются; маркер «redacted».
- Если `actor=agent:<role>` — проверяется `agent_definition.sensitivity_clearance` (новое поле).

## 4. Поведение `export`

- Markdown projection с `sensitivity ≥ confidential` помечается фронтматтером и **не** экспортируется в публичный CHANGELOG.
- При `cod-doc projection freeze` confidential-секции рендерятся как заглушки `> [content redacted: confidential — see DB]` для public-копии (опционально).

## 5. Audit-checks

`cod-doc audit --sensitivity`:

- Документ без `sensitivity` поля → warning (default `internal`).
- Найдены секрет-паттерны в `public`/`internal` → error.
- Документ с `sensitivity=public` ссылается на confidential → warning.

## 6. Не входит в стандарт

- Шифрование БД at-rest — задача инфраструктуры, не пакета.
- Compliance (GDPR, SOC2) — требует отдельного аудита; этот стандарт даёт строительные блоки, но не сертификацию.
