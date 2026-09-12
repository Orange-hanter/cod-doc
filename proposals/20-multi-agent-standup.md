# 20 — Multi-Agent Standup: 2+ agents in one cod-doc instance

> Category: 🔵 Architecture · Risk: high · Dependencies: 06-atomic-checkout, 04-run-id, agent_pick (AGT-002), heartbeat

## Context: "I have two of them"

The vibecoder of the future is not one person at a keyboard. It is an **orchestra of agents**:
- **Planner-agent** — reads the backlog, distributes across sprints, writes plans.
- **Coder-agent** — takes tasks, writes code, commits, updates task_doc.
- **Reviewer-agent** — catches drifts, checks acceptance, initiates approval.
- (Opt.) **PM-agent** — writes the daily diary, sends updates to Telegram.

cod-doc is already designed for this:
- `agent_pick` (AGT-002) — atomic acquisition of the next ready-task **with agent_id**, protection from double acquisition.
- `task_checkout` (PCA-200) — atomic `todo → in_progress`, race-safe.
- `run_id` (proposal 04) — each mutation is tagged with run_id, you can answer "what did agent X do in run Y".
- `activity_log` (proposal 09) — a unified timeline of all mutations from all agents.
- `heartbeat_service` (proposal 02) — agents ping "I'm alive", dead tasks can be returned to `todo`.
- 7-state TaskStatus (proposal 08) — formalizes `in_review` / `blocked` / `done`.

**Not closed:** there is no ready template "2 agents work in the same project without conflicts and without losing context".

## Current state of cod-doc

- `cod_doc/agent/` — orchestrator + LLM, prompts, skills runtime (one for all).
- Cycle-5 agent profile: 6 tools (`agent_pick`, `agent_get`, `agent_complete`, `agent_report`, `agent_release`, `agent_capabilities`).
- `agent_id` — a free string, passed to `agent_pick`. No agent registry.
- **Missing:** a ready "Planner + Coder" pattern, a demo script, a set of skills for specialized roles.

## Proposal

Create a **reference implementation** of multi-agent work in `cod_doc/agent/roles/`:

### 4.1. Structure

```
cod_doc/agent/roles/
├── base.py                  # AgentRole ABC: pick → work → complete
├── planner.py               # PlannerAgent: reads backlog, distributes across sprints
├── coder.py                 # CoderAgent: takes a task, writes code (calls Claude API)
├── reviewer.py              # ReviewerAgent: checks acceptance, initiates approval
├── coordinator.py           # Coordinator: runs N agents in asyncio.gather, synchronizes
└── demo/
    └── two_coder_race.py    # demo: 2 CoderAgents compete for tasks (atomic checkout saves)
```

### 4.2. AgentRole ABC

```python
class AgentRole(ABC):
    name: str                          # "planner", "coder", "reviewer"
    description: str                   # for logs
    skills: list[str]                  # which skills are loaded
    
    @abstractmethod
    async def pick_task(self) -> TaskCard | None:
        """Atomically take the next task suitable for the role."""
        ...
    
    @abstractmethod
    async def work(self, task: TaskCard) -> WorkResult:
        """Do the work. LLM is called inside."""
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
        """Runs agents in parallel with rate-limit. Stops
        when all agent_pick return None (backlog empty)."""
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

The simplest scenario that shows the value of cod-doc for multi-agent:
1. Create 10 tasks of type `task` in the DB with status=`todo` and acceptance="write function foo(N)".
2. Run 2 CoderAgents in parallel.
3. Watch them **atomically** take tasks (via `task_checkout`).
4. Each coder calls the Claude API → writes the function to `tmp/coder_<id>/foo.py`.
5. On completion → `task_complete` + `activity_log` event.
6. Final report: "10 tasks, 2 agents, 0 race conditions, 100% completion".

### 4.5. Heartbeat protocol between agents (proposal 02)

- **Before** `agent_pick` — each agent pings `heartbeat_service` with `agent_id=role.name` and `last_seen=now`.
- **If** a `task_checkout` on a task hangs >10 minutes without a heartbeat → automatic `task_release` + return to `todo`.
- **Coordinator** logs switches so they can be replayed (run_id).

### 4.6. Agent registry

```python
# cod_doc/agent/registry.py
class AgentRegistry:
    """Thread-safe registry of live agents in this cod-doc instance."""
    def register(self, agent: AgentRole) -> None: ...
    def list_active(self, project: str) -> list[AgentInfo]: ...
    def heartbeat(self, agent_id: str) -> None: ...
    def detect_dead(self, ttl_seconds: int = 600) -> list[str]: ...
