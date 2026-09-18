"""PCA-031: agent run-id propagation via contextvar.

**ADR-012 (ADO-044): это телеметрия встроенного оркестратора, а не
контракт.** ``Orchestrator.run_task`` — единственный вызывающий
(``agent/orchestrator.py``), и им не пользуются: реальная работа идёт
через MCP из Claude Code, где скоуп не открывается. Замер на живой БД
2026-09-06: `revision` 2166/2166 и `activity_event` 1114/1114 с
`run_id IS NULL`. Не пиши код, который рассчитывает на непустой
`run_id`, — правило «run-id на всех мутациях» снято из AGENTS.md §5.4.

Механика: внутри скоупа мутации ниже по стеку (``RevisionService.write``,
``activity_service.emit``, ``approval_service``, ``task_doc_service``,
``routine_service``) читают активный run_id из contextvar'а и штампуют
его на своих строках. Вне скоупа contextvar держит ``None`` — колонка
остаётся NULL.

Usage::

    from cod_doc.services.run_context import run_scope

    with run_scope(session, project_id, run_id="01J...", wake_reason="manual"):
        ...                # all mutations below carry run_id

Or via the orchestrator's heartbeat — see ``Orchestrator.run_task`` (PCA-022).

The contextvar is async-safe (set/reset via :class:`contextvars.Token`),
so concurrent agent runs don't bleed run_ids into each other's mutations.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cod_doc.domain.entities import actor_kind_for_author
from cod_doc.infra.models import AgentRunModel

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session, sessionmaker

_log = logging.getLogger(__name__)


_current_run_id: ContextVar[str | None] = ContextVar("cod_doc_run_id", default=None)


def get_current_run_id() -> str | None:
    """Return the run_id active in the current async/thread context, or None."""
    return _current_run_id.get()


def set_current_run_id(run_id: str | None) -> None:
    """Override the active run_id; intended for tests / non-orchestrator contexts.

    Production code should use :func:`run_scope` so the value is reset on exit.
    """
    _current_run_id.set(run_id)


@contextmanager
def run_scope(
    session: Session,
    *,
    project_id: int,
    run_id: str,
    wake_reason: str | None = None,
    triggering_task_id: str | None = None,
    triggering_doc_ref: str | None = None,
) -> Iterator[AgentRunModel]:
    """Enter an agent-run scope: write the ``agent_run`` row, set the contextvar.

    Yields the freshly-created :class:`AgentRunModel`. On clean exit, marks
    ``status='done'`` and stamps ``finished_at``; on exception, marks
    ``status='failed'``. The caller still owns the surrounding transaction.

    Caller is responsible for committing — this helper only flushes so the
    row is queryable inside the scope.
    """
    run_model = AgentRunModel(
        run_id=run_id,
        project_id=project_id,
        wake_reason=wake_reason,
        triggering_task_id=triggering_task_id,
        triggering_doc_ref=triggering_doc_ref,
        status="running",
    )
    session.add(run_model)
    session.flush()
    token = _current_run_id.set(run_id)
    try:
        yield run_model
    except Exception:
        run_model.status = "failed"
        run_model.finished_at = datetime.now(UTC)
        session.flush()
        raise
    else:
        run_model.status = "done"
        run_model.finished_at = datetime.now(UTC)
        session.flush()
    finally:
        _current_run_id.reset(token)


# --------------------------------------------------------------------------- #
# Orchestrator hooks (PCA-034)                                                 #
# --------------------------------------------------------------------------- #


def _open_session_for_project(
    project_path: str | None,
) -> tuple[sessionmaker[Session] | None, int | None]:
    """Best-effort session factory for a project on disk.

    Returns ``(session_factory, project_db_id)`` or ``(None, None)`` if
    the project has no DB (legacy YAML-only projects, fixture paths etc.).
    Used by the orchestrator hooks below — failures are silent so legacy
    test environments without a migrated DB don't break.
    """
    if not project_path:
        return None, None
    try:
        from pathlib import Path

        from cod_doc.infra.db import (
            make_engine,
            make_session_factory,
            resolve_db_url,
            transactional,
        )
        from cod_doc.infra.repositories import ProjectRepository

        url = resolve_db_url(Path(project_path))
        engine = make_engine(url)
        sf = make_session_factory(engine)
        with transactional(sf) as session:
            # Project lookup by slug — assumes the project is registered in
            # the slug-keyed cod-doc config; fixtures often skip this and
            # we silently fall through.
            from cod_doc.config import Config

            cfg = Config.load()
            for entry in cfg.list_projects():
                if entry.path == project_path:
                    proj = ProjectRepository(session).get_by_slug(entry.name)
                    if proj is not None and proj.row_id is not None:
                        return sf, proj.row_id
        return None, None
    except Exception:  # degraded path (covered by test_degraded_paths)
        return None, None


# --------------------------------------------------------------------------- #
# ADO-115: шаги прогона на диск                                                #
# --------------------------------------------------------------------------- #
#
# Живая лента консоли идёт по WebSocket (`event_bus`), и на диск не попадала
# вообще: `/p/<slug>/run/<id>` звал `events_for_run`, находил ноль строк и
# рисовал «run упал до первого write-tool». Ни один из двадцати прогонов в
# `agent_run` не имел ни одного шага.
#
# Пишем в два места, и это сознательно:
#
# * `activity_event` — КОНТРАКТ. По нему строится реплей, туда уходит
#   обрезанное тело. Строка в БД есть всегда, даже если sidecar не записался.
# * `<project>/.cod-doc/runs/<run_id>.jsonl` — ДОПОЛНЕНИЕ. Полное тело шага,
#   чтобы детали можно было восстановить. Рядом уже живут sidecar-файлы
#   (`nav_cache.json`, `task_audit.json`), но отличие надо назвать вслух: те
#   регенерируемый кэш, а этот — единственная копия деталей. Поэтому он
#   телеметрия (потеря допустима), но чистить его молча нельзя.
#
# Порядок записи: сперва файл, потом событие. Если файл не записался, событие
# всё равно пишется, но с `full: false` — указатель не должен обещать того,
# чего нет.

#: Потолок текстового поля шага в БД. Шаблон и так режет показ на 800,
#: так что 2000 не теряет ничего читаемого, но закрывает `tool_result` с
#: телом документа на сотни килобайт.
MAX_STEP_CHARS = 2000

#: Потолок числа шагов В БД на один прогон — ровно тот `limit`, который
#: страница прогона уже передаёт в `events_for_run`. Писать то, что никогда
#: не покажется, смысла нет. За потолком в БД пишутся только терминальные
#: шаги (`error` / `blocked` / `stopped`), а в sidecar — ВСЕ: это и есть
#: обещанная возможность восстановить детали.
MAX_STEPS_PER_RUN = 500

#: Шаги, которые пишутся даже после исчерпания потолка: без них у
#: воспроизведённого прогона не будет конца.
_TERMINAL_KINDS = frozenset({"agent.error", "agent.blocked", "agent.stopped"})

#: `run_id` становится ИМЕНЕМ ФАЙЛА и приходит в том числе из URL
#: (`/p/<slug>/run/<run_id>/step/<i>`), поэтому проверяется до любого
#: касания пути: без этого `..%2F..%2Fetc%2Fpasswd` уводит запись и чтение
#: за пределы каталога прогонов.
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_current_run_sink: ContextVar[_RunSink | None] = ContextVar("cod_doc_run_sink", default=None)


def validate_run_id(run_id: str) -> str:
    """Вернуть `run_id`, если он безопасен как имя файла; иначе ValueError.

    Точка входа одна и для записи, и для чтения — иначе проверка на одной
    стороне создаёт ложное чувство безопасности на другой.
    """
    if not isinstance(run_id, str) or not _RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"unsafe run_id {run_id!r}: expected 1-128 chars of [A-Za-z0-9._-]")
    if run_id in {".", ".."} or run_id.startswith("."):
        raise ValueError(f"unsafe run_id {run_id!r}")
    return run_id


def run_steps_path(project_path: str | Path, run_id: str) -> Path:
    """Путь к sidecar-файлу шагов прогона."""
    return Path(project_path) / ".cod-doc" / "runs" / f"{validate_run_id(run_id)}.jsonl"


@dataclass(slots=True)
class _RunSink:
    """Куда писать шаги текущего прогона. Резолвится один раз на прогон."""

    run_id: str
    session_factory: sessionmaker[Session] | None
    project_db_id: int | None
    steps_path: Path | None
    written: int = 0
    capped_reported: bool = False


@dataclass(slots=True)
class _RunHandle:
    """Пара токенов contextvar'ов. `start_orchestrator_run` возвращает `object`,
    поэтому расширение обошлось без смены сигнатуры."""

    run_token: object
    sink_token: object


def _append_sidecar(sink: _RunSink, index: int, kind: str, data: object) -> bool:
    """Дописать полный шаг в JSONL. True, если строка легла на диск."""
    if sink.steps_path is None:
        return False
    try:
        sink.steps_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"i": index, "ts": datetime.now(UTC).isoformat(), "kind": kind, "data": data},
            ensure_ascii=False,
        )
        with sink.steps_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        _log.debug("run_step_sidecar_failed", extra={"run_id": sink.run_id, "step": index})
        return False
    return True


def _truncate(value: object) -> tuple[object, bool, int | None]:
    """Обрезать длинное текстовое поле, сохранив признак обрезки."""
    if isinstance(value, str) and len(value) > MAX_STEP_CHARS:
        return value[:MAX_STEP_CHARS], True, len(value)
    return value, False, None


def record_step(kind: str, data: object) -> None:
    """Записать шаг прогона: полный — в sidecar, обрезанный — в activity_event.

    No-op вне прогона (sink is None) и best-effort внутри: ошибки глотаются
    в debug-лог. Это тот же degraded-контракт, что у best-effort строки
    `agent_run` рядом, и он намеренный — телеметрия не должна ронять прогон.
    """
    sink = _current_run_sink.get()
    if sink is None:
        return

    terminal = kind in _TERMINAL_KINDS
    if sink.written >= MAX_STEPS_PER_RUN and not terminal:
        if not sink.capped_reported:
            sink.capped_reported = True
            _log.debug("run_steps_capped", extra={"run_id": sink.run_id})
        # В sidecar пишем ВСЁ — потолок только у БД.
        _append_sidecar(sink, sink.written, kind, data)
        sink.written += 1
        return

    index = sink.written
    full = _append_sidecar(sink, index, kind, data)
    sink.written += 1

    if sink.session_factory is None or sink.project_db_id is None:
        return

    payload: dict[str, Any] = {"step": index, "full": full}
    if isinstance(data, dict):
        for key, value in data.items():
            cut, truncated, orig = _truncate(value)
            payload[key] = cut
            if truncated:
                payload[f"{key}_truncated"] = True
                payload[f"{key}_orig_len"] = orig
    else:
        cut, truncated, orig = _truncate(data)
        payload["data"] = cut
        if truncated:
            payload["data_truncated"] = True
            payload["data_orig_len"] = orig
    if sink.capped_reported:
        payload["capped"] = True

    try:
        from cod_doc.infra.db import transactional
        from cod_doc.services import activity_service

        # Транзакция на шаг, а не долгоживущая сессия: открытая пишущая
        # транзакция на SQLite блокирует основного писателя (MCP-демон), а
        # между шагами оркестратор всё равно стоит на round-trip к LLM.
        with transactional(sink.session_factory) as session:
            activity_service.emit(
                session,
                sink.project_db_id,
                kind,
                actor_kind=actor_kind_for_author(f"orchestrator-run-{sink.run_id}").value,
                actor_id=f"orchestrator-run-{sink.run_id}",
                run_id=sink.run_id,
                scope_kind="run",
                scope_id=sink.run_id,
                payload=payload,
            )
    except Exception:  # degraded path — телеметрия не роняет прогон
        _log.debug("run_step_emit_failed", extra={"run_id": sink.run_id, "step": index})


def start_orchestrator_run(
    *,
    project_path: str | None,
    run_id: str,
    wake_reason: str | None = None,
    triggering_task_id: str | None = None,
    triggering_doc_ref: str | None = None,
) -> object:
    """Begin an orchestrator run: set the contextvar + best-effort DB row.

    Returns an opaque handle to pass back into
    :func:`finalize_orchestrator_run`. Caller is responsible for calling
    finalize on every exit path so the contextvars reset cleanly.

    The agent_run DB row is best-effort — if the project has no DB, the
    contextvar still flows through revisions written elsewhere. This
    keeps proposal 04 working in legacy / fixture environments.
    """
    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import AgentRunModel

    sf, project_db_id = _open_session_for_project(project_path)
    if sf is not None and project_db_id is not None:
        try:
            with transactional(sf) as session:
                session.add(
                    AgentRunModel(
                        run_id=run_id,
                        project_id=project_db_id,
                        wake_reason=wake_reason,
                        triggering_task_id=triggering_task_id,
                        triggering_doc_ref=triggering_doc_ref,
                        status="running",
                    )
                )
        except Exception:  # degraded path (covered by test_degraded_paths)
            pass

    # ADO-115: sink резолвится ОДИН раз на прогон. Сессию в `run_task` не
    # заводим — её там нет и не должно появиться; писатель живёт здесь и
    # переиспользует тот же `_open_session_for_project`.
    steps_path: Path | None = None
    if project_path:
        try:
            steps_path = run_steps_path(project_path, run_id)
        except ValueError:
            # Небезопасный run_id: пишем только в БД, файл не трогаем.
            _log.debug("run_steps_disabled_unsafe_run_id", extra={"run_id": run_id})
    sink = _RunSink(
        run_id=run_id,
        session_factory=sf,
        project_db_id=project_db_id,
        steps_path=steps_path,
    )
    run_token = _current_run_id.set(run_id)
    sink_token = _current_run_sink.set(sink)
    return _RunHandle(run_token=run_token, sink_token=sink_token)


def finalize_orchestrator_run(
    token: object,
    *,
    project_path: str | None,
    run_id: str,
    status: str = "done",
    summary: str | None = None,
) -> None:
    """End an orchestrator run: best-effort row update + contextvar reset.

    Always resets the contextvar (so the heartbeat doesn't leak its
    run_id into a subsequent task). DB update is best-effort.
    """
    from sqlalchemy import select

    from cod_doc.infra.db import transactional
    from cod_doc.infra.models import AgentRunModel

    sf, project_db_id = _open_session_for_project(project_path)
    if sf is not None and project_db_id is not None:
        try:
            with transactional(sf) as session:
                run = session.execute(
                    select(AgentRunModel).where(AgentRunModel.run_id == run_id)
                ).scalar_one_or_none()
                if run is not None:
                    run.status = status
                    run.finished_at = datetime.now(UTC)
                    if summary is not None:
                        run.summary = summary
        except Exception:  # degraded path (covered by test_degraded_paths)
            pass

    from contextvars import Token

    # ADO-115: `start_orchestrator_run` теперь возвращает пару токенов.
    # Старая однотокеновая ветка оставлена не из вежливости — на неё
    # опираются существующие тесты, которые зовут finalize с голым Token.
    run_token: object = token
    if isinstance(token, _RunHandle):
        run_token = token.run_token
        if isinstance(token.sink_token, Token):
            _current_run_sink.reset(token.sink_token)
        else:
            _current_run_sink.set(None)
    else:
        _current_run_sink.set(None)

    if isinstance(run_token, Token):
        _current_run_id.reset(run_token)
    else:
        _current_run_id.set(None)
