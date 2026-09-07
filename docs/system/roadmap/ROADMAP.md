---
type: roadmap-index
scope: cod-doc-roadmap
status: active
source_of_truth: true
owner: cod-doc core
created: 2026-06-05
last_updated: 2026-09-07
audience: [contributors, agents]
related_docs:
  - ../MASTER.md
  - task-graph.md
  - ../../../proposals/README.md
  - ../audit/2026-07-29-state-of-the-project.md
  - ../../adoption-playbook.md
  - ../../../proposals/22-symbiosis-zairgrush-orakul.md
  - ../../../proposals/23-cloud-decentralized-agent-plane.md
  - ../../../proposals/24-structure-contracts-scenarios.md
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
- [Proposals (RFC 01–24)](../../../proposals/README.md)
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
| **RFC 23–24 (M6-track)** | 🟠 DEFERRED | RFC 23 (Cloud agent plane) и RFC 24 (structure/contracts/scenarios) — producer готов, задачи CAP-*/STR-* отложены до M6 |

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
| ~~**ADO-070**~~ | ~~CI на main не был зелёным ни разу с 2026-05-06~~ — **done 2026-09-03** (`bcb32f2`): три слоя причин (alembic через `sys.executable -m`, герметичность окружения + таймаут 25 мин, хардкод `.venv/bin/python` и обёртки FastAPI 0.141). [Run 33765619088](https://github.com/Orange-hanter/cod-doc/actions/runs/33765619088) — success, все 7 джоб | — | — |
| ~~**ADO-069**~~ | ~~Красный гейт локально: v2-кейс отстал от адаптера~~ — **done 2026-09-03** (`3de0fd5`): негативный кейс на версию вне `_KNOWN_VERSIONS` + позитивный тест приёма v2 через API v1 | — | — |
| ~~**ADO-068**~~ | ~~Тесты пишут в реальный `~/.cod-doc/config.yaml`~~ — **done 2026-09-03** (`3de0fd5`): `config_dir()`/`config_file()` вместо import-time констант, `adapters.json` уважает `COD_DOC_HOME`; реестр вычищен, `cod-doc` зарегистрирован | — | — |
| ~~**ADO-066**~~ | ~~`capabilities`/`tool_search`/`tools_diff` падают под живым MCP-сервером~~ — **done 2026-09-03** (`3de0fd5`): тулы в async; тот же дефект в `snapshot_tools.py`; AST-гейт на `asyncio.run` под `cod_doc/mcp/` | — | — |
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
| **STR-001..004** | [RFC 24](../../../proposals/24-structure-contracts-scenarios.md): контур structure/contracts/scenarios — фазы 3–6 (сторона cod-doc); producer смержен в ai-reviewer 2026-09-03 | medium | — (SYM-005..009 done) |

### Трек B — Feature-трек (hackathon RFC, после C)

❌ **Отбракован целиком 2026-08-29 (ADO-056, M3-кикофф).** Сверка секций
«Текущее состояние» RFC 16–21 по коду + сопоставление с friction-логом M2
(ADO-005, записи #8/#10/#11/#14) показали: ни одна RFC не закрывает
зафиксированный спрос. Пометки — в
[proposals/README.md](../../../proposals/README.md). Порядок B-MVP/B-Scale
ниже — исторический контекст.

## M6 — «Hub + кросс-проектность» *(планируется после завершения трека C/E)*

**Цели M6:**
1. Кросс-проектный поиск через hub-БД (`[[doc:slug:key]]`) — SYM-011.
2. Фикс ChromaDB L3-режима для мульти-проектности.
3. Расширение `agent_pick --projects` для работы с несколькими проектами.
4. Запуск **RFC 23** (Cloud decentralized agent plane) — задачи CAP-001…CAP-033.
5. Запуск **RFC 24** (единый контур structure/contracts/scenarios) — задачи STR-001…STR-004.

**Статус:** SYM-011 открыт (low priority); RFC 23 и RFC 24 спроектированы
(статус 🟠 DEFERRED), producer для RFC 24 готов (фазы 1–2 в ai-reviewer),
prerequisite SYM-005..009 выполнен. Задачи CAP-*/STR-* не начаты, ожидают
приоритизации в плане M6.

**Готово, когда:**
- [ ] SYM-011 закрыт: кросс-проектный поиск работает через hub-БД.
- [ ] ChromaDB L3-режим исправлен для мульти-проектности.
- [ ] `agent_pick --projects` поддерживает работу с несколькими проектами.
- [ ] RFC 23: запущен план CAP-001…CAP-033 (cloud agent plane).
- [ ] RFC 24: запущен план STR-001…STR-004 (structure/contracts/scenarios).

**Риск:** преждевременный старт M6 до завершения adoption (трек C/E) распылит
фокус. **Митигация:** M6 не начинается до закрытия SYM-010 и верификации
пилотов ZAIrgRush/Orakul.
