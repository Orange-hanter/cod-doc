---
type: roadmap-index
scope: cod-doc-roadmap
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-05
last_updated: 2026-09-02
audience: [contributors, agents]
related_docs:
  - ../MASTER.md
  - task-graph.md
  - ../../../proposals/README.md
  - ../audit/2026-07-29-state-of-the-project.md
  - ../../adoption-playbook.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
---

# COD-DOC — Roadmap (canonical index)

> Единая точка приоритизации поверх всех task-планов, RFC и аудитов.
> Пересобран 2026-07-29 после [state-of-the-project audit](../audit/2026-07-29-state-of-the-project.md):
> Трек A (стабилизация) закрыт 12/13, и главный дефицит сместился с качества
> кода на **отсутствие использования**.

## Правило источника истины

1. **БД приложения (`.cod-doc/state.db`) — source of truth для трекаемых задач.**
   Статус задачи определяется записью в БД, а не markdown.
2. **Код — арбитр при расхождении.** Если БД и markdown спорят — смотрим, что
   реально реализовано (`file:line` + тест), и приводим оба к нему.
3. **markdown Progress Overview — вторичен**, обновляется из БД/кода.

Процедура сверки формализована в skill
[`ground-truth-reconcile`](../../../cod_doc/skills/ground-truth-reconcile/SKILL.md).

## Navigation

- [System MASTER](../MASTER.md) · [Task graph](task-graph.md)
- [Proposals (RFC 01–21)](../../../proposals/README.md)
- [State-of-the-project audit 2026-07-29](../audit/2026-07-29-state-of-the-project.md)
- [Adoption playbook](../../adoption-playbook.md)

## Где мы находимся

Проверено прогоном 2026-07-29, не переписано из прошлых отчётов:

| Измерение | Состояние |
|---|---|
| Тесты / линт / типы | 1356 passed · ruff clean · mypy clean (287 файлов) |
| Drift | 106 документов `in_sync`, 0 расхождений |
| Планы | 5 планов, 0 issues в `plan audit` |
| Задачи | 179 `done` · 25 `pending` · 5 `cancelled` |
| Поверхность | 103 MCP-тула · 12 скиллов · 6 ADR · 25 stories |
| **Внешних пользователей** | **0 проектов, кроме самого cod-doc** |

Последняя строка — и есть новый приоритет.

## Ground-truth состояние планов

| План | Статус | Примечание |
|---|---|---|
| [paperclip-adoption](paperclip-adoption-task-plan.md) (RFC 01–15) | ✅ done | 94 done / 2 cancelled |
| [adr-system](adr-system-task-plan.md) | ✅ done | 8/8 |
| [observability-and-indexing](observability-and-indexing-task-plan.md) | ✅ done | 8/8 |
| [refactor-large-files](refactor-large-files-task-plan.md) | ✅ done | подтверждён STB-020 |
| [cod-doc bootstrap](cod-doc-task-plan.md) | ✅ done | 58/58; COD-042/043/052 закрыты |
| [web-frontend](web-frontend-task-plan.md) | ✅ done | WEB-031/042 закрыты (STB-003/004) |
| [audit-followups](audit-followups-task-plan.md) | ✅ done | закрыт STB-021 |
| [agent-tools-completion](agent-tools-completion-task-plan.md) | ✅ done | закрыт STB-001 |
| [stabilization-2026-06](../audit/2026-07-29-state-of-the-project.md) | 🔄 11 done / 1 cancelled | STB-012 → cancelled (re-scoped как ADO-013); открыт STB-023 |
| **adoption-2026-08** | 🔄 66/82 *(2026-09-02)* | Треки C+D+E; пилоты переназначены на ZAIrgRush и Orakul ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)). Секции: C 23/26, D 26/37, E 17/19 |
| RFC 16–21 (hackathon-track) | ❌ отбракованы 2026-08-29 | ADO-056: ни одна не закрывает спрос M2; пометки в [proposals/README.md](../../../proposals/README.md) |

## Смена приоритета: почему Adoption вперёд фич

Сверка 2026-07-29 дала четыре находки, указывающие в одну сторону:

- **F2** — `repo_file`/`repo_symbol` были пусты: собственная capability
  (OBI-030) ни разу не запускалась на собственном проекте.
