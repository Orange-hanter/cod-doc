"""RevertService — откат ревизии через сервис-владелец сущности (COD-022).

Вынесен из `revision_service` (SVC-002). Журнал ревизий — лист, на нём
держатся все write-сервисы; откат же вызывает `task_service`,
`doc_service` и `task_doc_service`. В одном модуле это было кольцо
«write-сервис → revision_service → write-сервис», которое держалось на
импортах в телах функций. Здесь зависимость одна и направлена вниз:
revert_service → сервисы-владельцы → revision_service.

Откат пишется новой ревизией (историю не правит). Поддержаны:
TASK (op=status/complete/affects_files), SECTION (обратный unified diff),
DOCUMENT (op=rename), TASK_DOC (снимок через `task_doc_service.revert`).
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from cod_doc.domain.entities import EntityKind, Revision, TaskStatus
from cod_doc.infra.models import (
    DocumentModel,
    RevisionModel,
    SectionModel,
    TaskDocumentModel,
    TaskModel,
)
from cod_doc.services import doc_service as _docs
from cod_doc.services import task_doc_service
from cod_doc.services import task_service as _tasks
from cod_doc.services.revision_service import (
    RevertNotSupportedError,
    RevisionNotFoundError,
    _revision_in_project,
    _to_domain,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


def revert(session: Session, revision_id: str, *, author: str, project_id: int) -> Revision:
    """Undo a revision by delegating to the entity-owning service.

    Dispatches based on `entity_kind` + the `op` field in `revision.diff`:

    * **TASK** — `op=status`: restore old status via TaskService.update_status.
      `op=complete`: same (restores `old_status` from diff).
      `op=affects_files`: restore the old set via
      TaskService.update_affects_files(mode='replace') (AFT-021).
    * **SECTION** — unified-diff body: restore old body via difflib.restore +
      DocService.patch_section.
    * **DOCUMENT** — `op=rename`: restore old doc_key via DocService.rename.

    The inverse operation is written as a new revision (not amending history).
    Raises `RevertNotSupportedError` for ops or entity kinds not listed above.

    ``project_id`` обязателен и не принимает ``None``, как у `get` (AFT-022):
    ревизия чужого проекта общей hub-БД ищется как отсутствующая
    (`LookupError`), а не откатывается.
    """
    model = _revision_in_project(session, revision_id, project_id)
    if model is None:
        raise RevisionNotFoundError(f"revision not found: {revision_id!r}")

    kind = EntityKind(model.entity_kind)

    if kind is EntityKind.TASK:
        _revert_task(session, model, author=author, project_id=project_id)

    elif kind is EntityKind.SECTION:
        _revert_section(session, model, author=author, project_id=project_id)

    elif kind is EntityKind.DOCUMENT:
        _revert_document(session, model, author=author, project_id=project_id)

    elif kind is EntityKind.TASK_DOC:
        _revert_task_doc(session, model, author=author, project_id=project_id)

    else:
        raise RevertNotSupportedError(f"revert not supported for entity_kind={kind.value!r}")

    return _to_domain(model)


# --------------------------------------------------------------------------- #
# Entity-specific revert helpers                                                #
# --------------------------------------------------------------------------- #


def _require_same_project(
    model: RevisionModel, entity: str, owner_project_id: int, project_id: int
) -> None:
    """Сущность ревизии обязана лежать в проекте вызывающего (AFT-022).

    `revert` нашёл ревизию в скоупе ``project_id``, но сущность достаётся по
    ``entity_id`` — при рассинхроне (легаси-импорт, ручная правка БД) откат
    иначе лёг бы на объект чужого проекта. Общий сторож для всех видов
    сущностей, чтобы скоуп не был асимметричным.
    """
    if owner_project_id != project_id:
        raise LookupError(f"{entity} #{model.entity_id} not found in project #{project_id}")


def _task_of_revision(session: Session, model: RevisionModel, project_id: int) -> TaskModel:
    """Задача TASK-ревизии — только если она в проекте вызывающего ``project_id``.

    `revert` уже нашёл ревизию в скоупе ``project_id``; задача достаётся по
    ``entity_id``, и её проект сверяется с тем же ``project_id`` ещё раз — при
    рассинхроне (легаси-импорт, ручная правка БД) откат иначе молча лёг бы на
    задачу чужого проекта.
    """
    task_model = session.get(TaskModel, model.entity_id)
    if task_model is None:
        raise LookupError(f"task #{model.entity_id} not found (may have been deleted)")
    _require_same_project(model, "task", task_model.project_id, project_id)
    return task_model


def _revert_task(session: Session, model: RevisionModel, *, author: str, project_id: int) -> None:
    diff_obj = json.loads(model.diff)
    op = diff_obj.get("op")

    if op == "affects_files":
        old_paths = diff_obj.get("old")
        if not isinstance(old_paths, list) or not all(isinstance(p, str) for p in old_paths):
            raise RevertNotSupportedError(
                f"TASK revision {model.revision_id}: op=affects_files без списка путей в 'old'"
            )
        _revert_task_affects_files(
            session, model, old_paths=old_paths, author=author, project_id=project_id
        )
        return
    if op == "status":
        old_status = TaskStatus(diff_obj["old"])
    elif op == "complete":
        old_status = TaskStatus(diff_obj["old_status"])
    else:
        raise RevertNotSupportedError(f"cannot auto-revert TASK revision with op={op!r}")

    task_model = _task_of_revision(session, model, project_id)

    _tasks.update_status(
        session,
        task_id=task_model.task_id,
        new_status=old_status,
        author=author,
        reason=f"revert revision {model.revision_id}",
        # Reverts may produce transitions that aren't in the forward state
        # machine (e.g. in_progress → pending). Bypass validation since the
        # original transition was already validated when first applied.
        force=True,
        project_id=project_id,
    )


def _revert_task_affects_files(
    session: Session, model: RevisionModel, *, old_paths: list[str], author: str, project_id: int
) -> None:
    """AFT-021: вернуть набор ``affects_files``, сохранённый в ревизии как ``old``.

    Ревизия хранит набор целиком, поэтому откат — ``replace`` на него, какой
    бы режим ни был у исходной правки. Если набор с тех пор уже равен
    ``old``, сервис отработает no-op и новой ревизии не будет. Набор, который
    нынешнее правило путей отвергает, — `RevertNotSupportedError` с
    ``revision_id``.
    """
    task_model = _task_of_revision(session, model, project_id)

    # Набор из ревизии проверяется до правки и отдельно: ревизия могла
    # прийти из легаси-импорта или быть старше `normalize_affected_paths`.
    # Оборачивается только этот отказ — прочие ValueError сервиса остаются
    # собой и не маскируются под «откат не поддержан».
    try:
        _tasks.normalize_affected_paths(old_paths)
    except ValueError as exc:
        raise RevertNotSupportedError(
            f"TASK revision {model.revision_id}: набор 'old' не проходит правило путей — {exc}"
        ) from exc

    _tasks.update_affects_files(
        session,
        task_id=task_model.task_id,
        paths=old_paths,
        mode="replace",
        author=author,
        reason=f"revert revision {model.revision_id}",
        project_id=project_id,
    )


_HUNK_HEADER_RE = re.compile(r"@@[^@]+@@")


def _restore_original_from_unified(diff: str) -> str:
    """Extract the 'from' content (body BEFORE the patch) of a unified diff.

    Handles the storage format produced by DocService._unified_diff, where
    difflib.unified_diff is called with lineterm='' + "".join(). This means
    the three header lines (---, +++, @@) are NOT separated by newlines from
    each other or from the first content line — the only newlines come from
    the content itself.

    Strategy: locate the end of the last @@ hunk-header, then parse the
    content lines that follow. Content lines start with +, -, space, or \\
    and run to the next newline (or end of string).
    """
    m = _HUNK_HEADER_RE.search(diff)
    if not m:
        return ""

    content = diff[m.end() :]
    # Each content line starts with a diff prefix and runs to the next '\n'.
    # Use re.findall with a sentinel '\n' appended to catch the final line.
    content_lines = re.findall(r"(?:[-+ \\][^\n]*)(?:\n|$)", content + "\n")

    result = []
    for line in content_lines:
        if line.startswith(("-", "---")):
            # '-' alone is an original line; '---' is a nested diff header (skip).
            if not line.startswith("---"):
                result.append(line[1:])
        elif line.startswith(" "):
            result.append(line[1:])
        # '+' lines are new-only → skip
    return "".join(result)


#: Заголовок SECTION-ревизии: ``--- <откуда>+++ section:<doc_key>#<anchor>@@ -…``
#: (`doc_service.section_label`; строки заголовка склеены — ``lineterm=""``,
#: и сразу за куда-меткой идёт заголовок ханка ``@@ -``). Сверяется только
#: заголовок и только с начала diff: тело секции может само содержать текст
#: вида ``section:x#y`` и подменило бы метку (ai-review #85).
_HUNK_START_RE = re.compile(r"@@ -\d")
_SECTION_LABEL_RE = re.compile(r"--- (?:/dev/null|section:.*)\+\+\+ section:[^#]*#([^@]+)$")


def _section_label_anchor(diff: str) -> str | None:
    """Якорь куда-метки SECTION-ревизии или ``None``, если её не прочитать однозначно.

    Ни ``doc_key``, ни якорь не валидируются, поэтому метка может содержать
    ``+`` (``c++-guide``) и даже ``+++ section:``. Регулярка не должна угадывать
    границу (ai-review #192: ленивый разбор отдавал якорь из подстроки
    старого якоря, жадный — из подстроки нового): заголовок берётся до начала
    ханка, и если ``+++ section:`` в нём не ровно один — разбор отказывает, а
    откат — вместе с ним. Отказ безопасен: ревизия просто не откатывается.
    """
    hunk = _HUNK_START_RE.search(diff)
    if hunk is None:
        return None
    header = diff[: hunk.start()]
    if "\n" in header or header.count("+++ section:") != 1:
        return None
    labelled = _SECTION_LABEL_RE.match(header)
    return labelled.group(1) if labelled else None


def _revert_section(
    session: Session, model: RevisionModel, *, author: str, project_id: int
) -> None:
    """Restore section body by reversing the stored unified diff.

    ADO-213: ``section.row_id`` без AUTOINCREMENT, и после удаления секции
    SQLite может отдать её id следующей новой — старые ревизии удалённой
    секции тогда числятся за новой. Откат такой ревизии записал бы старое тело
    в чужую секцию. Сторож сверяет якорь из метки ревизии с якорем строки;
    ``doc_key`` не сверяется намеренно — он меняется при переименовании
    документа, и легитимные старые ревизии тогда отказывали бы.
    """
    old_body = _restore_original_from_unified(model.diff)

    sec = session.get(SectionModel, model.entity_id)
    if sec is None:
        raise LookupError(f"section #{model.entity_id} not found")
    doc = session.get(DocumentModel, sec.document_id)
    if doc is None:
        raise LookupError(f"section #{model.entity_id}: document #{sec.document_id} not found")
    _require_same_project(model, "section", doc.project_id, project_id)
    # Без метки принадлежность не доказать — и такой ревизии на практике нет:
    # оба писателя (`add_section`, `patch_section`) метят diff, без метки
    # остаётся лишь пустой diff секции, созданной с пустым телом.
    written_anchor = _section_label_anchor(model.diff)
    if written_anchor is None or written_anchor != sec.anchor:
        written_for = (
            f"anchor {written_anchor!r}"
            if written_anchor is not None
            else "no unambiguous section label"
        )
        raise RevertNotSupportedError(
            f"revision {model.revision_id} was written for {written_for}, but section "
            f"row {model.entity_id} now holds anchor {sec.anchor!r} — the row id was "
            "reused after a delete, or the revision cannot be matched to it (ADO-213)"
        )

    _docs.patch_section(
        session,
        project_id=project_id,
        document_id=sec.document_id,
        anchor=sec.anchor,
        new_body=old_body,
        author=author,
        reason=f"revert revision {model.revision_id}",
    )


def _revert_document(
    session: Session, model: RevisionModel, *, author: str, project_id: int
) -> None:
    doc = session.get(DocumentModel, model.entity_id)
    if doc is None:
        raise LookupError(f"document #{model.entity_id} not found")
    _require_same_project(model, "document", doc.project_id, project_id)

    diff_obj = json.loads(model.diff)
    op = diff_obj.get("op")

    if op != "rename":
        raise RevertNotSupportedError(f"cannot auto-revert DOCUMENT revision with op={op!r}")

    old_doc_key = diff_obj["from"]["doc_key"]
    old_path = diff_obj["from"]["path"]

    _docs.rename(
        session,
        project_id=project_id,
        document_id=model.entity_id,
        new_doc_key=old_doc_key,
        new_path=old_path,
        author=author,
        reason=f"revert revision {model.revision_id}",
    )


def _revert_task_doc(
    session: Session, model: RevisionModel, *, author: str, project_id: int
) -> None:
    """PCA-917: Restore task-bound doc body from a revision snapshot.

    Delegates to ``task_doc_service.revert`` which handles snapshot lookup
    and writes the inverse revision via ``put`` with optimistic locking.
    """
    td = session.get(TaskDocumentModel, model.entity_id)
    if td is None:
        raise RevertNotSupportedError(f"TASK_DOC entity #{model.entity_id} not found")
    owner = session.get(TaskModel, td.task_id)
    if owner is None:
        raise LookupError(f"task_doc #{model.entity_id}: task #{td.task_id} not found")
    _require_same_project(model, "task_doc", owner.project_id, project_id)
    task_doc_service.revert(
        session,
        project_id=project_id,
        task_row_id=td.task_id,
        key=td.key,
        revision_id=model.revision_id,
        author=author,
    )
