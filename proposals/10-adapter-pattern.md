# 10 — Adapter pattern для LLM-исполнителей

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: 01

## Контекст: как у paperclip

В [`packages/adapters/`](https://github.com/paperclipai/paperclip/tree/master/packages/adapters):
- `claude-local`, `codex-local`, `cursor-local`, `gemini-local`, `acpx-local`, `pi-local`, `opencode-local`, `openclaw-gateway`.

Все регистрируются в **mutable registry** (см. [`adapter-plugin.md`](https://github.com/paperclipai/paperclip/blob/master/adapter-plugin.md)):
```ts
registerServerAdapter(adapter)
unregisterServerAdapter(type)
requireServerAdapter(type)
```

Внешние адаптеры могут грузиться через JSON-конфиг (`~/.paperclip/adapter-plugins.json`). Принцип: ядро не должно содержать hardcoded import'ов исполнителей.

## Текущее состояние cod-doc

[cod_doc/agent/orchestrator.py:70-77](cod_doc/agent/orchestrator.py#L70-L77):
```python
self.client = AsyncOpenAI(
    api_key=config.api_key,
    base_url=config.base_url,
    default_headers={...},
)
```

— OpenAI-совместимый клиент захардкожен в конструкторе. Чтобы:
- использовать Claude напрямую (anthropic SDK с tool-use)
- запустить локальную модель (llama.cpp, ollama)
- A/B-тестировать модели

— нужно править `Orchestrator`. Это блокирует эксперименты.

## Предложение

### 1. Интерфейс `LLMAdapter`

```python
class LLMAdapter(Protocol):
    name: str
    capabilities: AdapterCapabilities  # streaming, tool_use, json_mode, vision

    async def chat(
        self,
        messages: list[Message],
        tools: list[ToolDef],
        *,
        max_tokens: int,
        temperature: float | None = None,
    ) -> ChatResponse:
        ...

    async def stream_chat(self, ...) -> AsyncIterator[StreamEvent]:
        ...

    def estimate_tokens(self, text: str) -> int:
        ...

    def cost_estimate(self, input_tokens: int, output_tokens: int) -> Decimal:
        ...
```

### 2. Registry

```python
# cod_doc/agent/adapters/__init__.py
_REGISTRY: dict[str, type[LLMAdapter]] = {}

def register_adapter(name: str, factory: type[LLMAdapter]) -> None: ...
def get_adapter(name: str, config: dict) -> LLMAdapter: ...
def list_adapters() -> list[str]: ...
```

### 3. Встроенные адаптеры

| Адаптер           | Backend                          | Когда                            |
| ----------------- | -------------------------------- | -------------------------------- |
| `openai-compat`   | OpenAI SDK / OpenRouter / vLLM   | default (текущее поведение)      |
| `anthropic`       | anthropic SDK                    | для Claude tool-use native       |
| `ollama`          | ollama HTTP                      | локальные модели                 |
| `mock`            | детерминированный фейк           | тесты                            |

### 4. Конфигурация

В [cod_doc/config.py](cod_doc/config.py):
```toml
[llm]
adapter = "anthropic"

[llm.anthropic]
api_key = "..."
model = "claude-opus-4-7"
```

Селектор адаптера — единственное, что меняется при смене бэкенда.

### 5. Tool-use mapping

Самая сложная часть — разные SDK имеют разные форматы tool-use:
- OpenAI: `tools=[{type:"function", function:{name, parameters}}]`, `tool_calls` в response.
- Anthropic: `tools=[{name, description, input_schema}]`, `content=[{type:"tool_use", ...}]`.

Решение: **внутренний нейтральный формат** в [tool_defs.py](cod_doc/agent/tool_defs.py), каждый адаптер маппит в свой SDK-специфичный.

### 6. Внешние адаптеры (Phase 2)

Аналог `~/.paperclip/adapter-plugins.json` — `~/.cod-doc/llm-adapters.json` со ссылками на python-пакеты, реализующие протокол. Не делать на старте.

## План внедрения

1. **Определить протокол `LLMAdapter`** + типы (`Message`, `ToolDef`, `ChatResponse`, `StreamEvent`).
2. **Извлечь existing OpenAI-логику** в `cod_doc/agent/adapters/openai_compat.py`. Оркестратор больше НЕ создаёт client напрямую.
3. **Refactor `Orchestrator`** — принимает `adapter: LLMAdapter` через DI; всё взаимодействие через интерфейс.
4. **Registry + конфиг.** Селектор по имени.
5. **Реализация `anthropic`-адаптера.** Нативный tool-use.
6. **Реализация `mock`-адаптера** — поднимает покрытие тестами оркестратора.
7. **Документация.** Раздел в HANDBOOK: «как добавить адаптер».

## Риски

- **Большой scope.** Самое тяжёлое из всех proposals. Начинать только когда есть реальная потребность сменить бэкенд (либо для тестов).
- **Tool-use semantic drift.** SDK ведут себя чуть по-разному (например, Claude параллельные tool calls в одном response, OpenAI — sequential). Адаптер должен скрывать это, но возможны утечки абстракции.
- **Cost-tracking.** Если модели стоят по-разному, единая метрика стоимости должна нормализоваться. Заменить heuristics на per-adapter `cost_estimate`.

## Метрики успеха

- Смена LLM-бэкенда — изменение в config, ноль изменений в [orchestrator.py](cod_doc/agent/orchestrator.py).
- Юнит-тесты оркестратора используют `mock`-адаптер, не делают сетевых вызовов.
- Минимум 2 working backend'а (`openai-compat` + `anthropic`) на момент закрытия фазы.

## Связанные

- 01 (skills) — скилл-загрузка не зависит от адаптера, общая на всех.
- Все остальные proposals — нейтральны к выбору адаптера.

## Когда НЕ делать

- Пока единственный backend — OpenRouter с разными моделями (адаптер `openai-compat` это уже покрывает).
- Пока нет ощутимой выгоды от Claude-native tool-use или локальной модели.
- Это чисто архитектурный долг — берёмся, если ясен сценарий потребления.

## Замечания (контекст cod-doc)

- **`mock` адаптер как side-task.** Полный registry — большой scope, но `mock` адаптер для тестов оркестратора можно вытащить отдельно: создать минимальный интерфейс под две реализации (real OpenAI-compat + mock). Это уже даёт детерминированные тесты [orchestrator.py](cod_doc/agent/orchestrator.py), не открывая всю абстракцию.
- **Tool-use semantic drift — реальная боль.** OpenAI делает tool calls последовательно, Anthropic — параллельно в одном response. Это не «мелкая разница SDK», это семантика. Адаптер обязан скрывать, и это требует тестового покрытия по обоим бэкендам.
- **Cost-tracking нормализация.** `claude-opus` и `gpt-4o` стоят разных денег за токен; единая метрика `cost_event` должна содержать абсолютное значение в одной валюте, а не «токены». Источник pricing — статический справочник в коде или внешний API?
- **Когда НЕ делать — RFC сам говорит.** Пока единственный реальный сценарий — OpenRouter с разными моделями, текущий `openai-compat` это покрывает. Не браться, пока не появится явный запрос на Claude-native или локальную модель.
- **Streaming.** Если в UI streaming-режим важен (показ промежуточного thinking), это нужно учесть в протоколе с самого начала, иначе придётся переделывать.

## Открытые вопросы

- **Q1.** Можно ли вытащить `mock`-адаптер отдельным PR без полного registry — как минимальная dependency-injection точка в `Orchestrator`?
- **Q2.** Источник pricing-данных для `cost_estimate` — захардкожен в коде, отдельный JSON в репо, или внешний API (LiteLLM, OpenRouter)?
- **Q3.** Streaming в UI — поддерживается сейчас? Если нет — закладывать в адаптер сразу или non-streaming MVP?
- **Q4.** Конфиг секретов адаптеров — env vars, отдельный secret-store, или в `~/.cod-doc/credentials.toml`?
- **Q5.** Внешние адаптеры через JSON-конфиг (Phase 2) — нужны ли вообще, или достаточно встроенных + `mock`?
- **Q6.** Capability mismatch — что делать, если задача требует tool-use, а адаптер `capabilities.tool_use=False` (например, локальная модель без поддержки)? Авто-fallback на другой адаптер или ошибка?