- **F5** — `~/.cod-doc/config.yaml` содержит `model: test/model` и один
  зарегистрированный проект — временную директорию pytest.
- **F6** — в корне нет README: проект нечем объяснить за 30 секунд.
- **F7** — **`doc export` повреждает документ** (склейка preamble с первым
  заголовком, потеря H1, подмена `type`). Не всплывало, потому что этой
  дорогой ни разу не прошли.

F7 — решающий аргумент. «Markdown — только проекция, генерируется из БД при
export» — центральное обещание [VISION §2](../VISION.md), и оно сейчас не
работает. Догфудинг нашёл за вечер то, чего не нашли 1356 тестов, два
LLM-ревью и три аудита: все они смотрели на код, а не пользовались им.

Инструмент, не заведённый ни на одном реальном проекте, **не проверен там,
где он ломается**. Поэтому:

> **Решение: Трек C (Adoption) приоритетнее Трека B (hackathon-фичи).**
> **ADO-010 (export) шёл вперёд обоих** — экспортом нельзя было пользоваться
> на пилотах, пока он портил документы. Закрыт 2026-08-25.

## Смена пилотов: программа Symbiosis (2026-08-25)

Решением владельца пилоты переназначены с «спокойных» репозиториев на два
живых, дающих обратный поток данных
([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)):

- **ZAIrgRush** (пилот №1, ADO-016) — мульти-агентная петля разработки со
  свободным экспериментальным слотом E5 «Дрейф docs↔code»; cod-doc заходит
  туда как вариант C этого эксперимента.
- **Orakul / ai-review** (пилот №2, ADO-017) — LLM-ревью PR без БД; cod-doc
  становится хранилищем находок (дедуп, стабильность) и детерминированным
  drift-гейтом, которого у них нет.

Симбиоз двунаправленный: cod-doc отдаёт спеки/ADR/контекст, пилоты
возвращают findings, коммиты и измерения. ADO-003/ADO-004 закрыты как
`cancelled` с указанием причины; STB-012 закрыт как `cancelled`
(re-scoped в ADO-013). ADO-010 разбит на два этапа (guard сейчас,
byte-identical round-trip — перед первым `doc export` наружу).
Декомпозиция программы — секция E плана `adoption-2026-08` (SYM-001…011).

## Треки

Все задачи заведены в БД как план **`adoption-2026-08`** (на 2026-09-02 — 82
задачи: C — 26, D — 37, E — 19; таблицы ниже перечисляют не весь состав, а
опорные пункты треков):
`cod-doc plan ready adoption-2026-08 -p cod-doc`.

### Трек C — Adoption (приоритет)

Цель: cod-doc используется на ≥ 2 реальных проектах владельца, и обратная
связь оттуда правит бэклог. Сценарии по типам проектов —
[adoption-playbook.md](../../adoption-playbook.md).

| ID | Задача | Приоритет | Блокируется |
|---|---|---|---|
| **ADO-001** | Починить `~/.cod-doc/config.yaml`: выкинуть `integration-test`, прописать реальную модель и ключ (F5) | critical | — |
| **ADO-002** | Корневой `README.md`: что это, кому, quick start, скриншот Web UI (F6) | high | — |
| ~~ADO-003~~ | ~~Пилот №1: Mushrooms Shuchin~~ — cancelled, пилот переназначен (RFC 22) | — | — |
| ~~ADO-004~~ | ~~Пилот №2: yana-reconciliation~~ — cancelled, пилот переназначен (RFC 22) | — | — |
| **ADO-016** | Пилот №1: `ZAIrgRush` (петля агентов, 28 root-доков, слот E5) | high | SYM-001, SYM-002, SYM-004, ADO-015, ADO-010 |
| **ADO-017** | Пилот №2: `Orakul` (371 док, Diátaxis, ai-review) | high | ADO-016 |
| **ADO-005** | Friction-лог: неделя в пилотах, ≥ 10 наблюдений из живой работы | high | ADO-016, ADO-017 |
| **ADO-006** | Закрыть top-3 находки friction-лога | high | ADO-005 |
| **ADO-007** | Routine `doc_drift` на пилотах — автопроверки вне cod-doc | medium | ADO-016 |

### Трек D — Остаточный долг (фоном)

