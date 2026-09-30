"""ACU-003 (RFC 28 §3.1, §3.8): выгрузка проекций в собственный клон куратора и PR.

Куратор никогда не пишет в чекаут владельца. Всё, что он выгрузил, едет
одним PR синхронизации из его собственного клона
(``~/.cod-doc/curator/<slug>/``), а мерж остаётся за человеком.

Устройство и почему так:

- **Клон, а не ``git worktree``.** Worktree записал бы метаданные в ``.git``
  владельца; отдельный клон с того же ``origin`` не трогает его вовсе.
- **Ветка ``curator/sync`` от ``origin/<base>``.** Пока ``<base>`` не ушёл
  вперёд, прогон дописывает коммит в ту же ветку. Ушёл — ветка пересобирается
  от свежей базы: проекция детерминирована, всё её содержимое лежит в БД, и
  потерять при пересборке нечего. Форс-пуш — только в эту ветку.
- **``projection_hash`` не двигается** (``record_projection=False``). Это
  базовая линия чекаута владельца: сдвинь её на содержимое неслитой ветки, и
  нетронутый файл владельца станет ``edited_in_place``, а следующий
  ``cod-doc update`` импортирует его поверх БД. После мержа и pull файл
  владельца равен рендеру БД — это ``in_sync`` (``detect_drift``), а обычная
  выгрузка ставит базовую линию на место.
- **Какие файлы клона «свои».** Файл, который уже равен рендеру, не
  трогается. Переписывается файл, которого нет; файл, который эта ветка уже
  меняла (``git diff <base>...HEAD``); файл, совпадающий с последней выгрузкой
  или принятым импортом. Любой другой — правка человека в ``<base>``, которой
  нет в БД: он в отчёт, а не под перезапись.
- **Удаления.** Строки удалённого документа уже нет; путь его файла
  сохраняет событие ``doc.deleted``. Файл по такому пути, не занятый живым
  документом, из ветки убирается — в том числе после ``question import``.
- **Git — с чистым окружением.** Переменные, по которым git находит
  репозиторий, не наследуются (ADO-212): из pre-push хука ``GIT_DIR``
  направил бы команды в чужой репозиторий.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from sqlalchemy import select

from cod_doc.infra.models import ActivityEventModel, DocumentModel
from cod_doc.services import activity_service, doc_service, hash_service
from cod_doc.services.projection_service import (
    ExportGuardError,
    PathEscapeError,
    export_document,
    render_markdown,
)

if TYPE_CHECKING:
    from pathlib import Path

    from sqlalchemy import ScalarResult
    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document

SYNC_BRANCH = "curator/sync"
EVENT_KIND = "curator.synced"
DEFAULT_AUTHOR = "agent:curator"

#: Внутри клона remote всегда называется так — его создаёт ``git clone``.
_CLONE_REMOTE = "origin"

#: ADO-212: тот же набор, что сбрасывают scripts/gate.sh и tests/conftest.py.
_GIT_REPO_LOCATORS = frozenset(
    {
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_COMMON_DIR",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_PREFIX",
        "GIT_NAMESPACE",
    }
)

#: Автор коммитов синхронизации — различим в истории, не выдаёт себя за человека.
_IDENTITY = {
    "GIT_AUTHOR_NAME": "cod-doc curator",
    "GIT_AUTHOR_EMAIL": "curator@cod-doc.invalid",
    "GIT_COMMITTER_NAME": "cod-doc curator",
    "GIT_COMMITTER_EMAIL": "curator@cod-doc.invalid",
}

_PR_TITLE = "docs(curator): синхронизация проекций"


class GitError(RuntimeError):
    """git вернул ненулевой код."""


class GitRunner(Protocol):
    def __call__(self, args: list[str], cwd: Path) -> str: ...


class PullRequests(Protocol):
    """Найти или открыть draft PR ветки; вернуть его URL."""

    def __call__(self, *, head: str, base: str, title: str, body: str, cwd: Path) -> str: ...


def run_git(args: list[str], cwd: Path) -> str:
    """git с окружением без переменных-локаторов репозитория и с автором куратора."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REPO_LOCATORS}
    env.update(_IDENTITY)
    proc = subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.returncode}")
    return proc.stdout


