# 11 — `AGENTS.md` как контракт для контрибьюторов

> Категория: 🔵 Архитектура · Риск: низкий · Зависимости: 01

## Контекст: как у paperclip

Корневой [`AGENTS.md`](https://github.com/paperclipai/paperclip/blob/master/AGENTS.md) — единый документ для **людей и AI-контрибьюторов**:

Структура:
1. **Purpose** — что делает проект, текущая итерация.
2. **Read This First** — упорядоченный список доков, обязательных к прочтению.
3. **Repo Map** — где что лежит.
4. **Dev Setup** — как запустить локально.
5. **Core Engineering Rules** — ключевые инварианты («single-assignee model», «atomic checkout», «approval gates»).
6. **Database Change Workflow** — пошаговый рецепт.
7. **Verification Before Hand-off** — что прогнать перед PR.
8. **API and Auth Expectations** — обязательные паттерны.
9. **UI Expectations** — обязательные паттерны.
10. **Pull Request Requirements** — обязательный шаблон + поле "Model Used".
11. **Definition of Done** — формальный чек-лист.

Эффект:
- AI-агенту не нужно угадывать конвенции — читает один файл и действует.
- Человеку — то же самое + единая точка обновления правил.
- Поле **Model Used** в PR-шаблоне делает явным, какая модель писала код (audit, transparency).

## Текущее состояние cod-doc

В корне есть `MASTER.md` — но это **навигатор по проекту**, не контракт «как с этим репо работать».

Что отсутствует или разбросано:
- Quick-команды (lint/test/build) есть в `MASTER.md` § 4 «Quick Actions», но не выделены как контракт.
- Engineering rules (где какие модули, как писать код) — частично в `arch/` и `specs/`, не консолидировано.
- Definition of Done — нигде не зафиксирован.
- PR-template — нет (или нет специальной структуры).
- Memory-правила (FM-эскалации, audit-cadence) живут в личной памяти пользователя, не доступны контрибьютору.

## Предложение

### 1. Создать `AGENTS.md` в корне

Не дублировать `MASTER.md`, а **дополнять его** — `MASTER.md` про **что есть в проекте**, `AGENTS.md` про **как с проектом работать**.

Минимальная структура:

```markdown
# AGENTS.md — Гид для контрибьюторов (people & AI)

## 1. Цель
COD-DOC — система автоматизированного управления документацией с MCP-интеграцией.
Текущая итерация: <см. MASTER.md → Project Status>.

## 2. Прочесть в первую очередь
1. `MASTER.md` — карта проекта.
2. `arch/architecture.md` — архитектура.
3. `specs/modules.md` — контракты модулей.
4. `cod_doc/skills/orchestrator/SKILL.md` — heartbeat-протокол агента (см. proposal 01).

## 3. Карта репо
- `cod_doc/agent/` — оркестратор и LLM-взаимодействие
- `cod_doc/mcp/` — MCP-сервер и tools
- `cod_doc/core/` — доменные модели
- `cod_doc/skills/` — модульные инструкции для агента
- `arch/`, `specs/`, `models/`, `docs/` — спецификации проекта
- `proposals/` — RFC по улучшениям
- `tests/` — pytest

## 4. Dev setup
pip install -e .[dev]
pytest tests/ -v --tb=short

## 5. Core rules
1. **Hash-verified docs.** Любое изменение doc → пересчёт sha → обновление в MASTER.md.
2. **Snowball Protocol.** Грузить контекст по уровням L0/L1/L2.
3. **Атомарный checkout** (см. proposal 06) — переход `todo → in_progress` только через checkout.
4. **Run-id на всех мутациях** (см. proposal 04).
5. **Контракты MCP-tools** — менять синхронно с тестами и регистрацией в `tool_defs.py`.

## 6. Workflow изменения схемы БД
1. Edit `cod_doc/core/...` или таблицы в `_db.py`.
2. Создать миграцию через alembic.
3. Прогнать `alembic upgrade head` + тесты.

## 7. Verification before hand-off
ruff check cod_doc/ tests/
ruff format --check cod_doc/ tests/
mypy cod_doc/
pytest tests/ -v --tb=short --timeout=120

Если что-то не запускалось — явно отметить «not run, because <reason>».

## 8. MCP-tool conventions
- Чистые функции, никаких глобальных мутаций.
- Возврат — typed dataclass или TypedDict.
- Документировать в docstring (это идёт в `tools/list`).
- Регистрация в `cod_doc/agent/tool_defs.py` синхронно с реализацией.

## 9. UI expectations
- Markdown-таблицы — всегда с явным заголовком.
- Линки между документами — через гибридные refs.

## 10. PR требования
Заполнить шаблон `.github/PULL_REQUEST_TEMPLATE.md` целиком:
- **Что изменено** — bullet list
- **Зачем** — мотивация
- **Как проверить** — шаги
- **Риски** — что может пойти не так
- **Model used** — модель, использованная при разработке (или "human-authored")
- **Checklist** — все пункты

## 11. Definition of Done
- [ ] Поведение соответствует задаче / спецификации.
- [ ] Тесты, lint, mypy, форматирование зелёные.
- [ ] Контракты синхронизированы (модели ↔ MCP ↔ UI ↔ docs).
- [ ] Документы (MASTER, arch, specs, models) обновлены при изменении поведения.
- [ ] PR заполнен по шаблону.
- [ ] Если изменение видимо в UI — сделан скриншот / описание.
```

### 2. Создать `.github/PULL_REQUEST_TEMPLATE.md`

С обязательным полем `Model used` (как у paperclip — это полезный сигнал для аудита AI-вкладов).

### 3. Связь со скиллами

`AGENTS.md` ссылается на `cod_doc/skills/` (proposal [01](01-skills-layer.md)) как на **канонические инструкции для агента**. Это устраняет split-brain: агент читает то же, что и человек.

## План внедрения

1. **Драфт `AGENTS.md`** — на основе шаблона выше + текущих неявных конвенций (опросить через grep'ы по `# TODO`, `# FIXME`, существующие docstring'и).
2. **PR-template.**
3. **Перенос правил из `MEMORY.md`** — то, что shared (FM-валидация, audit-cadence) — в скиллы (см. [01](01-skills-layer.md)) и сюда. Личное (preference про язык, тон) — остаётся в memory.
4. **Линковка из `MASTER.md`** — добавить пункт «Read AGENTS.md before contributing».
5. **Hooks (опционально):** pre-commit hook, проверяющий, что PR-описание содержит required-секции.

## Риски

- **Дрифт.** Файл может устареть. Решение: одна из routines (см. [07](07-routines.md)) — `agents_md_freshness` — флагит, если AGENTS.md не трогался > 90 дней при активной разработке.
- **Дублирование с MASTER.md.** Чёткое разделение: MASTER = «что есть», AGENTS = «как работать».

## Метрики успеха

- Новый контрибьютор (или новая AI-сессия) в состоянии запустить тесты и сделать осмысленный PR, прочитав только AGENTS.md.
- 100% PR содержат заполненное поле «Model used».
- Нет shared-правил, которые живут только в личной памяти.

## Связанные

- 01 (skills) — `AGENTS.md` ссылается на скиллы как канонический источник для агента.
- 04 (run-id) — поле «Model used» дополняет run-id audit (что писала AI vs человек).

## Замечания (контекст cod-doc)

- **Делать после [01](01-skills-layer.md).** AGENTS.md ссылается на `cod_doc/skills/` как канонический источник для агента. Если скиллов ещё нет — ссылаться не на что, и AGENTS.md становится одновременно и meta-документом, и хранилищем правил, что плохо.
- **Перенос из MEMORY.md.** Сейчас shared-знание (FM-валидация, audit-cadence) живёт в личной памяти пользователя — оно невидимо для нового контрибьютора и для новой сессии без памяти. AGENTS.md (через ссылки на скиллы) делает его частью репо.
- **«Model used» как сигнал для аудита.** Поле в PR-template — недорогое улучшение, реальная ценность раскрывается в связке с [04](04-run-id-audit.md): можно сравнивать «что заявил автор» с «что реально писал какой run».
- **Дублирование с MASTER.md § 4 Quick Actions.** Решить заранее: оставляем команды в MASTER (тогда AGENTS просто ссылается «см. MASTER § 4») или переезжаем в AGENTS (тогда чистим MASTER). Не оставлять в обоих.
- **Routine `agents_md_freshness`.** Простой defence от дрейфа — алерт, если AGENTS.md не трогался > N дней при активной разработке. Хороший кандидат для первой routine из [07](07-routines.md).

## Открытые вопросы

- **Q1.** Quick Actions — оставляем в MASTER.md § 4 или переезжаем в AGENTS.md? Где источник истины?
- **Q2.** «Model used» — обязательное поле или опциональное? Что писать для PR из чисто-человеческого редактирования?
- **Q3.** Pre-commit hook на required-секции PR — обязательный, recommended, или вообще без enforcement?
- **Q4.** Кто аудитит свежесть AGENTS.md — routine `agents_md_freshness` или ручной квартальный review?
- **Q5.** Связь с встроенным `AGENTS.md` (есть схожий файл-стандарт у некоторых tools — ChatGPT, Cursor, VS Code Copilot) — мы пишем универсальный или cod-doc-специфичный?
- **Q6.** Локализация — AGENTS.md на русском (как остальная документация проекта) или на английском (стандарт открытого кода)?