| ID | Задача | Приоритет | Блокируется |
|---|---|---|---|
| ~~**ADO-010**~~ | ~~`doc export` повреждает документ (F7)~~ — **done 2026-08-25**: оба этапа. Guard (`--dry-run`, отказ писать поверх правки руками и в чужой checkout, `--force-write`) + byte-identical round-trip (71/71 дока `docs/`), миграция `0025_projection_fidelity` | — | — |
| **ADO-011** | Route drift: синхронизировать `capabilities/web-frontend.md §3` — 66 undocumented (F3) | medium | — |
| **ADO-012** | Поставить `audit --web-routes` в CI как advisory-шаг, чтобы дрейф не копился | medium | ADO-011 |
| **ADO-013** | Пересмотреть RFC #21: Tier-1 потерял основание после STB-010 (STB-012 закрыт `cancelled`, остаток живёт здесь) | medium | — |
| **ADO-015** | Enum `DocumentType`: + `design/audit/journal/plan/analysis/research/capability/audit-report` — 5 из 6 типов ZAIrgRush молча становятся `module-spec`; тихую подмену → предупреждение | medium | — |
| **ADO-014** | Актуализировать `capabilities/project-bootstrap.md`: `project new` vs `project add` + `init` | low | — |
| **STB-023** | `activity_subscribe` (SSE). Держать закрытым, пока не появится реальный event-driven сценарий | low | — |

Находки сверки 2026-09-02 (планирование M5) — очередь спринта, контракты в БД
(`task_doc key="contract"`):

| ID | Задача | Приоритет | Блокируется |
|---|---|---|---|
| **ADO-070** | CI на main не был зелёным ни разу с 2026-05-06: `alembic` не на PATH, гейт декоративный | **critical** | — |
| **ADO-069** | Красный гейт локально: `test_post_findings_invalid_payload_version` отстал от адаптера v2 (SYM-009) | **critical** | — |
| **ADO-068** | Тесты пишут в реальный `~/.cod-doc/config.yaml`: `CONFIG_DIR` заморожен на импорте; регресс F5/ADO-001 | **critical** | — |
| **ADO-066** | `capabilities`/`tool_search`/`tools_diff` падают под живым MCP-сервером (`asyncio.run` в running loop) | **critical** | — |
| **ADO-067** | `task_update`: description/acceptance/priority недоступны в MCP и CLI — агент не может грумить бэклог | high | — |
| **ADO-044** | Провенанс мутаций: run_id + audit_log + actor_kind — реализовать или снять контракт (консолидация ADO-043 + ADO-051) | medium | — |
| ~~ADO-043~~ | ~~audit_log мёртвая~~ — `cancelled` 2026-09-02, свёрнута в ADO-044 | — | — |
| ~~ADO-051~~ | ~~actor_kind startswith-эвристика~~ — `cancelled` 2026-09-02, свёрнута в ADO-044 | — | — |

### Трек E — Symbiosis ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md))

Фаза 0 (самопочинка, блокирует пилоты) → Фазы 1–5 (hub, findings, петля,
ai-review, кросс-проектность). Полный план:
`cod-doc plan ready adoption-2026-08 -p cod-doc`.

| ID | Задача | Приоритет | Блокируется |
|---|---|---|---|
| **SYM-001** | B1: `project add/init` создаёт БД + починить прескрипт онбординга в 3 доках | **critical** | — |
| **SYM-002** | SQLite hardening: WAL + busy_timeout + synchronous; dialect-guard FTS5 | high | — |
| **SYM-003** | Безопасность: bind 127.0.0.1 по умолчанию + loopback-гейт `POST /settings` | high | — |
| **SYM-004** | `import docs --exclude` | medium | — |
| **SYM-005** | Фаза 1: hub.db + миграции 0026/0027 + finding_service | high | SYM-002 |
| **SYM-006** | Фаза 1: реестр ingest-адаптеров + CLI ingest/ctx + api/v1 + MCP finding_*/ctx_* | high | SYM-005 |
| **SYM-007** | ADR-мост ZAIrgRush: 13 ADR → adr-система + decisions.jsonl | medium | ADO-016 |
| **SYM-008** | Фаза 2: ctx docs + патч петли ZAIrgRush (E5 вариант C) + git-трейлеры | medium | SYM-006, ADO-016 |
| **SYM-009** | Фаза 3: ingest ai_review pull-моделью + upstream-PR slimFinding + finding stability | medium | SYM-006 |
| **SYM-010** | Фаза 4a: ctx drift → PR-комментарий (гейт ссылок/frontmatter Orakul) | medium | SYM-009, ADO-017 |
| **SYM-011** | Фаза 5: кросс-проектный поиск + `[[doc:slug:key]]` + фикс Chroma L3 + `agent_pick --projects` | low | SYM-005 |