def gh_pull_requests(*, head: str, base: str, title: str, body: str, cwd: Path) -> str:
    """Боевой PR-клиент — `gh` через единственную точку ``gh_service``."""
    from cod_doc.services import gh_service

    return gh_service.ensure_draft_pr(head=head, base=base, title=title, body=body, cwd=cwd).url


@dataclass(slots=True)
class SyncReport:
    branch: str
    base: str
    rebuilt: bool
    exported: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    hashes_updated: int = 0
    commit_sha: str | None = None
    pr_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def export_sync(
    session: Session,
    project_id: int,
    *,
    repo_root: Path,
    clone_dir: Path,
    author: str = DEFAULT_AUTHOR,
    base: str = "main",
    remote: str = "origin",
    master_rel: str = "MASTER.md",
    git: GitRunner = run_git,
    pull_requests: PullRequests = gh_pull_requests,
) -> SyncReport:
    """Выгрузить устаревшие проекции в ветку ``curator/sync`` клона и держать по ней PR.

    ``repo_root`` — чекаут владельца; из него читается только URL ``remote``.
    """
    if base == SYNC_BRANCH:
        raise ValueError(f"база не может совпадать с веткой синхронизации {SYNC_BRANCH!r}")
    base_ref = f"{_CLONE_REMOTE}/{base}"
    rebuilt = _prepare_clone(git, repo_root, clone_dir, base_ref=base_ref, remote=remote)
    report = SyncReport(branch=SYNC_BRANCH, base=base, rebuilt=rebuilt)

    branch_paths = set(git(["diff", "--name-only", f"{base_ref}...HEAD"], clone_dir).splitlines())
    docs = doc_service.list_for_project(session, project_id)
    _export_changed(session, docs, clone_dir, branch_paths, author, report)
    _delete_removed(session, project_id, docs, clone_dir, git, report)

    master = clone_dir / master_rel
    if master.is_file():
        report.hashes_updated, _ = hash_service.update_master_hashes(
            session, project_id, master, author=author
        )

    git(["add", "--all"], clone_dir)
    if git(["status", "--porcelain"], clone_dir).strip():
        git(["commit", "--quiet", "-m", _commit_message(report)], clone_dir)
        report.commit_sha = git(["rev-parse", "HEAD"], clone_dir).strip()
        push = ["push", "--quiet", _CLONE_REMOTE, f"HEAD:refs/heads/{SYNC_BRANCH}"]
        if rebuilt:
            push.insert(1, "--force-with-lease")
        git(push, clone_dir)
    elif rebuilt:
        # Пересобранная ветка без новых правок всё равно должна уйти на remote:
        # иначе там останется старая, разошедшаяся с базой.
        git(
            ["push", "--quiet", "--force-with-lease", _CLONE_REMOTE, f"HEAD:{SYNC_BRANCH}"],
            clone_dir,
        )

    ahead = int(git(["rev-list", "--count", f"{base_ref}..HEAD"], clone_dir).strip() or "0")
    if ahead:
        report.pr_url = pull_requests(
            head=SYNC_BRANCH,
            base=base,
            title=_PR_TITLE,
            body=_pr_body(git, clone_dir, base_ref),
            cwd=clone_dir,
        )

    if report.commit_sha:
        activity_service.emit_for_write(
            session,
            project_id,
            EVENT_KIND,
            author,
            scope_kind="project",
            scope_id=SYNC_BRANCH,
            payload=report.to_dict(),
            summary=(
                f"{SYNC_BRANCH}: выгружено {len(report.exported)}, "
                f"удалено {len(report.deleted)}, пропущено {len(report.skipped)}"
            ),
        )
    return report


