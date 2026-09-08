---
type: sprint-plan
scope: adoption-2026-08
status: active
source_of_truth: false
canonical_source: docs/system/roadmap/ROADMAP.md
owner: cod-doc core
created: 2026-08-30
last_updated: 2026-09-02
audience: [next-session-agent, contributors]
related_docs:
  - ROADMAP.md
  - sprint-2026-08-30-m3-friction-log.md
  - ../audit/2026-09-06-sprint-m3-friction.md
---

# Sprint M4 — «Доказательство ценности + разбор долга»

> **Принцип (решение владельца 2026-08-30).** Код пишет AI-агент — сроки и
> окна не планируются. Спринт = **упорядоченная очередь работ** с чёткими
> контрактами и критерием выхода. Порядок важен, даты — нет.
>
> **Не source of truth.** Статусы задач — в БД (план `adoption-2026-08`);
> приоритеты — [ROADMAP.md](ROADMAP.md).

## 0. Ground truth на старт спринта (сверено 2026-08-30)

- M3 закрыт досрочно: audit
  [2026-09-06-sprint-m3-friction.md](../audit/2026-09-06-sprint-m3-friction.md),
  финальный коммит `1ae797e`. Friction-лог ADO-005 обнулён (0 из 14).
- Suite 1601 passed, mypy чист (318 файлов), drift 130/130 in_sync,
  ratchet 6 записей.
- План `adoption-2026-08`: 61/78 done; остаток — секция D (12 задач,
  единственный high — ADO-040) и секция E (SYM-009/010/011).
- Пилот `orakul` не зарегистрирован в текущем окружении — открытый чек
  M3 в ROADMAP («проверено на корпусе Orakul»).
- SYM-008 дал механику моста cod-doc→ZAIrgRush, но `doc_context = off`:
  метрики E5-C не собираются, ценность не доказана.

## 1. Очередь (порядок = приоритет)

### 1. Перерегистрация Orakul (разблокировка)

- `cod-doc project add` Orakul в текущем окружении; реимпорт корпуса.
- Прогон фиксов M3 на корпусе 405 доков: dry-run `--limit 0`, счётчик
  hidden-dirs, warning'и чужих `type:`.
- Закрыть чек в ROADMAP («Каждый фикс проверен на пилотном корпусе
  Orakul»).
- Задача: ADO-062 (chore, high) — блокер доверия к пилотам.

### 2. E5-C в бою: doc_context=executor на реальной задаче ZAIrgRush

Главный вопрос спринта: есть ли ценность у моста cod-doc→петля.

- Маппинг `task.paths` (.py) → doc-пути в `docctx.py`/конфиге — без него
  блок документов пуст на реальных задачах (finding F1 аудита M3).
- Включить `doc_context = "executor"` в конфиге swarm ZAIrgRush.
- Прогнать 1–3 реальные задачи петли; метрики RFC 22 §3.4: latency/раунд,
  breaker-open rate, субъективная оценка качества владельцем.
- Результат — артефакт: findings + ADR/заметка в ZAIrgRush с решением
  «масштабируем / выключаем» (оба исхода валидны).
- Задача: ADO-063 (feature, critical) — критерий выхода M4.

### 3. ADO-040 — единый write-path wrapper для activity events (high)

- Общий helper/декоратор write-пути (revision + activity + run_id).
- Покрытие: adr/approval/task_doc/story/comment/checkout/link_resolver/
  repo_index/commit_link сервисы эмитят события.
- Ошибка emit — log/raise, не pass (сейчас глотается,
  `task_service.py:405-420, 574-589`).
- Задача уже в БД (ADO-040, секция D).

### 4. SYM-009 — ingest ai_review pull-моделью (medium)

- `cod-doc ingest ai_review --from-pr N` + поллер
  `scripts/ingest-orakul-reviews.sh`.
- Upstream-PR в ai-reviewer: fp/verifierStatus/actionabilityScore в
  slimFinding + bump EXPORT_VERSION (их P0 #18, ~10 строк).
- `cod-doc finding stability --sha`: попарный Jaccard из
  finding_source_run.
- Acceptance (из БД): 3 исторических PR-экспорта импортированы,
  повторные находки times_seen>1; git status Orakul чист; PR в
  ai-reviewer открыт.

### 5. Хвост секции D (опционально, только после п.1–4)

ADO-042 (SQL → repositories), ADO-043 (audit_log: writers или удалить),
ADO-045 (DATA_MODEL sync). Одна задача = один закрытый контракт; не
цель спринта.

### Вне скоупа M4

- SYM-010 (drift-гейт для Orakul) — до живого Orakul (п.1) и спроса из
  п.2; кандидат в M5.
- SYM-011 (кросс-проектность) — low, по спросу.
- ADO-046…051 — backlog.
- Трек B — отбракован (ADO-056), не переоткрывать.

## 2. Критерий выхода

1. Orakul зарегистрирован; чек ROADMAP закрыт.
2. Решение по E5-C зафиксировано артефактом (ADR/findings + метрики).
3. ADO-040 done: wrapper + 9 сервисов эмитят; ошибка emit видна.
4. Гейты зелёные: suite, ruff/format, mypy, drift 100%, ratchet ≤ 6.
5. Audit-отчёт M4 (active, в БД), ROADMAP обновлён.

## 3. Порядок исполнения

1. Оформление: этот sprint-док (doc create + import), задачи ADO-062/063
   в БД (секция C), указатель в ROADMAP — один коммит.
2. П.1 → п.2 → п.3 → п.4 строго по очереди; каждая задача
   checkout → complete с `commit_sha`; баги — с красным прогоном.
3. Финал: гейты, audit-отчёт, ROADMAP, коммит.

## 4. Риски

- **Пустые блоки в E5-C** без маппинга .py→doc-пути — маппинг включён в
  п.2 явно.
- **Upstream-PR в ai-reviewer (п.4) может зависнуть** — внешняя
  зависимость; не влит к концу M4 → SYM-009 переносится, спринт не
  блокируется.
- **Скоуп-крип секции D**: п.5 опционален сознательно.

## 5. Definition of Done

- [ ] Каждая задача очереди прошла `task_checkout` → `task_complete` с
      `commit_sha`.
- [ ] Решение по E5-C — артефакт в ZAIrgRush (не «в голове»).
- [ ] Правки трекаемых `.md` импортированы в тех же коммитах;
      финальный `doc drift --all` — 100% in_sync.
- [ ] `ruff` + `mypy` + `pytest` зелёные на последнем коммите;
      ratchet ≤ 6.
- [ ] Audit-отчёт M4 — status active, в БД, со ссылками на коммиты.