### Трек B — Feature-трек (hackathon RFC, после C)

❌ **Отбракован целиком 2026-08-29 (ADO-056, M3-кикофф).** Сверка секций
«Текущее состояние» RFC 16–21 по коду + сопоставление с friction-логом M2
(ADO-005, записи #8/#10/#11/#14) показали: ни одна RFC не закрывает
зафиксированный спрос. Пометки — в
[proposals/README.md](../../../proposals/README.md). Порядок B-MVP/B-Scale
ниже — исторический контекст.

- ~~**B-MVP:** RFC 18 Vibecoder's Diary → RFC 19 Context-Scout → RFC 17 Living Specification.~~
- ~~**B-Scale:** RFC 16 AI-Pair-Hacker → RFC 20 Multi-Agent Standup.~~

Каждая RFC декомпозируется в отдельный план (`plan_create`) перед стартом —
см. skill [`rfc-authoring`](../../../cod_doc/skills/rfc-authoring/SKILL.md).

> **Замечание о перекосе.** 21 RFC против 5 планов: предложений написано
> вчетверо больше, чем фронтов работ. ~~Перед стартом Трека B стоит
> перечитать RFC 16–20 и отбраковать те, чья секция «Текущее состояние»
> устарела — как это случилось с RFC #21.~~ Выполнено 2026-08-29 (ADO-056):
> отбракованы все шесть.

## Три ближайших милстоуна

> **Активный спринт — M5 «Гейт, которому можно верить + симбиоз в бою»:**
> [sprint-m5-trustworthy-gate.md](sprint-m5-trustworthy-gate.md) — очередь без
> дат: 1) ADO-070 CI не зелёный ни разу с 2026-05-06, 2) ADO-069 единственный
> красный тест локально, 3) ADO-068 тесты пишут в реальный конфиг (регресс F5),
> 4) ADO-066 `capabilities`/`tool_search`/`tools_diff` падают под живым сервером,
> 5) ADO-067 `task_update` в MCP/CLI, 6) ADO-065 разбор E5-C, 7) SYM-010
> drift-гейт Orakul, 8) ADO-044 провенанс мутаций. Контракт каждой задачи —
> в БД, `task_doc key="contract"`.
> **M4 «Доказательство ценности» закрыт 2026-09-02:**
> [sprint-m4-proof-of-value.md](sprint-m4-proof-of-value.md) — audit
> [2026-09-02-sprint-m4-proof-of-value.md](../audit/2026-09-02-sprint-m4-proof-of-value.md);
> все четыре пункта очереди выполнены (ADO-062/064, ADO-063, ADO-040, SYM-009),
> E5-C доказан боевым прогоном за $0.98 с вердиктом «масштабируем».
> **M3 «friction-log leftovers» закрыт досрочно 2026-08-30:**
> [sprint-2026-08-30-m3-friction-log.md](sprint-2026-08-30-m3-friction-log.md)
> — audit [2026-09-06-sprint-m3-friction.md](../audit/2026-09-06-sprint-m3-friction.md);
> friction-лог обнулён (#8/#10/#11/#14 → ADO-058…061), стретчи ADO-039 и
> SYM-008 выполнены.
> Спринт H1 закрыт досрочно 2026-08-29:
> [sprint-2026-08-29-hardening-m3-kickoff.md](sprint-2026-08-29-hardening-m3-kickoff.md)
> — audit [2026-09-05-sprint-h1-hardening.md](../audit/2026-09-05-sprint-h1-hardening.md).
> M2 закрыт досрочно 2026-08-29: [sprint-2026-08-28-m2-feedback-loop.md](sprint-2026-08-28-m2-feedback-loop.md)
> — audit [2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md).
> Предыдущие спринты:
> [sprint-2026-08-27-m1-phase1.md](sprint-2026-08-27-m1-phase1.md)
> (закрыт досрочно 2026-08-28, audit в `docs/system/audit/`).

### M1 — «Пилот работает» *(SYM-001..004, ADO-010 этап 1, 001, 002, 015, 016, 017; ~2–3 недели)*

Cod-doc заведён на двух реальных проектах и отдаёт контекст, а экспорт не
портит документы.

**Готово, когда:**
- [x] **ADO-010 закрыт целиком** (2026-08-25, а не в два захода): guard (`--dry-run`, `--force-write`, отказ писать поверх правки руками и в чужой checkout) **и** byte-identical round-trip — 71/71 дока `docs/` (миграция `0025_projection_fidelity`).
- [x] **SYM-001 закрыт:** `project add/init` создаёт БД — онбординг работает по документации.
- [x] `cod-doc project list` не содержит `integration-test`; конфиг указывает на рабочую модель *(ADO-001, 2026-08-25)*.
- [x] В корне есть `README.md`, объясняющий проект без чтения `docs/` (2026-08-25).
- [x] Два проекта проходят все 5 критериев «проект заведён» из skill `project-onboarding` — ZAIrgRush (ADO-016, 2026-08-28) и Orakul (ADO-017, 2026-08-28).
- [x] `cod-doc search` на каждом пилоте находит документы по доменному термину («swarm» в ZAIrgRush, «pulse» → 18 хитов в Orakul; оба — после `--reindex`, friction #5).

**Риск:** импорт затащит архивный/вендорный markdown → шум в FTS.
**Митигация:** `--dry-run` обязателен; решение по `Архив/` принимается до импорта (зафиксировано в скилле и playbook'е).

**Почему ADO-010 первым:** пилоты — это чужие рабочие репозитории. Ставить
туда инструмент, который может испортить markdown, нельзя.

### M2 — «Обратная связь встроена» *(ADO-005..007, 011, 012; ~3 недели)*

Пилоты прожиты, найденное — починено, дрейф не копится.

**Готово, когда:**
- [x] Есть friction-лог ≥ 10 наблюдений из реальной работы, не из чтения кода. *(ADO-005, 2026-08-28: 14 записей)*
- [x] Top-3 находки закрыты задачами в БД, а не заметками. *(ADO-006, 2026-08-28: top-1 ADO-023 + ADO-030/031/032/033)*
- [x] `capabilities/web-frontend.md §3` совпадает с живыми роутами; `audit --web-routes` в CI. *(ADO-011 `635b004`, ADO-012 `cf328e7`, advisory-job)*
- [x] Routine `doc_drift` отрабатывает на пилоте по расписанию. *(ADO-007/ADO-024: OS-cron `*/15` tick, реальный cron-fire подтверждён)*
- [x] Написан audit-отчёт по итогам пилота (skill `audit-cadence`). *([2026-09-11-sprint-m2-feedback-loop.md](../audit/2026-09-11-sprint-m2-feedback-loop.md), 2026-08-29)*

**Риск:** пилоты «заведены и заброшены» — трекинг переедет обратно в голову.
**Митигация:** M2 не закрывается без friction-лога из живой работы.

### M3 — «Первая фича по спросу» *(остатки friction-лога M2; ~2–3 недели)*

Одна RFC из hackathon-трека реализована — **выбранная по friction-логу M2**,
а не по порядку из README. *(2026-08-29: RFC не выбрана — см. резолюцию;
M3 перенацелён решением владельца.)*

> **Резолюция кикоффа 2026-08-29 (ADO-056): «отбракованы все».** Анализ
> RFC 16–21 против friction-лога M2 (ADO-005 #8/#10/#11/#14) и findings
> контракт-аудита (2026-08-29-contract-audit.md) не нашёл ни одной RFC,
> закрывающей зафиксированный спрос; секции «Текущее состояние» всех шести
> перепроверены по коду 2026-08-29 (подробности — task-doc 'acceptance'
> в ADO-056). Пометки об отбраковке — в `proposals/README.md` (ADO-013
> закрыт). `plan_create` для Трека B не выполняется.
>
> **Решение владельца 2026-08-29: M3 перенацелён на остатки friction-лога
> M2** — #8 dry-run лимит 50 строк, #10 битый path у `*.txt`, #11
> недокументированный скип скрытых каталогов, #14 маппинг `diataxis`/`quadrant`
> в DocumentType. Это подтверждённый спрос вместо hackathon-track.

**Готово, когда:**
- [x] Выбор RFC обоснован ссылками на конкретные наблюдения M2. *(исход: «отбракованы все», обоснование через ADO-005 #8/#10/#11/#14 + contract-audit)*
- [x] Секция «Текущее состояние» выбранной RFC перепроверена по коду. *(выбранной нет; сверены все шесть, 2026-08-29)*
- [x] Остатки friction-лога M2 (#8/#10/#11/#14) заведены задачами и закрыты. *(ADO-058…061, 2026-08-30; friction-лог обнулён)*
- [x] Каждый фикс проверен на пилотном корпусе Orakul (405 доков), не только в тестах. *(ADO-062, 2026-08-30: orakul перерегистрирован; dry-run `--limit 0` — 53 кандидата полным списком, счётчик hidden-dirs «(4): .claude, .cursor, .github, .graphify», чужие `type:` дают warning. Побочная находка: 405 stale_export — проекции Orakul не переэкспортировались после миграций, отдельное решение владельца.)*
- [x] Остальные RFC 16–20 пересмотрены: устаревшие помечены в `proposals/README.md`. *(16–21, 2026-08-29)*

**Риск:** соблазн начать с RFC 18 просто потому, что она первая в списке.
**Митигация:** первый чек-пункт — обоснование выбора данными M2.

### M4 — «Доказательство ценности + разбор долга» *(очередь без дат)*

Инфраструктура готова — спрос надо подтвердить артефактом, а не тестами.
*(2026-08-30: решение владельца — код пишет AI-агент, поэтому окна/сроки
не планируются; спринт = упорядоченная очередь с критерием выхода.)*

**Очередь (порядок = приоритет):**
1. Перерегистрация Orakul + верификация фиксов M3 на корпусе 405 доков
   (ADO-062, high) — закрывает открытый чек M3.
2. E5-C в бою: `doc_context=executor` на реальной задаче ZAIrgRush +
   маппинг .py→doc-пути + замер по RFC 22 §3.4 + решение
   «масштабируем/выключаем» артефактом (ADO-063, critical).
3. ADO-040 — единый write-path wrapper + emit во всех write-сервисах
   (high, единственный high секции D).
4. SYM-009 — ingest ai_review pull-моделью + upstream-PR slimFinding
   (medium; внешний риск — PR в ai-reviewer).

**Готово, когда:**
- [x] Orakul зарегистрирован; чек M3 «проверено на корпусе Orakul» закрыт. *(ADO-062, 405/405 in_sync)*
- [x] Решение по E5-C зафиксировано артефактом (ADR/findings + метрики). *(ADO-063: вердикт «масштабируем», $0.98, s5dc done с 1 итерации)*
- [x] ADO-040 done: write-path wrapper + 9 сервисов эмитят; ошибка emit видна. *(`3b2662b`)*
- [x] Audit-отчёт M4 (active, в БД), ROADMAP обновлён. *([2026-09-02-sprint-m4-proof-of-value.md](../audit/2026-09-02-sprint-m4-proof-of-value.md))*

**Риск:** E5-C покажет пустые блоки без маппинга путей; upstream-PR
может зависнуть (SYM-009 переносится, спринт не блокируется). *(Не реализовался:
маппинг путей включён в ADO-063, PR ai-reviewer#5 открыт.)*

### M5 — «Гейт, которому можно верить + симбиоз в бою» *(очередь без дат)*

Спринт задан не остатком бэклога, а шестью находками сверки 2026-09-02 (F1–F6
audit-отчёта M4). Общий сюжет: **тест-окружение и боевое разошлись в обе
стороны** — тесты не видят боевых дефектов и при этом пишут в боевое состояние,
а гейт, который должен был это ловить, не работает и не проверяется.

**Очередь (порядок = приоритет):**
1. ADO-070 (critical) — CI на main не был зелёным ни разу с 2026-05-06
   (`alembic` не на PATH); пункт «гейты зелёные» в DoD M1…M4 проверялся
   только локальным прогоном.
2. ADO-069 (critical) — единственный красный тест локально: v2-кейс отстал
   от адаптера, приехавшего тем же коммитом (SYM-009). Вместе с п.1 даёт
   первый зелёный прогон.
3. ADO-068 (critical) — тесты пишут в реальный `~/.cod-doc/config.yaml`;
   `CONFIG_DIR` заморожен на импорте; регресс F5 (ADO-001 прожил 5 дней).
4. ADO-066 (critical) — `capabilities`/`tool_search`/`tools_diff` падают под
   живым MCP-сервером; тесты зовут их синхронно и потому не видят.
5. ADO-067 (high) — `task_update` (description/acceptance/priority) в MCP и CLI.
6. ADO-065 (high) — разбор боевого прогона E5-C по ролям.
7. SYM-010 (medium) — `ctx drift --changed-files` → PR-комментарий Orakul.
8. ADO-044 (medium) — провенанс мутаций: ADR «реализовать или снять».

**Готово, когда:**
- [ ] Зелёный прогон CI на main — со ссылкой на run id (главный критерий).
- [ ] `capabilities` отвечает через живой MCP-сервер.
- [ ] Прогон тестов не меняет реальный `~/.cod-doc/config.yaml`; `cod-doc` зарегистрирован.
- [ ] Грумминг бэклога выполним через MCP и CLI без скриптов в service-слой.
- [ ] Разбор E5-C — таблица по ролям, findings F1–F5 разведены.
- [ ] Audit-отчёт M5 (active, в БД), ROADMAP обновлён.

**Риск:** зелёный CI вскроет слой дефектов, невидимых локально (CI ставит
ruff без пина). Это не повод откладывать — именно это гейт и обязан ловить.

## Порядок исполнения

1. **Фаза 0** (SYM-001..004 + ADO-001/010-этап-1/015) → cod-doc безопасен для чужого репозитория.
2. **M1** → ZAIrgRush и Orakul заведены (ADO-016/017), в их деревья не записано ничего лишнего.
3. **Фазы 1–4** (SYM-005..010) → hub, findings, петля E5-C, drift-гейт; параллельно M2 (friction-лог).
4. **M3 / Фаза 5** → кросс-проектность и фича, выбранная спросом.

Трек D идёт фоном; ADO-011/012 привязаны к M2, ADO-013 — к M3, ADO-014/015
опортунистично (ADO-015 удобно закрыть вместе с ADO-010 — общая корневая
причина).

## История сверки

- **2026-06-04** — [self-improvement audit](../audit/2026-06-04-self-improvement-compared.md): двойное LLM-ревью, P0/P1/P2 backlog, RFC #21.
- **2026-06-05** — [трёхсторонняя сверка](../audit/2026-06-05-doc-drift-source-of-truth.md) БД↔markdown↔код; Трек A заведён в БД (`stabilization-2026-06`).
- **2026-07-29** — [state-of-the-project audit](../audit/2026-07-29-state-of-the-project.md): Трек A закрыт 12/13; извлечены 3 скилла (9 → 12); найден F7 (`doc export` повреждает документ); приоритет смещён на adoption; заведён план `adoption-2026-08` (13 задач); этот роадмап пересобран.
- **2026-08-25** — программа Symbiosis ([RFC 22](../../../proposals/22-symbiosis-zairgrush-orakul.md)): пилоты переназначены на ZAIrgRush/Orakul (ADO-003/004 → cancelled, ADO-016/017); секция E (SYM-001…011); STB-012 → cancelled (re-scoped в ADO-013); ADO-010 разбит на 2 этапа; ADO-015 расширен под типы пилотов.
- **2026-08-27** — заведён спринт [sprint-2026-08-27-m1-phase1.md](sprint-2026-08-27-m1-phase1.md) (M1 + Фаза 1): SYM-005/006 декомпозированы в SYM-005A–D / SYM-006A–D (секция E, контракты в БД); найдены расхождения ground truth — миграция 0026 на невлитой ветке `worktree-swarm-ado022-ado015-sym004` (номера Фазы 1 сдвинуты на 0027/0028), ai-reviewer живёт в `/Users/dakh/Git/_my/ai-reviewer`, а не под Mozarella.
- **2026-08-28** — **M1 «Пилот работает» закрыт.** ADO-016 (ZAIrgRush, 31 док) и ADO-017 (Orakul, 405 доков) → done; SYM-003 (bind-hygiene + гейт loopback) → done (ae5911e); ветка `worktree-swarm-ado022-ado015-sym004` влита (1a66aaa — ADO-015 типы, SYM-004 `--exclude`, ADO-022 fidelity, миграция 0026). Friction-лог ADO-005: 14 записей (≥10 для M2). Оговорки: ADR ZAIrgRush без типов → SYM-007; stale_export после импорта (friction #7/#12) — кандидат в M2.
- **2026-08-28 (2)** — заведён спринт [sprint-2026-08-28-m2-feedback-loop.md](sprint-2026-08-28-m2-feedback-loop.md) (M2): friction-слоты ADO-030…033 (записи #5/#6/#9/#13 лога, решение владельца — все четыре), хвосты аудита F1/F2/F4 (ADO-027/028/029), route drift ADO-011/012, стретч SYM-007.
- **2026-08-29** — **M2 «Обратная связь встроена» закрыт досрочно** ([audit](../audit/2026-09-11-sprint-m2-feedback-loop.md)): все гола + стретч SYM-007 (13/13 ADR ZAIrgRush, 2 supersede). Коммиты `17b7137`…`cf328e7`. Suite 1562 passed, drift 123/123. Остаток friction-лога (#8/#10/#11/#14) → backlog, приоритизация на планировании M3.
- **2026-08-29 (2)** — **M3-кикофф (ADO-056): Трек B отбракован целиком.** Сверка RFC 16–21 по коду + friction-лог M2 (#8/#10/#11/#14) + contract-audit: спрос не закрывается ни одной RFC → решение «отбракованы все», пометки в `proposals/README.md` (ADO-013 закрыт). Решение владельца: M3 перенацелен на остатки friction-лога M2 (#8/#10/#11/#14). Параллельно спринт H1: hardening по contract-аудиту (ADO-035/036/037/038/052/053/055 done).
- **2026-08-30** — **M3 «friction-log leftovers» закрыт досрочно** (день в день со стартом; [audit](../audit/2026-09-06-sprint-m3-friction.md)): #8/#10/#11/#14 → ADO-058…061, friction-лог ADO-005 обнулён (0 открытых из 14); стретчи ADO-039 (enforce atomic checkout, `a2f3cfb`) и SYM-008 + ADO-057 (`cod-doc ctx docs|drift|search --json`, `0282835`; петля E5-C в ZAIrgRush, `4110cc6`) выполнены. Drift 130/130 (заодно зарегистрирован ранее не трекаемый `sprint-2026-08-27-m1-phase1.md`), ratchet 6 записей без роста. Незакрытый чек M3: проверка фиксов на корпусе Orakul — проект не зарегистрирован в этом окружении; фиксы верифицированы на живом корпусе ZAIrgRush (dry-run показывает warning'и неизвестных `type:`).
- **2026-09-02** — **M4 «Доказательство ценности» закрыт** ([audit](../audit/2026-09-02-sprint-m4-proof-of-value.md)): все четыре пункта очереди (ADO-062/064 Orakul 405/405, ADO-063 E5-C `7bc156d` — вердикт «масштабируем» за $0.98, ADO-040 write-path `3b2662b`, SYM-009 ingest ai_review `2ac0631`). **Главная находка закрытия: CI на main не был зелёным ни разу с 2026-05-06** — 10 прогонов из 10 failure, причина `FileNotFoundError: 'alembic'` в фикстурах; значит пункт «гейты зелёные» в DoD M1…M4 проверялся только локальным прогоном. Ещё пять находок сверки: единственный красный тест локально (v2-кейс отстал от SYM-009), три MCP-тула падают под живым сервером (`asyncio.run` в running loop), тесты пишут в реальный `~/.cod-doc/config.yaml` (регресс F5), грумминг бэклога недоступен агенту (`task_update` только в web), три объявленных контракта без реализации (run_id 2004/2004 NULL, audit_log 0 строк / 0 писателей). Заведены ADO-066…070; ADO-043 и ADO-051 свёрнуты в ADO-044. Заведён спринт [M5 «Гейт, которому можно верить»](sprint-m5-trustworthy-gate.md) с контрактами задач в БД (новый ключ task_doc — `contract`).