def _prepare_clone(
    git: GitRunner, repo_root: Path, clone_dir: Path, *, base_ref: str, remote: str
) -> bool:
    """Клон на ветке ``curator/sync``; вернуть, пересобрана ли ветка от базы.

    Пересборка — ветка на remote есть, но база ушла вперёд и больше не её
    предок. Новой ветки это не касается: ей нечего пересобирать.
    """
    if not (clone_dir / ".git").exists():
        url = git(["remote", "get-url", remote], repo_root).strip()
        clone_dir.parent.mkdir(parents=True, exist_ok=True)
        git(["clone", "--quiet", url, str(clone_dir)], clone_dir.parent)
    else:
        git(["fetch", "--quiet", "--prune", _CLONE_REMOTE], clone_dir)

    remote_branch = f"{_CLONE_REMOTE}/{SYNC_BRANCH}"
    existed = bool(git(["branch", "--remotes", "--list", remote_branch], clone_dir).strip())
    continues = existed and _is_ancestor(git, clone_dir, base_ref, remote_branch)
    start = remote_branch if continues else base_ref
    git(["checkout", "--quiet", "--force", "-B", SYNC_BRANCH, start], clone_dir)
    git(["clean", "-fdq"], clone_dir)
    return existed and not continues


def _is_ancestor(git: GitRunner, cwd: Path, ancestor: str, ref: str) -> bool:
    merge_base = git(["merge-base", ancestor, ref], cwd).strip()
    return merge_base == git(["rev-parse", ancestor], cwd).strip()


def _export_changed(
    session: Session,
    docs: list[Document],
    clone_dir: Path,
    branch_paths: set[str],
    author: str,
    report: SyncReport,
) -> None:
    for doc in docs:
        if doc.row_id is None:
            continue
        model = session.get(DocumentModel, doc.row_id)
        if model is None:
            continue
        target = clone_dir / doc.path
        if target.is_file():
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            rendered = render_markdown(session, doc.row_id)
            if current == hashlib.sha256(rendered.encode("utf-8")).hexdigest():
                continue
            ours = {h for h in (model.projection_hash, model.content_sha256_head) if h}
            if doc.path not in branch_paths and current not in ours:
                report.skipped.append(
                    {
                        "doc_key": doc.doc_key,
                        "path": doc.path,
                        "reason": "в базовой ветке файл не совпадает ни с выгрузкой, ни с "
                        "импортом — правка человека, которой нет в БД",
                    }
                )
                continue
        try:
            export_document(
                session,
                doc.row_id,
                root_path=clone_dir,
                author=author,
                force=True,
                record_projection=False,
            )
        except (ExportGuardError, PathEscapeError) as exc:
            report.skipped.append({"doc_key": doc.doc_key, "path": doc.path, "reason": str(exc)})
            continue
        report.exported.append(doc.path)


def _delete_removed(
    session: Session,
    project_id: int,
    docs: list[Document],
    clone_dir: Path,
    git: GitRunner,
    report: SyncReport,
) -> None:
    live = {doc.path for doc in docs}
    payloads: ScalarResult[dict[str, Any]] = session.execute(
        select(ActivityEventModel.payload).where(
            ActivityEventModel.project_id == project_id,
            ActivityEventModel.kind == "doc.deleted",
        )
    ).scalars()
    gone = {str(p["path"]) for p in payloads if isinstance(p, dict) and p.get("path")}
    root = clone_dir.resolve()
    for path in sorted(gone - live):
        target = (clone_dir / path).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue
        git(["rm", "--quiet", "--", path], clone_dir)
        report.deleted.append(path)


def _commit_message(report: SyncReport) -> str:
    lines = [_PR_TITLE, ""]
    lines += [f"- выгружен {path}" for path in report.exported]
    lines += [f"- удалён {path}" for path in report.deleted]
    if report.hashes_updated:
        lines.append(f"- реестр хэшей: переписано записей {report.hashes_updated}")
    return "\n".join(lines) + "\n"


def _pr_body(git: GitRunner, cwd: Path, base_ref: str) -> str:
    commits = git(["log", "--format=- %s (%h)", f"{base_ref}..HEAD"], cwd).strip()
    return (
        "Выгрузка проекций куратором cod-doc (RFC 28 §3.8). Содержимое уже в БД: "
        "PR только доставляет markdown. Ветка пересобирается от базы, когда та "
        "уходит вперёд.\n\n"
        f"Коммиты:\n{commits}\n"
    )
