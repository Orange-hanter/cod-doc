"""Рутина ``sync``: автосинк проекта с remote по расписанию (RFC 29 §3.9, CLO-040).

Движок рутин зовёт проверку как ``check_fn(session, project_id, **check_args)``
внутри своей транзакции. Сетевой обмен держал бы эту пишущую транзакцию SQLite
минутами, поэтому ``sync`` объявлена в ``OWN_TRANSACTION_CHECKS``: движок
фиксирует свою транзакцию до вызова (строка ``routine_run`` «running» уже
записана), а проверка использует переданную сессию только для чтения слага и
итога. ``pull`` и ``push`` открывают свои короткие транзакции через фабрику
сессий поверх того же движка БД: сеть — вне транзакций, применение — одной
транзакцией на changeset (§10.6).

Исходы:

- **remote не настроен** — пропуск без находки (``skipped: no_remote``): проект
  без remote синхронизировать не с чем, это не поломка.
- **нет сети** (``RemoteUnavailableError``, ``SYNC-101``) — тихий пропуск без
  находки: следующий прогон попробует снова.
- **дайджест не сошёлся** после цикла при общей голове с remote — находка: та
  же голова, а состояние другое — реплики разошлись.
- **прочие ошибки синхронизации** (``SYNC-1xx``/``2xx``: слияние, эпоха, доступ)
  — находка с кодом: без человека цикл их не разрешит, и молча пропускать их
  значило бы перестать синхронизироваться незаметно.

``author`` — автор changeset'ов push'а. Для умного https-remote им обязан быть
владелец токена (``human:<handle>``, CLO-037), поэтому он — аргумент рутины, а не
константа: MCP ``routine_create(check_name="sync", cron="*/10 * * * *",
check_args={"author": "human:dakh"})`` (RFC: по умолчанию раз в 10 минут).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from cod_doc.infra.remote.errors import RemoteUnavailableError, SyncError

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

__all__ = ["DEFAULT_AUTHOR", "check_sync"]

DEFAULT_AUTHOR: Final = "routine:sync"


def _finding(kind: str, message: str, **details: object) -> dict[str, Any]:
    return {"kind": kind, "message": message, **details}


def check_sync(
    session: Session,
    project_id: int,
    *,
    remote: str = "origin",
    author: str = DEFAULT_AUTHOR,
    **_: Any,
) -> dict[str, Any]:
    """``pull``, затем ``push``; итог — для ``routine_run``, находки — по докстрингу модуля."""
    from cod_doc.config import Config
    from cod_doc.infra.db import make_session_factory
    from cod_doc.infra.models import ProjectModel
    from cod_doc.services.sync_service import _local, digest, pull, push

    project = session.get(ProjectModel, project_id)
    if project is None:
        return {"findings": [], "findings_count": 0, "skipped": "no_project"}
    slug = project.slug
    if remote not in Config.load().project_remotes(slug):
        return {"findings": [], "findings_count": 0, "skipped": "no_remote"}
    # Транзакцию чтения — закрыть до сети: движок свою уже зафиксировал.
    session.commit()
    sf = make_session_factory(session.get_bind())  # type: ignore[arg-type]

    try:
        store = _local.open_store(slug, remote)
        pulled = pull.pull(sf, slug, remote=remote, author=author, store=store)
        pushed = push.push(sf, slug, remote=remote, author=author, store=store)
        verified = digest.verify(sf, slug, remote=remote, store=store)
        remote_ref = store.get_ref(project.uid or "", _local.MAIN) if project.uid else None
    except RemoteUnavailableError as exc:
        return {"findings": [], "findings_count": 0, "skipped": "offline", "error": str(exc)}
    except SyncError as exc:
        found = _finding("sync_failed", str(exc), code=exc.code)
        return {"findings": [found], "findings_count": 1}

    with sf() as s:
        local_head = _local.local_head(s, project_id)
    result: dict[str, Any] = {
        "pulled": len(pulled.applied),
        "pushed": len(pushed.changesets),
        "conflicts": len(pulled.conflicts),
        "rebased": pulled.rebased,
        "head": local_head,
        "digest_equal": verified.equal,
        "findings": [],
    }
    same_head = remote_ref is not None and remote_ref.sha == local_head
    if same_head and not verified.equal:
        result["findings"].append(
            _finding(
                "digest_mismatch",
                "голова реплики совпадает с remote, а дайджест состояния — нет",
                local=verified.local,
                remote=verified.remote,
                head=local_head,
            )
        )
    result["findings_count"] = len(result["findings"])
    return result
