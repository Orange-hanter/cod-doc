# 20 — Multi-Agent Standup: 2+ агента в одной cod-doc-инстанции

> Категория: 🔵 Архитектура · Риск: высокий · Зависимости: 06-atomic-checkout, 04-run-id, agent_pick (AGT-002), heartbeat

## Контекст: «у меня их двое»

Vibecoder будущего — не один человек за клавиатурой. Это **оркестр агентов**:
- **Planner-agent** — читает backlog, раскидывает по спринтам, пишет планы.
- **Coder-agent** — забирает задачи, пишет код, коммитит, обновляет task_doc.
- **Reviewer-agent** — ловит drift'ы, проверяет acceptance, инициирует approval.
- (Опц.) **PM-agent** — пишет daily diary, шлёт апдейты в Telegram.

Cod-doc уже спроектирован под это:
- `agent_pick` (AGT-002) — атомарный захват следующей ready-задачи **с agent_id**, защита от двойного захвата.
- `task_checkout` (PCA-200) — атомарный `todo → in_progress`, race-safe.
- `run_id` (proposal 04) — каждая мутация тегается run_id, можно ответить «что натворил агент X на прогоне Y».
- `activity_log` (proposal 09) — единый timeline всех мутаций от всех агентов.
- `heartbeat_service` (proposal 02) — агенты пингуют «я жив», мёртвые задачи можно вернуть в `todo`.
- 7-state TaskStatus (proposal 08) — формализует `in_review` / `blocked` / `done`.

**Не закрыто:** нет готового шаблона «2 агента работают в одном проекте без конфликтов и без потери контекста».

## Текущее состояние cod-doc

- `cod_doc/agent/` — оркестратор + LLM, промпты, skills runtime (один на всё).
- Cycle-5 agent profile: 6 тулов (`agent_pick`, `agent_get`, `agent_complete`, `agent_report`, `agent_release`, `agent_capabilities`).
- `agent_id` — свободная строка, передаётся в `agent_pick`. Нет реестра агентов.
- **Нет:** готового паттерна «Planner + Coder», демо-скрипта, набора скиллов для специализированных ролей.

## Предложение

Создать **reference-имплементацию** мульти-агентной работы в `cod_doc/agent/roles/`:

### 4.1. Структура

```
cod_doc/agent/roles/
├── base.py                  # AgentRole ABC: pick → work → complete
├── planner.py               # PlannerAgent: читает backlog, раскидывает по sprints
├── coder.py                 # CoderAgent: забирает task, пишет код (вызывает Claude API)
├── reviewer.py              # ReviewerAgent: проверяет acceptance, инициирует approval
├── coordinator.py           # Coordinator: запускает N агентов в asyncio.gather, синхронизирует
└── demo/
    └── two_coder_race.py    # demo: 2 CoderAgent конкурируют за задачи (atomic checkout спасает)
```

### 4.2. AgentRole ABC

```python
class AgentRole(ABC):
    name: str                          # "planner", "coder", "reviewer"
    description: str                   # для логов
    skills: list[str]                  # какие скиллы подгружаются
    
    @abstractmethod
    async def pick_task(self) -> TaskCard | None:
        """Атомарно забрать следующую задачу, подходящую роли."""
        ...
    
    @abstractmethod
    async def work(self, task: TaskCard) -> WorkResult:
        """Выполнить работу. LLM вызывается внутри."""
        ...
    
    @abstractmethod
    async def finalize(self, task: TaskCard, result: WorkResult) -> None:
        """agent_complete / agent_report / agent_release."""
        ...
```

### 4.3. Coordinator

```python
class Coordinator:
    def __init__(self, agents: list[AgentRole], max_parallel: int = 2):
        self.agents = agents
        self.semaphore = asyncio.Semaphore(max_parallel)
    
    async def run(self, max_iterations: int = 100) -> None:
        """Гоняет агентов в parallel с rate-limit. Останавливается
        когда у всех agent_pick возвращает None (backlog пуст)."""
        iteration = 0
        while iteration < max_iterations:
            tasks = []
            for agent in self.agents:
                tasks.append(self._run_one(agent))
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            if all(r is None for r in results):
                break  # all backlogs empty
            iteration += 1
    
    async def _run_one(self, agent: AgentRole) -> None:
        async with self.semaphore:
            task = await agent.pick_task()
            if task is None:
                return
            async with run_scope(run_id=generate_run_id(), agent_id=agent.name):
                result = await agent.work(task)
                await agent.finalize(task, result)
```

### 4.4. Demo: two_coder_race

Самый простой сценарий, который показывает ценность cod-doc для multi-agent:
1. Создать в БД 10 задач типа `task` со status=`todo` и acceptance=«напиши функцию foo(N)».
2. Запустить 2 CoderAgent в parallel.
3. Смотреть, как они **атомарно** разбирают задачи (через `task_checkout`).
4. Каждый кодер вызывает Claude API → пишет функцию в `tmp/coder_<id>/foo.py`.
5. По завершению → `task_complete` + `activity_log` event.
6. Финальный отчёт: «10 задач, 2 агента, 0 race conditions, 100% completion».

