"""RFC 25 §3.5 (CUR-016): «doc card» куратора — один вызов вместо четырёх.

Аналог task card (:func:`cod_doc.services.agent_service._build_task_card`),
но для санитарии документации. Куратор на профиле ``agent`` до сих пор был
обязан сам сшивать четыре источника — ``ctx_drift``, ссылки гейта, реестр
хэшей ``MASTER.md`` и открытые findings — и сам решать, за что браться
первым. ``next()`` собирает их в одну карточку и отдаёт очередь с уже
проставленным приоритетом и готовой командой на каждый пункт.

Модуль **read-only**: ни одной мутации, поэтому вызывающий держит
``transactional(sf, commit=False)``. Activity event не эмитится осознанно —
эмитить нечего (см. ``tests/services/test_activity_write_path.py``: гейт
смотрит write-сервисы, а не сборщики контекста).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy.orm import Session

#: Потолок скиллов в карточке — тот же бюджет контекста, что у task card.
_SKILL_CAP = 4

#: Скиллы куратора: база (``orchestrator``) плюс три профильных. Порядок
#: значим — ``orchestrator`` всегда первый, как и в task card.
_CURATOR_SKILLS: tuple[str, ...] = (
    "orchestrator",
    "drift-handling",
    "ground-truth-reconcile",
    "doc-style",
)

#: Сколько открытых findings тянуть в карточку. Очередь всё равно режется
#: ``limit``, но карточка остаётся обозримой даже при большом хвосте.
_FINDINGS_CAP = 50

# --------------------------------------------------------------------- #
# Порядок очереди. Числа — ранги сортировки, не «важность в процентах».  #
# Шкала одна на все виды находок, поэтому и живёт одной таблицей:        #
# missing > edited_in_place > LINK-BROKEN > hash BROKEN > hash STALE >   #
# stale_export > finding.                                               #
# --------------------------------------------------------------------- #
_RANK_DRIFT_MISSING = 0
_RANK_DRIFT_EDITED = 1
_RANK_LINK_BROKEN = 2
_RANK_MASTER_BROKEN = 3
_RANK_MASTER_STALE = 4
_RANK_DRIFT_STALE_EXPORT = 5
# ADO-116: неразложенные документы. Ниже всего, что рвёт целостность, — они
# находимы поиском и не теряются, — но выше внешних находок: пока корпус не
# разложен, навигация по нему не работает, а RFC 25 §3.1 прямо относит
# «неклассифицированный import» к работе куратора.
_RANK_UNPLACED = 6
_RANK_FINDING = 7

_DRIFT_RANK: dict[str, int] = {
    "missing": _RANK_DRIFT_MISSING,
    "edited_in_place": _RANK_DRIFT_EDITED,
    "stale_export": _RANK_DRIFT_STALE_EXPORT,
}

_MASTER_RANK: dict[str, int] = {
    "BROKEN": _RANK_MASTER_BROKEN,
    "STALE": _RANK_MASTER_STALE,
}

#: Подставляется в команды, когда слаг проекта вызывающим не передан.
_SLUG_PLACEHOLDER = "<project>"

#: Имя самого дорогого раздела карточки: его сбор обходит каждую секцию
#: корпуса, и только он умеет не собираться (``skip_links``).
_SECTION_LINKS = "links"

_NEXT_ACTIONS: tuple[str, ...] = (
    "Прочитай тела скиллов из navigation.applicable_skills — они задают протокол.",
    "Бери priority[0]: в нём уже лежит готовая команда (suggested_action).",
    "Правку файла фиксируй в БД: `cod-doc doc import <path> -p <slug>`, не наоборот.",
    "После правки тела документа обнови реестр: `cod-doc hash update MASTER.md`.",
    "Повторный curator_next(project=...) безопасен: карточка read-only.",
    "Нужна политика человека — agent_report(kind='approval_request', ...).",
)

_SUCCESS_CRITERIA: tuple[str, ...] = (
    "ctx_drift(project) не показывает missing / edited_in_place / stale_export.",
    "Ни одной нерезолвящейся ссылки (LINK-BROKEN) в затронутых документах.",
    "Реестр хэшей MASTER.md без BROKEN / STALE.",
    "Открытые findings либо промоутнуты в задачу, либо сняты с обоснованием.",
    "Инбокс дерева документации пуст: каждый документ лежит в разделе.",
)


def _skills_with_bodies() -> list[dict[str, Any]]:
    """Скиллы куратора с ПОЛНЫМИ телами (не только описаниями).

    Инлайн тел — то же обоснование, что и в task card: агент не должен
    звать ``skill_get`` отдельно, чтобы прочитать правила drift-handling.
    Неизвестное имя молча пропускается — каталог скиллов поставляется
    пакетом и может отстать от этого списка.
    """
    from cod_doc.services.skill_service import get_skill_body, list_skills

    by_name = {s["name"]: s for s in list_skills()}
    out: list[dict[str, Any]] = []
    for name in _CURATOR_SKILLS[:_SKILL_CAP]:
        record = by_name.get(name)
        if record is None:
            continue
        out.append(
            {
                "name": name,
                "description": (record.get("description") or "").strip(),
                "body": get_skill_body(name) or "",
            }
        )
    return out


def _drift_card(
    session: Session,
    project_id: int,
    root_path: Path,
) -> dict[str, Any]:
    """Дрейф проекта в форме MCP-тула ``ctx_drift`` (минус ключ ``project``).

    Слаг сюда не приезжает — сервис знает только ``project_id``; поверхности
    (``curator_next`` / ``cod-doc ctx next``) дописывают его сами.
    """
    from cod_doc.services import projection_service

    report = projection_service.detect_project_drift(session, project_id, root_path=root_path)
    return {
        "total_docs": report.total_docs,
        "problem_count": report.problem_count,
        "counts": report.counts,
        "issues": [
            {
                "doc_key": item.doc_key,
                "path": item.path,
                "status": item.report.status.value,
                "projection_hash": item.report.projection_hash,
                "db_content_hash": item.report.db_content_hash,
                "file_hash": item.report.file_hash,
                # ADO-213: без этого ключа документ попадал в список проблем
                # со статусом `in_sync` и без единой причины — сирота секции
                # поднимает `problem_count`, но объяснить себя не могла.
                # Остальные три места сериализации (`doc_tools`) его несут.
                "orphan_sections": list(item.report.orphan_sections),
            }
            for item in report.issues
        ],
    }


def _link_card(session: Session, project_id: int) -> list[dict[str, Any]]:
    """Нерезолвящиеся ссылки/якоря — тот же сборщик, что у drift-гейта PR."""
    from cod_doc.services import doc_service, drift_gate_service

    docs = doc_service.list_for_project(session, project_id)
    return [
        {
            "path": f.path,
            "doc_key": f.doc_key,
            "anchor": f.anchor,
            "code": f.code,
            "severity": f.severity,
            "title": f.title,
            "body": f.body,
        }
        for f in drift_gate_service.link_findings(session, docs)
    ]


def _master_card(master_path: Path, root_path: Path) -> list[dict[str, str]]:
    """Реестр гибридных ссылок MASTER.md: находки ``BROKEN`` / ``STALE``."""
    from cod_doc.core.hash_calc import check_stale_refs

    return check_stale_refs(master_path, repo_root=root_path)


def _unplaced_card(session: Session, project_id: int) -> dict[str, Any]:
    """Сколько документов не разложено по разделам дерева (ADO-116).

    Ключи не перечисляем: их бывает сотня после хаотичного импорта, а
    действие на всех одно — прогнать раскладку. Список смотрят
    ``doc_tree_unplaced`` и Инбокс на экране документации.
    """
    from cod_doc.services import doc_tree_service

    return {
        "count": doc_tree_service.unplaced_count(session, project_id),
        "tree_seeded": bool(doc_tree_service.list_nodes(session, project_id)),
    }


def _findings_card(session: Session, project_id: int) -> list[dict[str, Any]]:
    """Открытые внешние находки (RFC 22): ai_review / zairgrush / routines."""
    from cod_doc.services.finding_service import list_findings

    return list_findings(session, project_id, status="open", limit=_FINDINGS_CAP)


def _drift_priority(issue: dict[str, Any], slug: str) -> tuple[int, dict[str, str]] | None:
    """Пункт очереди по одной drift-находке, или ``None`` для ``in_sync``.

    ``in_sync`` попадает в ``issues`` только из-за расхождения frontmatter
    (ADO-092) — это advisory, в очередь действий его не ставим.
    """
    status = str(issue["status"])
    rank = _DRIFT_RANK.get(status)
    if rank is None:
        return None
    doc_key = str(issue["doc_key"])
    path = str(issue["path"])
    if status == "edited_in_place":
        reason = f"файл {path} правился на диске — правка не доехала до БД"
        action = f"cod-doc doc import {path} -p {slug}"
    elif status == "missing":
        reason = f"документ есть в БД, файла {path} нет на диске"
        action = f"cod-doc doc export {doc_key} -p {slug}"
    else:  # stale_export
        reason = f"в БД есть изменения, которых нет в файле {path}"
        action = f"cod-doc doc export {doc_key} -p {slug}"
    return rank, {
        "kind": "drift",
        "ref": doc_key,
        "reason": reason,
        "suggested_action": action,
    }


def _link_priority(link: dict[str, Any], slug: str) -> tuple[int, dict[str, str]]:
    """Пункт очереди по одной нерезолвящейся ссылке."""
    doc_key = str(link["doc_key"])
    anchor = link.get("anchor")
    ref = f"{doc_key}#{anchor}" if anchor else doc_key
    return _RANK_LINK_BROKEN, {
        "kind": "link",
        "ref": ref,
        "reason": str(link["title"]),
        "suggested_action": (
            f'link_verify(project="{slug}", doc_key="{doc_key}", anchor="{anchor}")'
        ),
    }


def _master_priority(
    finding: dict[str, str],
    master_rel: str,
) -> tuple[int, dict[str, str]] | None:
    """Пункт очереди по одной записи реестра хэшей MASTER.md.

    ``STALE`` лечится пересчётом реестра. ``BROKEN`` — нет: пересчёт только
    предупредит, файл всё равно отсутствует, поэтому скилл ``drift-handling``
    велит заводить задачу восстановления, а первый шаг к ней — найти, где
    файл потерялся.
    """
    status = finding["status"]
    rank = _MASTER_RANK.get(status)
    if rank is None:
        return None
    path = finding["path"]
    if status == "STALE":
        reason = f"хэш {path} в реестре MASTER.md устарел (ожидался {finding['expected']})"
        action = f"cod-doc hash update {master_rel}"
    else:  # BROKEN
        reason = f"реестр MASTER.md ссылается на {path}, файла нет на диске"
        action = f"git log --diff-filter=D --oneline -- {path}"
    return rank, {
        "kind": "master",
        "ref": path,
        "reason": reason,
        "suggested_action": action,
    }


def _finding_priority(finding: dict[str, Any], slug: str) -> tuple[int, dict[str, str]]:
    """Пункт очереди по одной открытой внешней находке."""
    uid = str(finding["finding_uid"])
    return _RANK_FINDING, {
        "kind": "finding",
        "ref": uid,
        "reason": f"{finding.get('severity', 'unknown')}: {finding.get('title', '')}".strip(),
        "suggested_action": f'finding_get(project="{slug}", finding_uid="{uid}")',
    }


def _unplaced_priority(card: dict[str, Any], slug: str) -> tuple[int, dict[str, str]] | None:
    """Один пункт на весь Инбокс: действие на всех неразложенных — одно.

    Пока дерево не засеяно, «не разложено» означает лишь «разделов нет» —
    тогда и предлагать надо сев, а не раскладку.
    """
    count = int(card["count"])
    if not count:
        return None
    if not card["tree_seeded"]:
        return _RANK_UNPLACED, {
            "kind": "unplaced",
            "ref": f"{count} docs",
            "reason": "дерево разделов не заведено — весь корпус вне навигации",
            "suggested_action": f'doc_tree_init(project="{slug}")',
        }
    return _RANK_UNPLACED, {
        "kind": "unplaced",
        "ref": f"{count} docs",
        "reason": f"{count} документов не разложено по разделам — Инбокс не разобран",
        "suggested_action": f'doc_tree_classify(project="{slug}", dry_run=true)',
    }


def _build_priority(
    card: dict[str, Any],
    *,
    slug: str,
    master_rel: str,
) -> list[dict[str, str]]:
    """Свести четыре источника в одну очередь и отсортировать по рангу.

    Сортировка стабильная: внутри одного ранга порядок остаётся тем, в
    котором находки пришли из своих источников (drift — порядок документов,
    ссылки — порядок секций, findings — newest-seen first).
    """
    drift_items = (_drift_priority(issue, slug) for issue in card["drift"]["issues"])
    master_items = (_master_priority(entry, master_rel) for entry in card["master"])
    ranked: list[tuple[int, dict[str, str]]] = [item for item in drift_items if item is not None]
    ranked.extend(_link_priority(link, slug) for link in card["links"])
    ranked.extend(item for item in master_items if item is not None)
    ranked.extend(_finding_priority(f, slug) for f in card["findings"])
    unplaced = _unplaced_priority(card["unplaced"], slug)
    if unplaced is not None:
        ranked.append(unplaced)
    ranked.sort(key=lambda pair: pair[0])
    return [item for _rank, item in ranked]


# Имя из контракта RFC 25 §3.5. Встроенный `next` в этом модуле не нужен, а
# зовут функцию всегда через модуль — `curator_service.next(...)`.
def next(
    session: Session,
    *,
    project_id: int,
    root_path: Path,
    master_path: Path,
    limit: int = 10,
    project_slug: str | None = None,
    skip_links: bool = False,
) -> dict[str, Any]:
    """Собрать «doc card» куратора: что протухло и за что браться первым.

    Args:
        session: открытая сессия; вызывающий держит ``commit=False``.
        project_id: ``project.row_id`` — сервис не резолвит слаги.
        root_path: корень чекаута проекта (для сверки файлов на диске).
        master_path: путь к ``MASTER.md`` с реестром гибридных ссылок.
        limit: сколько пунктов очереди вернуть; остальное отражено в
            ``meta.truncated`` и ``meta.counts.priority_total``.
        project_slug: слаг для подстановки в ``suggested_action``. Без него
            в командах остаётся плейсхолдер ``<project>`` — карточка
            собирается, но копипастить её команды нельзя.
        skip_links: не собирать раздел ``links``. Сборка обходит КАЖДУЮ
            секцию корпуса и на каждой зовёт ``resolve_section`` — на
            больших проектах она доминирует по времени во всём вызове,
            а вызывающему, который ссылки чинить не собирается (``cod-doc
            update --skip-links``), этот обход не нужен вовсе. Умолчание
            ``False``: диагност общий, и ни ``curator_next``, ни drift-гейт
            PR своего поведения не меняют.

    Returns:
        ``{"card": {drift, links, master, findings, unplaced},
        "priority": [...], "navigation": {...}, "meta": {...}}``. Карточка —
        полный срез, ``priority`` — усечённая очередь действий по нему.

        ``meta["not_collected"]`` перечисляет разделы карточки, которые не
        собирались, и появляется только когда такие есть. Пустой
        ``card["links"]`` при ``"links"`` в этом списке означает «не
        смотрели», а не «ссылки в порядке» — без такого признака следующий
        читатель принял бы одно за другое. Читать через
        ``meta.get("not_collected", [])``: у полной карточки ключа нет вовсе,
        и это намеренно — её форма не меняется.
    """
    slug = project_slug or _SLUG_PLACEHOLDER
    try:
        master_rel = str(master_path.relative_to(root_path))
    except ValueError:
        master_rel = str(master_path)

    card: dict[str, Any] = {
        "drift": _drift_card(session, project_id, root_path),
        "links": [] if skip_links else _link_card(session, project_id),
        "master": _master_card(master_path, root_path),
        "findings": _findings_card(session, project_id),
        "unplaced": _unplaced_card(session, project_id),
    }
    priority = _build_priority(card, slug=slug, master_rel=master_rel)

    meta: dict[str, Any] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "truncated": len(priority) > limit,
        "counts": {
            "drift_issues": len(card["drift"]["issues"]),
            "links": len(card["links"]),
            "master": len(card["master"]),
            "findings": len(card["findings"]),
            "unplaced": card["unplaced"]["count"],
            "priority_total": len(priority),
        },
    }
    if skip_links:
        # Ключ появляется ТОЛЬКО когда есть о чём сообщить: полная карточка
        # обязана остаться байт в байт прежней (`tests/cli/test_ctx.py`
        # пришпиливает набор ключей `meta`), да и в контекст агента лишние
        # байты идут за токены. Читать — через `meta.get("not_collected", [])`.
        meta["not_collected"] = [_SECTION_LINKS]

    return {
        "card": card,
        "priority": priority[:limit],
        "navigation": {
            "applicable_skills": _skills_with_bodies(),
            "next_actions": list(_NEXT_ACTIONS),
            "success_criteria": list(_SUCCESS_CRITERIA),
        },
        "meta": meta,
    }
