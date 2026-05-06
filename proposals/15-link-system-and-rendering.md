---
status: draft
type: tech-proposal
author: human:dakh
date: 2026-05-06
scope: web-ui · link-service · markdown-renderer · semantic-backfill
related:
  - cod_doc/api/web/markdown.py
  - cod_doc/services/link_service/__init__.py
  - cod_doc/services/link_service/parser.py
  - cod_doc/services/link_service/resolver.py
  - cod_doc/services/doc_service.py
  - cod_doc/services/import_service.py
  - cod_doc/cli/link.py
  - cod_doc/core/reindex.py
---

# Proposal 15 · Доработка системы ссылок и рендеринга документов

> 🎯 Цель: (1) починить сломанную верстку нумерованных списков,
> (2) закрыть дыры в `link_service` (импорт/массовые правки/URL),
> (3) добавить **семантический backfill** — умное восстановление
> outgoing/incoming-связей у документов, которые были созданы
> до COD-079 либо импортированы как plain-markdown без `[label](key)`.

## 1. Что не так сейчас

### 1.1. Верстка списков (визуальный баг на скриншоте)

В UI документ `obsidian/Modules/INFRA Tools Architecture/sql-workflow`
секции «Step 4 — Handle Migration Drift» и «Step 5 — Report Findings»
рендерятся **одной строкой**:

> 1. **Document the drift explicitly.** … 2. **Assess whether the drift
> blocks the current task:** … 3. **Do not auto-resolve structural
> drift** — surface it every time.

Ожидалось — три отдельных `<li>`.

