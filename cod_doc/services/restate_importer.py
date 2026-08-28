"""COD-051: bulk import an existing project into the cod-doc DB.

Two pipelines today (more can be added incrementally):
- ``import_docs(repo_root, project_id, …)`` walks markdown/rst/txt files in
  the repo and creates one Document per file via ``import_service``.
  Idempotent: files whose doc_key already exists are reported as ``skipped``.
- ``import_legacy_tasks(yaml_path, project_id, …)`` reads a legacy
  ``.cod-doc/tasks.yaml`` (priority is int 1-5, status uses underscores)
  and writes each entry into the DB ``task`` table under a synthetic
  "imported-legacy" plan + section. Existing tasks with the same auto-id
  prefix are not deduped here — the importer is meant to run once per
  project.

Both helpers return a small summary dict the CLI prints; on dry-run the
callers wrap the call in a savepoint and roll back instead of committing.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

import yaml

from cod_doc.domain.entities import (
    Plan,
    PlanSection,
    Priority,
    TaskStatus,
    TaskType,
)
from cod_doc.infra.repositories import (
    DocumentRepository,
    PlanRepository,
    PlanSectionRepository,
)
from cod_doc.services import import_service, task_service
from cod_doc.services.projection_service._safety import _sha256

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from sqlalchemy.orm import Session


DEFAULT_MAX_FILES = 1000
_DOC_EXTENSIONS = {".md", ".rst", ".txt", ".markdown"}
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cod-doc",
    ".chroma",
    "dist",
    "build",
    ".pytest_cache",
    ".mypy_cache",
    "cod_doc.egg-info",
}


@dataclass
class DocsSummary:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    # ADO-015: "<rel path>: type: 'journal' → 'module-spec' (unknown value)".
    # A bulk import that bends metadata has to say which file it bent.
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors,
            "files": self.files,
            "warnings": self.warnings,
        }


@dataclass
class TasksSummary:
    imported: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)
    plan_scope: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "errors": self.errors,
            "plan_scope": self.plan_scope,
        }


# ── docs ────────────────────────────────────────────────────────────────


def _normalise_patterns(raw: Sequence[str] | None) -> tuple[str, ...]:
    """Привести exclude-паттерны к форме относительного POSIX-пути.

    Снимает ведущие ``./``, переводит ``\\`` в ``/`` и обрезает крайние ``/``,
    чтобы ``./experiments/stand*/`` и ``experiments/stand*`` значили одно и то
    же. Пустые строки отбрасываются: пустой паттерн заматчил бы корень репо и
    вычистил бы весь импорт.
    """
    if not raw:
        return ()
    out: list[str] = []
    for item in raw:
        cleaned = item.strip().replace("\\", "/")
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.strip("/")
        if cleaned:
            out.append(cleaned)
    return tuple(out)


def _is_excluded(rel_posix: str, patterns: tuple[str, ...]) -> bool:
    """Проверить относительный путь файла против exclude-паттернов.

    Паттерн сверяется и с самим путём, и с каждым его каталогом-предком,
    поэтому каталожный паттерн вычищает всё поддерево: ``experiments/stand*``
    убирает и ``experiments/stand-01/notes.md``, а ``docs/*.md`` продолжает
    работать как файловый.
    """
    if not patterns:
        return False
    candidates = [rel_posix]
    candidates.extend(
        str(parent) for parent in PurePosixPath(rel_posix).parents if str(parent) != "."
    )
    return any(fnmatchcase(candidate, pat) for candidate in candidates for pat in patterns)


def _walk_doc_files(
    repo_root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    exclude: Sequence[str] | None = None,
) -> list[Path]:
    """Iterate the repo for importable doc files. Skips noisy build dirs.

    SYM-004: ``exclude`` — glob-паттерны, которые сверяются с путём файла
    **относительно** ``repo_root`` (не с абсолютным), поэтому
    ``experiments/stand*`` предсказуемо означает «каталоги стендов в корне
    репозитория», а не «где угодно на диске».

    Матчинг — ``fnmatch.fnmatchcase``, а не ``PurePath.match``: ``match``
    якорится справа и покомпонентно, поэтому
    ``PurePosixPath("experiments/stand-01/notes.md").match("experiments/stand*")``
    возвращает ``False`` — то есть паттерн из критерия приёмки молча не
    исключил бы ничего. ``PurePath.full_match`` умеет ``**``, но появился в
    3.13, а проект держит ``requires-python >=3.11``. ``fnmatchcase`` (а не
    ``fnmatch``) — чтобы не гонять ``os.path.normcase``, который на Windows
    лоуркейсит путь и меняет разделители: нужен детерминированный матч.

    ВНИМАНИЕ: это НЕ gitignore-семантика. ``*`` в fnmatch перекрывает ``/``,
    поэтому ``docs/*`` заматчит и ``docs/a/b/c.md``.

    Исключение проверяется ДО ``out.append``, поэтому отброшенные файлы не
    расходуют лимит ``max_files``.
    """
    if not repo_root.exists() or not repo_root.is_dir():
        return []
    patterns = _normalise_patterns(exclude)
    out: list[Path] = []
    for path in sorted(repo_root.rglob("*")):
        if len(out) >= max_files:
            break
        if not path.is_file() or path.suffix.lower() not in _DOC_EXTENSIONS:
            continue
        # COD-077: skip dotfiles by their own name, not just dotted parents,
        # so .gitignore.md / .env.txt don't sneak through.
        if path.name.startswith("."):
            continue
        rel = path.relative_to(repo_root)
        if any(p in _SKIP_DIRS or p.startswith(".") for p in rel.parts[:-1]):
            continue
        if _is_excluded(rel.as_posix(), patterns):
            continue
        out.append(path)
    return out


def _doc_key_for(repo_root: Path, path: Path) -> str:
    """Derive a stable doc_key from a file's path relative to the repo root."""
    rel = path.relative_to(repo_root)
    # Strip the extension — DocumentService keeps "type" in the doc_type
    # field; doc_key is the canonical identity. POSIX separators always.
    return str(rel.with_suffix("")).replace("\\", "/")


def import_docs(
    session: Session,
    *,
    repo_root: Path,
    project_id: int,
    author: str = "human:cli",
    max_files: int = DEFAULT_MAX_FILES,
    exclude: Sequence[str] | None = None,
) -> DocsSummary:
    """Walk ``repo_root`` for markdown files and import each as a Document.

    SYM-004: ``exclude`` — повторяемые glob-паттерны относительно
    ``repo_root``; семантику см. в :func:`_walk_doc_files`.

    Caller commits the transaction (or rolls back for a dry-run).
    """
    summary = DocsSummary()
    repo_doc = DocumentRepository(session)

    for path in _walk_doc_files(repo_root, max_files=max_files, exclude=exclude):
        rel = str(path.relative_to(repo_root))
        doc_key = _doc_key_for(repo_root, path)
        if repo_doc.get_by_key(project_id, doc_key) is not None:
            summary.skipped += 1
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            summary.errors.append(f"{rel}: read error — {exc}")
            continue
        try:
            # ADO-023: go through the shared import_or_update path with the
            # file's sha256, so the same transaction records
            # content_sha256_head (accepted file) and projection_hash
            # (DB-render baseline) — without them every imported doc shows as
            # stale_export in drift reports until a manual re-export.
            report = import_service.import_or_update_markdown(
                session,
                project_id=project_id,
                doc_key=doc_key,
                raw_markdown=raw,
                fallback_title=path.stem,
                author=author,
                reason=f"restate-import:{rel}",
                source_sha256=_sha256(raw),
            )
            summary.imported += 1
            summary.files.append(rel)
            summary.warnings.extend(f"{rel}: {w.describe()}" for w in report.warnings)
        except Exception as exc:
            summary.errors.append(f"{rel}: {exc}")
    return summary


# ── legacy tasks ────────────────────────────────────────────────────────


_LEGACY_PRIORITY_MAP = {
    1: Priority.CRITICAL,
    2: Priority.HIGH,
    3: Priority.MEDIUM,
    4: Priority.MEDIUM,
    5: Priority.LOW,
}


# Legacy enum (cod_doc.core.project.TaskStatus) uses underscores;
# DB enum (cod_doc.domain.entities.TaskStatus) uses hyphens for in-progress.
_LEGACY_STATUS_MAP = {
    "pending": TaskStatus.PENDING,
    "in_progress": TaskStatus.IN_PROGRESS,
    "in-progress": TaskStatus.IN_PROGRESS,
    "done": TaskStatus.DONE,
    # legacy 'failed'/'blocked' have no exact DB equivalent — fall back to
    # PENDING + a blocked_reason so the data is preserved without losing it.
    "failed": TaskStatus.PENDING,
    "blocked": TaskStatus.PENDING,
}


_IMPORT_PLAN_SCOPE = "imported-legacy"
_IMPORT_PLAN_PRINCIPLE = "from-yaml"
_IMPORT_SECTION_LETTER = "A"
_IMPORT_SECTION_SLUG = "A-Imported"
_IMPORT_TASK_PREFIX = "LEG"


def _ensure_import_plan(session: Session, project_id: int) -> tuple[int, int]:
    """Return (plan_id, section_id) for the synthetic 'imported-legacy' plan.

    Created on first call; reused on subsequent imports for the same project.
    """
    plan_repo = PlanRepository(session)
    plan = plan_repo.get_by_scope(_IMPORT_PLAN_SCOPE)
    if plan is not None and plan.project_id == project_id and plan.row_id is not None:
        plan_id = plan.row_id
    else:
        new_plan = plan_repo.add(
            Plan(
                project_id=project_id,
                scope=_IMPORT_PLAN_SCOPE,
                principle=_IMPORT_PLAN_PRINCIPLE,
            )
        )
        assert new_plan.row_id is not None
        plan_id = new_plan.row_id

    sec_repo = PlanSectionRepository(session)
    sections = sec_repo.list_for_plan(plan_id)
    if sections:
        section_id = sections[0].row_id
        assert section_id is not None
        return plan_id, section_id
    new_section = sec_repo.add(
        PlanSection(
            plan_id=plan_id,
            letter=_IMPORT_SECTION_LETTER,
            title="Imported (legacy)",
            slug=_IMPORT_SECTION_SLUG,
            position=0,
        )
    )
    assert new_section.row_id is not None
    return plan_id, new_section.row_id


def import_legacy_tasks(
    session: Session,
    *,
    yaml_path: Path,
    project_id: int,
    author: str = "human:cli",
    progress: Callable[[int, int, str], None] | None = None,
) -> TasksSummary:
    """Migrate entries from a ``.cod-doc/tasks.yaml`` into the DB.

    Each entry becomes a Task in the synthetic 'imported-legacy' plan; the
    legacy result/description are stitched into ``description`` so nothing
    is lost. Failed/blocked statuses become PENDING with the reason recorded
    in ``blocked_reason``.

    WEB-031: when ``progress`` is given, it is called once per processed entry
    with ``(done, total, label)`` so callers can stream live import progress
    (e.g. over the project WebSocket). The callback must not raise — any
    exception is suppressed so it can never break the migration.
    """
    summary = TasksSummary(plan_scope=_IMPORT_PLAN_SCOPE)
    if not yaml_path.exists():
        summary.errors.append(f"yaml not found: {yaml_path}")
        return summary
    try:
        data = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        summary.errors.append(f"yaml parse error: {exc}")
        return summary

    entries = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        summary.errors.append("yaml has no 'tasks' list at top level")
        return summary

    plan_id, section_id = _ensure_import_plan(session, project_id)
    total = len(entries)

    for idx, raw in enumerate(entries, start=1):
        label = "?"
        try:
            if not isinstance(raw, dict):
                summary.skipped += 1
                continue
            title = str(raw.get("title") or "").strip()
            label = title or str(raw.get("id") or "?")
            if not title:
                summary.skipped += 1
                continue
            priority_raw = raw.get("priority", 3)
            try:
                priority = _LEGACY_PRIORITY_MAP.get(int(priority_raw), Priority.MEDIUM)
            except (ValueError, TypeError):
                priority = Priority.MEDIUM
            legacy_status = str(raw.get("status", "pending")).strip().lower()
            status = _LEGACY_STATUS_MAP.get(legacy_status, TaskStatus.PENDING)
            blocked_reason = (
                f"legacy status: {legacy_status}"
                if legacy_status in {"failed", "blocked"}
                else None
            )
            description = str(raw.get("description") or "").strip()
            result = str(raw.get("result") or "").strip()
            legacy_id = str(raw.get("id") or "").strip()
            body_parts: list[str] = []
            if legacy_id:
                body_parts.append(f"_legacy id_: `{legacy_id}`")
            if description:
                body_parts.append(description)
            if result:
                body_parts.append(f"**Result**\n{result}")
            full_description = "\n\n".join(body_parts) or None

            # COD-071: savepoint per legacy entry — a single bad row should not
            # take down the rest of the migration batch.
            try:
                with session.begin_nested():
                    task = task_service.create(
                        session,
                        project_id=project_id,
                        plan_id=plan_id,
                        section_id=section_id,
                        title=title,
                        type=TaskType.CHORE,
                        priority=priority,
                        author=author,
                        id_prefix=_IMPORT_TASK_PREFIX,
                        description=full_description,
                        blocked_reason=blocked_reason,
                        allow_duplicate=True,
                        reason=f"restate-import:{legacy_id or '?'}",
                    )
                    if status != TaskStatus.PENDING:
                        task_service.update_status(
                            session,
                            task_id=task.task_id,
                            new_status=status,
                            author=author,
                            reason="restate-import:status",
                            force=True,  # import sets arbitrary legacy status
                        )
            except Exception as exc:
                summary.errors.append(f"{title!r}: {exc}")
                continue
            summary.imported += 1
        finally:
            if progress is not None:
                # progress must never break the import
                with contextlib.suppress(Exception):
                    progress(idx, total, label)
    return summary