### 4.5. Heartbeat-протокол между агентами (proposal 02)

- **Перед** `agent_pick` — каждый агент пингует `heartbeat_service` с `agent_id=role.name` и `last_seen=now`.
- **Если** `task_checkout` на задаче висит >10 минут без heartbeat → автоматический `task_release` + return в `todo`.
- **Coordinator** логирует переключения, чтобы можно было replay'ить (run_id).

### 4.6. Agent registry

```python
# cod_doc/agent/registry.py
class AgentRegistry:
    """Thread-safe реестр живых агентов в данной cod-doc инстанции."""
    def register(self, agent: AgentRole) -> None: ...
    def list_active(self, project: str) -> list[AgentInfo]: ...
    def heartbeat(self, agent_id: str) -> None: ...
    def detect_dead(self, ttl_seconds: int = 600) -> list[str]: ...
```

MCP-тул:
```
agent_registry_list(project?) -> list[AgentInfo]
agent_registry_heartbeat(agent_id) -> HeartbeatResult
agent_registry_detect_dead(ttl_seconds=600) -> list[str]
```

## Эффект

- **Демо за 5 минут.** Запустил `coordinator.run()` с 2 CoderAgent → получил 10 решённых задач + красивый лог.
- **Прямая польза для vibecoder'а.** «У меня есть Planner на Claude и Coder на локальной ollama-модели, они не мешают друг другу».
- **Масштабируется на ReviewerAgent.** ReviewerAgent забирает `in_review` задачи, проверяет acceptance, ставит `done` или возвращает `in_progress` с комментарием.
- **Audit-trail из коробки.** `activity_for_run` + `commit_link_service` показывают, что каждый агент делал.

## Зависимости

| Proposal / компонент | Нужно для |
|---|---|
| `06-atomic-checkout` (PCA-200) | защита от двойного захвата |
| `04-run-id` (PCA-911) | аудит по прогонам |
| `09-activity-log` (PCA-912) | timeline всех мутаций |
| `02-heartbeat` (proposal 02) | детект мёртвых агентов |
| Cycle-5 agent profile (AGT-001..007) | 6-тул surface |
| `08-status-taxonomy` | формализация `in_review` / `blocked` |

## Структура

```
cod_doc/agent/roles/
├── base.py
├── planner.py
├── coder.py
├── reviewer.py
├── coordinator.py
└── demo/
    ├── two_coder_race.py
    └── planner_coder_pair.py
cod_doc/agent/registry.py
cod_doc/mcp/tools/
└── agent_registry_tools.py
tests/agent/
└── test_multi_agent.py
```

## Риски и митигация

| Риск | Митигация |
|---|---|
| Race condition между 2 агентами | Уже закрыто `task_checkout` (PCA-200). Demo `two_coder_race` это явно тестирует. |
| Агент зависает с захваченной задачей | Heartbeat + TTL → `task_release` автоматически. |
| Бесконечный цикл (агенты не заканчивают) | `max_iterations` в Coordinator + мониторинг `activity_log` на `kind=error`. |
| Два агента с одним `agent_id` | `AgentRegistry.register` идемпотентен, но логирует warning при коллизии. |
| LLM-cost runaway (агенты спамят Claude API) | Rate-limit через semaphore + per-agent budget в `model_catalog`. |
| Multi-project confusion (агент работает в проекте A, Coordinator думает что в B) | `project` — обязательный параметр в `agent_pick`, валидируется в Coordinator. |

## Acceptance criteria

1. `cod_doc/agent/roles/base.py` существует, ABC задокументирован, типизирован.
2. `coordinator.run()` запускает N агентов в parallel, останавливается при пустом backlog.
3. `demo/two_coder_race.py` запускается end-to-end: 10 задач → 2 CoderAgent → 0 race, 100% complete.
4. `AgentRegistry` тестируется на `detect_dead` (TTL=0 → все «мёртвые»).
5. Heartbeat от CoderAgent виден в `activity_log` с `event_kind='heartbeat'`.
6. `run_id` проставлен на всех мутациях от агентов.

## Альтернативы

- **Один монолитный агент** — работает, но контекст раздувается, нет специализации.
- **Out-of-process workers (Celery/RQ)** — overkill для документ-центричной задачи, добавляет инфраструктуру.
- **LangGraph / AutoGen** — внешние фреймворки, не интегрированы с cod-doc API. Наш подход — cod-doc native.

## Источники

- Paperclip [agent profile](https://github.com/paperclipai/paperclip) + heartbeat-протокол — прямой референс.
- AutoGen, LangGraph — референс multi-agent pattern (но с over-engineering для нашего случая).
- `agent_pick` AGT-002 (cycle-5) — `agent_id` уже есть, нужна обвязка.