```

MCP-tool:
```
agent_registry_list(project?) -> list[AgentInfo]
agent_registry_heartbeat(agent_id) -> HeartbeatResult
agent_registry_detect_dead(ttl_seconds=600) -> list[str]
```

## Effect

- **A demo in 5 minutes.** Launched `coordinator.run()` with 2 CoderAgents → got 10 solved tasks + a nice log.
- **Direct value for the vibecoder.** "I have a Planner on Claude and a Coder on a local ollama-model, they don't interfere with each other".
- **Scales to ReviewerAgent.** ReviewerAgent takes `in_review` tasks, checks acceptance, sets `done` or returns `in_progress` with a comment.
- **Audit-trail out of the box.** `activity_for_run` + `commit_link_service` show what each agent did.

## Dependencies

| Proposal / component | Needed for |
|---|---|
| `06-atomic-checkout` (PCA-200) | protection from double acquisition |
| `04-run-id` (PCA-911) | audit per run |
| `09-activity-log` (PCA-912) | timeline of all mutations |
| `02-heartbeat` (proposal 02) | dead agent detection |
| Cycle-5 agent profile (AGT-001..007) | 6-tool surface |
| `08-status-taxonomy` | formalization of `in_review` / `blocked` |

## Structure

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

## Risks and mitigation

| Risk | Mitigation |
|---|---|
| Race condition between 2 agents | Already closed by `task_checkout` (PCA-200). The `two_coder_race` demo tests this explicitly. |
| Agent hangs with a taken task | Heartbeat + TTL → `task_release` automatically. |
| Infinite loop (agents don't finish) | `max_iterations` in Coordinator + monitoring of `activity_log` for `kind=error`. |
| Two agents with the same `agent_id` | `AgentRegistry.register` is idempotent, but logs a warning on collision. |
| LLM-cost runaway (agents spam the Claude API) | Rate-limit via semaphore + per-agent budget in `model_catalog`. |
| Multi-project confusion (agent works in project A, Coordinator thinks B) | `project` — a mandatory parameter in `agent_pick`, validated in Coordinator. |

## Acceptance criteria

1. `cod_doc/agent/roles/base.py` exists, ABC is documented, typed.
2. `coordinator.run()` runs N agents in parallel, stops on empty backlog.
3. `demo/two_coder_race.py` runs end-to-end: 10 tasks → 2 CoderAgents → 0 races, 100% complete.
4. `AgentRegistry` is tested on `detect_dead` (TTL=0 → all "dead").
5. A heartbeat from CoderAgent is visible in `activity_log` with `event_kind='heartbeat'`.
6. `run_id` is set on all mutations from agents.

## Alternatives

- **One monolithic agent** — works, but context bloats, no specialization.
- **Out-of-process workers (Celery/RQ)** — overkill for a document-centric task, adds infrastructure.
- **LangGraph / AutoGen** — external frameworks, not integrated with cod-doc API. Our approach — cod-doc native.

## Sources

- Paperclip [agent profile](https://github.com/paperclipai/paperclip) + heartbeat protocol — direct reference.
- AutoGen, LangGraph — multi-agent pattern reference (but with over-engineering for our case).
- `agent_pick` AGT-002 (cycle-5) — `agent_id` already exists, the wrapping is needed.