Корень — [`cod_doc/api/web/markdown.py:243-248`](cod_doc/api/web/markdown.py#L243-L248):

```python
# Bullet list?
if line.startswith(("- ", "* ")):
    flush_paragraph()
    flush_blockquote()
    list_items.append(line[2:])
    i += 1
    continue
```

Парсер ловит **только** `- ` / `* `. Префикс `1. `, `2. `, `10) ` не
распознаётся как маркер списка → строка падает в
`paragraph_lines.append(line)` ([markdown.py:251](cod_doc/api/web/markdown.py#L251)),
все пункты склеиваются в один `<p>` через перевод строки, который CSS
схлопывает в пробел. Отсюда «список в строчку».

Дополнительно в docstring [`markdown.py:17`](cod_doc/api/web/markdown.py#L17)
нумерованные списки даже не упомянуты как out-of-scope —
по сути undocumented gap.

### 1.2. Система ссылок — что работает и где течёт

Архитектура корректная, 5-стадийная (parse → sync → resolve → verify →
cascade), см. [`link_service/__init__.py:1-68`](cod_doc/services/link_service/__init__.py#L1-L68).
Парсер ([`parser.py`](cod_doc/services/link_service/parser.py)) ловит
`[[doc:KEY]]`, `[[doc:KEY#anchor]]`, `[[task:ID]]`, `[[story:ID]]`,
`[[Wiki Title]]`, markdown `[txt](href)`, bare URL.

Auto-sync включается из [`doc_service.py:256,342`](cod_doc/services/doc_service.py#L256)
через `_sync_section_links_safe()` — best-effort, ошибки логируются.
COD-079 исправил регресс «links always empty».

| Проблема | Где | Влияние |
|---|---|---|
| **Импортированный документ может не иметь ссылок.** До COD-079 импорт шёл мимо `_sync_section_links_safe`. Юзеру нужно вручную помнить про `cod-doc link backfill`. | [`import_service.py:144-206`](cod_doc/services/import_service.py#L144-L206), [`cli/link.py:204-258`](cod_doc/cli/link.py#L204-L258) | После миграции/массового импорта документы выглядят «осиротевшими» — секции `Outgoing/Incoming` пустые, как на скриншоте. |
| **Plain markdown без `[label](key)`.** Если у автора нет привычки ставить canonical-ссылки, документ навсегда останется без рёбер графа — backfill ничего не вернёт, потому что синтаксиса нет. | весь `parser.py` | Граф связей деградирует до «островов». |
| **URL-ссылки никогда не верифицируются.** `verify_section` пропускает kind=`URL` — нет HTTP-проверки. | [`resolver.py`](cod_doc/services/link_service/resolver.py) (verify-ветка) | Битые внешние ссылки молча лежат как `resolved=true`. |
| **patch_section вне UI обходит auto-sync.** Если кто-то правит body через прямой SQL/мерж-операцию или MCP-инструмент в обход `doc_service.patch_section`, ссылки не пересчитываются. | [`doc_service.py:342`](cod_doc/services/doc_service.py#L342) | Дрейф между body и `link`-таблицей. |
| **Нет cross-project ссылок.** Заявлено как deferred. | [`link_service/__init__.py:22-28`](cod_doc/services/link_service/__init__.py#L22-L28) | Документы из разных проектов не могут ссылаться друг на друга. |
| **Нет fuzzy/семантического резолва.** `[[Some Title]]` ищется только по точному совпадению title в БД. | `resolver.py` | Опечатка в wiki-link → `broken_reason=not_found`. |

### 1.3. Семантический слой существует, но не используется для ссылок

В [`cod_doc/core/reindex.py`](cod_doc/core/reindex.py) уже есть
ChromaDB-индекс и embedding-бэкенд (OpenAI или local
sentence-transformers, см. `config.embedding_backend`,
`config.embedding_model`). Он питает `search_docs()` для агента —
но **никогда** не используется ни для (а) автоподсказок ссылок при
редактировании, ни для (б) backfill графа после импорта. Инфраструктура
есть, поверхность к ней — нет.

## 2. Что предлагается

### 2.1. Починка ordered lists (P0, маленькая правка)

В [`markdown.py`](cod_doc/api/web/markdown.py):

1. Добавить регэксп `_OL_ITEM = re.compile(r"^(\d+)[.)]\s+(.+)$")`.
2. Завести `ol_items: list[str]` и `flush_ol_list()` по аналогии
   с `flush_list()`. Выводить `<ol>…</ol>`. Стартовый номер брать из
   первого пункта (`<ol start="N">`) — нужно для случаев, когда автор
   разрывает список параграфом и продолжает с «5.».
3. Перед веткой `if line.startswith(("- ", "* "))` добавить ветку
   `if m := _OL_ITEM.match(line): …`.
4. В `flush_all()` добавить `flush_ol_list()`.
5. В docstring строки 14-19 явно перечислить supported/unsupported.
6. Тесты в [`tests/api/web/test_markdown.py`](tests/api/web/test_markdown.py)
   (создать, если нет): single-item, multi-item, mixed `1.`/`1)`,
   разрыв списка пустой строкой, продолжение нумерации, смешанный
   bullet+numbered.

**Не делать** на этой итерации: вложенные списки (требует stack
indent-уровней — отдельная задача), GFM task-lists `- [ ]`.

### 2.2. Гарантия auto-sync на всех write-путях (P1)

Сейчас auto-sync висит на `add_section`/`patch_section` в
`doc_service`. Нужно:

1. Поднять его на уровень **репозитория** или единого
   `_apply_section_body_change()` хука — чтобы любой будущий вызов
   не мог обойти.
2. В `import_service.import_markdown()` ([import_service.py:144-206](cod_doc/services/import_service.py#L144-L206))
   после цикла `add_section` добавить **финальный** проход
   `link_service.resolve_section()` для всех вставленных секций —
   сейчас resolve вызывается из sync, но между ними успевают вставиться
   соседние секции, и forward-link `[[doc:NEW-DOC]]` на ещё не созданный
   документ останется `resolved=false`. Двухпроходный импорт это чинит.
3. В CLI/MCP оставить `link backfill` как safety-net для legacy данных,
   но в UI убрать необходимость его помнить — см. 2.3.

### 2.3. **Semantic backfill** — умный анализ документации

Это центральное предложение. Сценарий: пользователь импортировал 200
markdown-файлов из Obsidian. У них либо вики-стиль `[[Note]]`, либо
вообще plain-text упоминания. Граф связей пуст.

#### 2.3.1. Источник сигнала

Использовать **уже работающий** ChromaDB-индекс из
[`reindex.py`](cod_doc/core/reindex.py). Он индексирует тело каждой
секции с эмбеддингами `config.embedding_model`. Это даёт нам бесплатный
поиск «семантически похожих секций» без нового стора.

#### 2.3.2. Алгоритм (per project, idempotent)

Для каждой секции `S` проекта:

1. **Вытащить кандидатов через эмбеддинг** — top-K (K=20)
   ближайших секций из ChromaDB, исключая саму `S` и секции того же
   документа. Сходство по cosine.
2. **Шумовой фильтр** — оставить только кандидатов с similarity ≥ τ
   (стартовое τ=0.78, настройка `config.semantic_link_threshold`).
3. **Переранжирование лексическими сигналами** — для каждого
   кандидата `C`:
   - `+0.10` если title `C` встречается как substring в body `S`
     (case-insensitive, word-boundary). Это ловит «упоминания без
     ссылки».
   - `+0.15` если doc_key `C` встречается как substring (catch для
     импортов из Obsidian, где люди писали путь руками).
   - `+0.05` если у `C` уже есть incoming-ссылки из того же
     родительского модуля (значит, концепт связный).
   - `−0.10` если `C` — orphan-секция без incoming/outgoing вообще
     (вероятно, шум).
4. **Cap-and-confirm** — top-N (N=5) после реранкинга предлагаются
   как **suggestions**, не пишутся в `link`-таблицу автоматически.
5. Suggestions попадают в новую таблицу `link_suggestion`
   (`from_section_id`, `to_doc_key`, `to_section_id`, `score`,
   `evidence` JSON, `state` ∈ `pending|accepted|rejected`,
   `created_at`).

#### 2.3.3. Поверхность для пользователя

- **CLI**: `cod-doc link suggest --project P [--threshold 0.78]
  [--apply-above 0.92]` — печатает таблицу
  «section → suggested target → score → evidence», с флагом
  `--apply-above X` сразу аксептит то, что выше X (для случая
  «доверяю, всё что > 0.92 — точно линк»).
- **Web**: на странице документа в подвале (где сейчас «No outgoing
  links yet») — секция «Suggested links» с списком pending-suggestions
  и кнопками `Accept` / `Reject` по каждой. Accept вставляет
  `[label](doc-key)` в конец body соответствующей секции (или в
  специальный авто-управляемый блок «See also»), trigger-ит обычный
  sync-resolve пайплайн, переводит suggestion в `accepted`.
- **MCP**: tool `link_suggest_for_section(section_id)` — для
  агента-ассистента, чтобы он мог в автономном режиме предлагать
  ссылки во время написания.

#### 2.3.4. Почему это не брутфорс

Полный перебор пар секций — O(N²), на проекте в 5к секций это
2.5×10⁷ сравнений. Через ANN-индекс ChromaDB → O(N·log N) с константой
поиска top-K. На 5к секций — секунды, не часы. Реранкинг работает
только на 20×N = 100k мелких операций (substring match), что тоже
дешёво.

#### 2.3.5. Где это лежит в коде

Создать `cod_doc/services/link_service/semantic.py`:

```
def suggest_for_section(session, section_id, *, k=20, tau=0.78) -> list[Suggestion]:
    ...

def backfill_project(session, project_id, *, apply_above=None, dry_run=False) -> Report:
    ...
```

Зависимости: `core.reindex` (эмбеддинги), `infra.repositories.section_repo`
(метаданные), `infra.repositories.link_repo` (фильтр уже-существующих
рёбер). Нового embedding-стека **не вводим**.

### 2.4. URL-верификация (P2, опционально)

Не в этом proposal. Отдельной задачей: фоновая периодическая job
(routine), которая опрашивает URL-links HEAD-запросом, ставит
`broken_reason=http_5xx|http_4xx|timeout`. Не на write-path —
там по-прежнему пропускаем (правило §7).

## 3. Roadmap

| Шаг | Содержание | Файлы | Размер |
|---|---|---|---|
| **15.1** | Ordered lists в renderer + тесты | [`markdown.py`](cod_doc/api/web/markdown.py), `tests/api/web/test_markdown.py` | S |
| **15.2** | Двухпроходный импорт: после `import_markdown` — пакетный `resolve_section` для всех вставок | [`import_service.py`](cod_doc/services/import_service.py) | S |
| **15.3** | Поднять auto-sync на уровень единого хука; задокументировать инвариант «любая правка body → sync» | [`doc_service.py`](cod_doc/services/doc_service.py) | M |
| **15.4** | Модель `LinkSuggestion` + миграция Alembic | `cod_doc/infra/models/`, `alembic/versions/` | M |
| **15.5** | `link_service/semantic.py` — алгоритм 2.3.2, юнит-тесты на синтетическом эмбеддинг-сторе | `cod_doc/services/link_service/semantic.py` | L |
| **15.6** | CLI `cod-doc link suggest` и `link suggest --apply-above` | [`cli/link.py`](cod_doc/cli/link.py) | M |
| **15.7** | Web-UI «Suggested links» в подвале документа | [`templates/web/_frag/section_view.html`](cod_doc/templates/web/_frag/section_view.html), [`api/web/pages/docs.py`](cod_doc/api/web/pages/docs.py) | M |
| **15.8** | MCP-инструмент `link_suggest_for_section` | [`mcp/tools/link_tools.py`](cod_doc/mcp/tools/link_tools.py) | S |

P0 — шаг 15.1 (визуальный баг, фиксится за один коммит). Остальное —
последовательно, semantic backfill (15.4–15.7) — отдельная фаза с
kickoff-brief по стандарту проекта.

## 4. Что **не** делаем

- Не вводим второй эмбеддинг-стек. Только то, что уже есть в `reindex`.
- Не пишем suggestions в `link` напрямую — отдельная таблица, чтобы
  ложноположительные не загрязнили граф.
- Не делаем real-time suggestion-on-keystroke — батчевый CLI/UI-флоу
  достаточен и предсказуем.
- Не трогаем cascade на rename — он уже работает корректно.
- Не покрываем nested lists и task-lists (`- [ ]`) — отдельная задача.

## 5. Открытые вопросы

1. Стартовое значение τ — 0.78 эмпирическое. Нужен датасет «known good
   links» из текущего проекта, чтобы откалибровать precision/recall.
2. Куда писать accept'нутую ссылку: в конец секции, в auto-секцию
   «See also», или предлагать пользователю выбрать место? Дефолт —
   «See also», auto-managed блок (как footer-summary в COD-078).
3. Нужно ли suggestions триггерить автоматически на каждый импорт,
   или только по явной команде? Предлагается: на импорт — только
   индекс обновляем, suggestions считаем по требованию (их генерация
   инвалидируется добавлением новых секций).
