"""Read/dismiss queries over the ``finding`` table (RFC 22 §3.2, SYM-006D)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from cod_doc.domain.entities import actor_kind_for_author
from cod_doc.infra.models import FindingModel
from cod_doc.services import activity_service, search_service

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: Словарь статусов — RFC 22 §3.2. ``dismissed`` и ``promoted`` терминальны:
#: это решения человека, и автоматика их не отменяет.
FINDING_STATUS_OPEN = "open"
FINDING_STATUS_RESOLVED = "resolved"
FINDING_STATUS_DISMISSED = "dismissed"
DEFAULT_LIST_LIMIT = 100

#: Сколько прогонов подряд производитель должен не видеть находку, чтобы её
#: закрыли. Единица — сегодняшнее поведение и правильный дефолт: правила
#: детерминированы и не промахиваются, а лишний прогон отсрочки задержал бы
#: закрытие вылеченного. Больше единицы просит тот, чей вердикт субъективен.
DEFAULT_CLOSE_AFTER_MISSES = 1


def finding_to_dict(f: FindingModel) -> dict[str, Any]:
    """Render a FindingModel row as a plain dict for MCP/JSON return."""
    return {
        "finding_id": f.row_id,
        "finding_uid": f.finding_uid,
        "source": f.source,
        "source_ref": f.source_ref,
        "fingerprint": f.fingerprint,
        "severity": f.severity,
        "kind": f.kind,
        "title": f.title,
        "body": f.body,
        "path": f.path,
        "line": f.line,
        "status": f.status,
        "confidence": f.confidence,
        "times_seen": f.times_seen,
        "miss_streak": f.miss_streak,
        "first_seen_at": f.first_seen_at.isoformat() if f.first_seen_at else None,
        "last_seen_at": f.last_seen_at.isoformat() if f.last_seen_at else None,
        "promoted_task_id": f.promoted_task_id,
        # ADO-116: потребителю нужен адрес находки (раздел/проект), а он
        # лежит только в payload. Без него сгруппировать находки по
        # разделам можно лишь разбором заголовка.
        "payload": dict(f.payload or {}),
    }


def _get_model(session: Session, project_id: int, finding_uid: str) -> FindingModel | None:
    f = session.execute(
        select(FindingModel).where(FindingModel.finding_uid == finding_uid)
    ).scalar_one_or_none()
    if f is None or f.project_id != project_id:
        return None
    return f


def list_findings(
    session: Session,
    project_id: int,
    *,
    status: str | None = None,
    source: str | None = None,
    limit: int = DEFAULT_LIST_LIMIT,
) -> list[dict[str, Any]]:
    """List findings for a project, newest-seen first.

    ``status``: open | resolved | dismissed | promoted (RFC 22 §3.2).
    ``source``: ai_review | zairgrush | routine.
    """
    stmt = (
        select(FindingModel)
        .where(FindingModel.project_id == project_id)
        .order_by(FindingModel.last_seen_at.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(FindingModel.status == status)
    if source is not None:
        stmt = stmt.where(FindingModel.source == source)
    return [finding_to_dict(f) for f in session.execute(stmt).scalars()]


def get_finding(
    session: Session,
    project_id: int,
    finding_uid: str,
) -> dict[str, Any] | None:
    """Return one finding by its stable ``finding_uid``, or None."""
    f = _get_model(session, project_id, finding_uid)
    return finding_to_dict(f) if f is not None else None


def dismiss_finding(
    session: Session,
    *,
    project_id: int,
    finding_uid: str,
    author: str = "mcp",
    reason: str | None = None,
) -> dict[str, Any]:
    """Mark a finding ``dismissed`` (operator triage: not worth a task).

    Emits a ``finding.dismissed`` activity event in the same transaction
    (proposal 09). Dismissing an already-dismissed finding is a no-op and
    does not emit a second event.
    """
    f = _get_model(session, project_id, finding_uid)
    if f is None:
        raise ValueError(f"finding '{finding_uid}' not found")
    if f.status == FINDING_STATUS_DISMISSED:
        return finding_to_dict(f)

    f.status = FINDING_STATUS_DISMISSED
    session.flush()
    activity_service.emit(
        session,
        project_id,
        "finding.dismissed",
        actor_kind=actor_kind_for_author(author),
        actor_id=author,
        scope_kind="finding",
        scope_id=f.finding_uid,
        payload={"finding_id": f.row_id, "source": f.source, "reason": reason},
        summary=f"Finding {f.finding_uid} dismissed by {author}",
    )
    # CUR-012: a dismissed finding leaves the FTS index — the operator
    # already rejected it, so it must stop showing up in ctx_search.
    search_service.index_finding(session, f)
    return finding_to_dict(f)


def reconcile_partition(
    session: Session,
    *,
    project_id: int,
    source: str,
    source_ref: str,
    seen_fingerprints: set[str],
    author: str,
    close_after_misses: int = DEFAULT_CLOSE_AFTER_MISSES,
    unjudged_fingerprints: set[str] | None = None,
) -> dict[str, int]:
    """Свести находки одной партиции с тем, что производитель видит сейчас.

    Партиция — пара ``(source, source_ref)``, а не один ``source``: под
    ``source="routine"`` живут разные проверки, и упавшая или пропущенная не
    вправе закрывать находки, которых она не рассматривала. Тот же довод, что
    у ``structure_drift.reconcile_findings``, где партицией служит ``scope``.

    Делает две вещи и обе идемпотентно:

    * **закрывает** ``open``-находки партиции, которых нет в
      ``seen_fingerprints`` — пробел вылечен;
    * **переоткрывает** ``resolved``-находки, которые вернулись. Без этого
      рецидив пропадал бы навсегда: ``ingest_findings`` на конфликте поднимает
      только ``times_seen`` и ``last_seen_at``, статус не трогает, а
      ``curator_next`` смотрит только ``status="open"``.

    ``dismissed`` и ``promoted`` не трогаются ни в одну сторону: это решения
    человека, и автоматика их не отменяет.

    Вызывающий обязан передать **полный** набор отпечатков своей партиции.
    Частичный прогон (обрезанный лимитом, упавший на середине) закрыл бы
    живые находки — такой прогон сверять не должен вовсе.

    **``unjudged_fingerprints`` — то, о чём прогон не смог судить.**
    По умолчанию ``None``: производитель отвечает за всю партицию, и
    отсутствие отпечатка в ``seen_fingerprints`` означает «вылечено». Но
    источник бывает частичным не по своей вине: модель обязана вернуть вердикт
    на каждый раздел и иногда молчит о нескольких. Молчание — не вердикт
    «покрыто», и считать его промахом нельзя: несколько таких прогонов подряд
    закрыли бы живую находку, о которой никто ничего не сказал.

    Перечисленные здесь находки не трогаются вовсе — ни закрытия, ни счётчика.
    Всё остальное сверяется как обычно, поэтому частично полезный прогон не
    пропадает.

    Список именно **запретный**, а не разрешительный, и это не стилистика.
    Производитель знает, о чём он промолчал, но не знает, какие ещё находки
    лежат в партиции: там бывают находки о предмете, которого он больше не
    рассматривает вовсе (раздел опустел и выпал из промпта). Их закрывать
    как раз законно — предмет вердикта исчез. Разрешительный список подвесил
    бы их навсегда.

    Мягче, чем `can_close` из ``structure_drift`` (там недоверенный прогон
    теряет право закрывать целиком), и точнее: там сигнал про весь прогон,
    здесь — про каждую находку.

    **Гистерезис.** ``close_after_misses`` — сколько прогонов подряд находку
    должны не увидеть, прежде чем закрыть. Единица (дефолт) — прежнее
    поведение. Больше единицы нужно там, где производитель субъективен: вердикт
    модели мигает от прогона к прогону, и без отсрочки каждый хвостовой вердикт
    давал бы пару событий ``resolved``/``reopened`` на прогон.

    Три следствия, которые иначе читаются как баги:

    * **возврат обнуляет серию, а не уменьшает её.** Чередование «видели / не
      видели» при ``close_after_misses=2`` не закроет находку никогда — она
      останется открытой и тихой. Это размен осознанный: гистерезис превращает
      флап не в «закрываем медленнее», а в «не закрываем и не шумим». Человеку
      остаётся ``finding_dismiss``;
    * **промах не эмитит событие и не переиндексирует находку.** Ни статус, ни
      содержимое не изменились, а событие на каждый промах вернуло бы ровно тот
      шум, ради которого всё затевается. Правило «каждый write оставляет след»
      (ADO-040) этим не нарушается: бумп счётчика — не переход состояния,
      видимого наружу;
    * **промах не трогает ``last_seen_at`` и ``times_seen``** — это отметки о
      настоящем наблюдении, их единственный писатель ``ingest_findings``.

    Счётчик пишет только эта функция (обнуляет — ``_set_status``). В
    ``ingest_findings`` сбрасывать его не надо и нельзя: ``on_conflict_do_update``
    идёт Core-DML мимо ORM, объект в identity map остался бы со старым
    значением, и следующий ``select`` отдал бы протухший атрибут. Да и незачем:
    оба вызывающих строят ``seen_fingerprints`` из того же списка seeds, поэтому
    заинжестенный отпечаток попадает в ветку «видели» и обнуляется там.
    """
    if close_after_misses < 1:
        # Ноль закрывал бы находку, которую производитель только что видел.
        raise ValueError("close_after_misses must be >= 1")

    # Список, а не курсор: внутри цикла идёт flush и мутации строк.
    rows = list(
        session.execute(
            select(FindingModel).where(
                FindingModel.project_id == project_id,
                FindingModel.source == source,
                FindingModel.source_ref == source_ref,
            )
        ).scalars()
    )

    resolved = 0
    reopened = 0
    missed = 0
    skipped = 0
    for f in rows:
        if unjudged_fingerprints and f.fingerprint in unjudged_fingerprints:
            # Прогон об этой находке судить не смог — молчание не улика
            # против неё. Отсутствие в `seen_fingerprints` тут не значит
            # «вылечено».
            skipped += 1
            continue
        seen = f.fingerprint in seen_fingerprints
        if f.status == FINDING_STATUS_OPEN and not seen:
            if f.miss_streak + 1 >= close_after_misses:
                _set_status(
                    session, f, FINDING_STATUS_RESOLVED, author=author, event="finding.resolved"
                )
                resolved += 1
            else:
                f.miss_streak += 1
                missed += 1
        elif f.status == FINDING_STATUS_OPEN and seen:
            f.miss_streak = 0
        elif f.status == FINDING_STATUS_RESOLVED and seen:
            _set_status(session, f, FINDING_STATUS_OPEN, author=author, event="finding.reopened")
            reopened += 1

    return {
        "resolved": resolved,
        "reopened": reopened,
        "missed": missed,
        "skipped": skipped,
    }


def _set_status(
    session: Session,
    f: FindingModel,
    status: str,
    *,
    author: str,
    event: str,
) -> None:
    """Сменить статус находки со следом: ревизий у находок нет, есть событие."""
    f.status = status
    # Серия промахов живёт только у открытой находки. Обнуление здесь, а не у
    # вызывающего, держит инвариант «не `open` → 0» без единого исключения.
    f.miss_streak = 0
    session.flush()
    activity_service.emit(
        session,
        f.project_id,
        event,
        actor_kind=actor_kind_for_author(author),
        actor_id=author,
        scope_kind="finding",
        scope_id=f.finding_uid,
        payload={"finding_id": f.row_id, "source": f.source, "source_ref": f.source_ref},
        summary=f"Finding {f.finding_uid} → {status} by {author}",
    )
    # Закрытая находка уходит из поиска по той же причине, что и dismissed
    # (CUR-012): она уже не работа, и в выдаче `ctx_search` ей делать нечего.
    search_service.index_finding(session, f)
