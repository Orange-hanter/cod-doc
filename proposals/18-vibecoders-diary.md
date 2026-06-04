# 18 — Vibecoder's Diary: activity_log → human-friendly daily doc

> Категория: 🟡 Адаптация · Риск: низкий · Зависимости: 09-activity-log, model_catalog (COD-059)

## Контекст: «что я вчера накодил?»

Vibecoder работает в потоке. К концу дня в `activity_log` (proposal 09) накапливается 30-80 событий: `task_checkout`, `doc_create`, `task_update_status`, `adr_link_task`, `commit_link`, etc. Через неделю — невозможно вспомнить.

Существующая боль:
- **Нет human-friendly обзора дня.** `activity_list` возвращает сырые JSON-события. Чтобы понять «что сделал за день» — нужно вручную агрегировать.
- **Нет changelog'а проекта.** Если посреди спринта тебя выдернули на 3 дня — потом не догнать, что происходило.
- **Нет story-telling.** Датасетам из 50 событий нужна narrative: «вчера доделал OBI-040, нашёл drift в ADR-002, починил pre-commit».

## Текущее состояние cod-doc

- `activity_service` (`cod_doc/services/activity_service.py`) — emit/list/get, PCA-111.
- `model_catalog` (COD-059) — уже умеет перечислять LLM-модели с ценами.
- `section_summary_service` — генерит секционные саммари (есть инфра для LLM-прохода).
- MCP-тулы: `activity_list`, `activity_for_run`, `activity_get`.
- **Нет:** scheduled job, который превращает `activity_list` за день в `doc` типа `daily/YYYY-MM-DD.md`.

## Предложение

Создать `cod_doc/services/daily_diary.py` + routine `daily_diary_evening`:

### 4.1. Pipeline

```
activity_list(since=yesterday_18:00, until=today_18:00)
  ↓ group by (project, task_id, file_path)
  ↓ filter out: noise events (heartbeats, capability refreshes)
  ↓ LLM-prompt: "вот 50 событий, сделай human-friendly changelog на русском"
  ↓ LLM returns: { sections: [{title, summary, files_touched, tasks_done}], highlights: [...] }
  ↓ doc_create(type='daily', slug='YYYY-MM-DD', body=...)
  ↓ activity_log.emit(event='daily_diary_generated', doc_id=...)
```

### 4.2. Где живёт дневник

- **Внутри cod-doc**, как `doc` с новым типом `daily` (рядом с `system`, `adr`, `task_doc`).
- **Web-страница** `/p/<project>/diary` — календарь с кликабельными днями.
- **Опц.:** `cod-doc diary today` CLI — показывает сегодняшний дневник в терминале (Markdown).

### 4.3. Routine

```python
# cod_doc/services/daily_diary.py
def generate_daily_doc(
    project: str,
    date: date,
    *,
    model: str = "anthropic/claude-sonnet-4-6",  # из model_catalog
) -> Doc:
    """Generate a human-friendly daily doc from activity_log."""
    events = activity_service.list(
        project=project,
        since=datetime.combine(date, time(0, 0)),
        until=datetime.combine(date, time(23, 59)),
    )
    # ... LLM-pass → DocCreate
    return doc

# cod_doc/routines.py (proposal 07)
routine_register(
    name="daily_diary_evening",
    schedule="0 18 * * *",  # каждый вечер в 18:00
    handler="cod_doc.services.daily_diary:generate_for_all_projects",
)
```

### 4.4. Prompt design (ключевая часть)

```yaml
system: |
  Ты — Vibecoder's Diary writer. Получаешь список событий из cod-doc
  activity_log за один день. Твоя задача — сделать human-friendly
  обзор на русском языке, длиной 200-400 слов.

  Структура:
  - Заголовок: "📅 YYYY-MM-DD — <project>"
  - Краткий TL;DR (1-2 предложения)
  - Что доделано (по задачам)
  - Что начато, но не закрыто
  - Подозрительные паттерны (например, 5 возвратов в in_review за день = bottleneck)
  - Цитаты из кода или PR, если есть

  Не выдумывай. Если событий мало (≤5) — напиши "тихий день, починка мелочей".

user: |
  Вот события за {date} для проекта {project}:
  <events>
  {events_json}
  </events>
```

### 4.5. MCP-тулы (cycle-5 поверхность)

```
diary_generate(project, date?) -> Doc           # разовый запуск
diary_list(project, since?, until?) -> list[Doc]
diary_today(project) -> str  # Markdown для CLI
```

## Эффект

- **Утренний onboarding 1 минута.** Вчерашний дневник — на главной странице проекта.
- **Еженедельный/ежемесячный обзор** — серия дневников, прочитанных подряд, восстанавливает контекст.
- **Post-mortem после инцидента** — `activity_for_run` + `diary_get` рядом.
- **Мотивация vibecoder'а** — видишь свой прогресс, не «ой, опять ничего не сделано», а «вот 5 задач закрыто, 3 на ревью».

## Зависимости

| Proposal / компонент | Нужно для |
|---|---|
| `09-activity-log` (PCA-912) | источник событий |
| `07-routines` (PCA-211) | cron-инфраструктура |
| `model_catalog` (COD-059) | выбор LLM для генерации (cost-aware) |
| `section_summary_service` | паттерн LLM-прохода по docs |

## Структура

```
cod_doc/services/
├── daily_diary.py              # core: generate, list, get
├── daily_diary_prompts.py      # prompt templates
cod_doc/mcp/tools/
└── diary_tools.py              # MCP surface
cod_doc/api/
└── routes_diary.py             # /p/<project>/diary web page
cod_doc/templates/web/
└── diary.html                  # calendar view
tests/services/
└── test_daily_diary.py
```

## Риски и митигация

| Риск | Митигация |
|---|---|
| LLM галлюцинирует (приписывает события) | Prompt строго требует «не выдумывай, цитируй SHA задач». Validation: каждое упомянутое task_id проверяется в БД. |
| Дорого по токенам (50 событий × 1К токенов в день × 30 дней = 1.5M токенов / месяц) | model_catalog: Sonnet для дефолта, Haiku для быстрых дней; опц. отключение для тихих дней (≤5 событий). |
| Privacy: sensitive task titles утекают в LLM | `privacy` поле в task: если True — событие фильтруется до LLM. |
| Дневник сам становится источником дрейфа | doc создаётся с типом `daily`, **не** участвует в `update_master_hashes`. |
| Несогласованность стиля между днями | Один system prompt, один шаблон — стиль стабилен. |

## Acceptance criteria

1. `cod_doc/services/daily_diary.py` существует, покрыт unit-тестами.
2. `routine daily_diary_evening` зарегистрирована, создаёт doc при наличии событий.
3. CLI `cod-doc diary today --project=X` печатает Markdown в stdout.
4. Web `/p/<project>/diary` показывает календарь с кликабельными днями.
5. Cost-aware: для проекта с ≤5 событий — дневник не генерируется, в `activity_log` пишется `daily_diary_skipped_quiet_day`.
6. Privacy: task с `privacy=true` не появляется в `events_json`, который шлётся в LLM.

## Альтернативы

- **Просто читать `git log --author=...` каждый день** — работает, но теряет контекст задач, ADR, docs.
- **Notion / Obsidian daily note руками** — не автоматизировано, через неделю забиваешь.
- **LLM-агент, читающий `activity_list` ad-hoc по запросу** — работает, но без scheduled reminder'а — забываешь спросить.

## Источники

- Реальная боль: при попытке вспомнить «что я делал 12 мая» — 30 минут в git log.
- Paperclip [wake-payload pattern](https://github.com/paperclipai/paperclip) — паттерн «агент получает compact summary, не сырые данные».
- Obsidian Daily Note plugin, Notion Daily Journal — UX-референс.
