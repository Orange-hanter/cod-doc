"""Тонкий клиент GitHub поверх `gh` CLI (SYM-010, RFC 22 §3.5).

Обратное направление симбиоза: SYM-009 читал из PR (`gh run download` в
`cli/cmd_ingest.py`), здесь cod-doc **пишет** в PR. Правило то же самое —
единственное место, где мы шеллимся в `gh`, чтобы поверхности (CLI, MCP)
подменяли одну функцию в тестах, а не три.

Гарантии модуля:

- **read-only по чужому рабочему дереву**: используются только `gh pr view`,
  `gh api` и `gh repo view`; ни `git`-команд, ни записи файлов;
- **идемпотентность**: :func:`upsert_marker_comment` ищет свой комментарий по
  маркеру и делает `PATCH`, а не новый `POST`. Повторный прогон обязан вернуть
  тот же ``comment_id``.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "CommentRef",
    "GhError",
    "find_marker_comment",
    "pr_changed_files",
    "resolve_repo",
    "upsert_marker_comment",
]


class GhError(RuntimeError):
    """`gh` отсутствует, не авторизован или вернул ненулевой код."""


@dataclass(slots=True, frozen=True)
class CommentRef:
    """Ссылка на комментарий PR + что именно с ним сделали."""

    comment_id: int
    url: str
    action: str  # created | updated | unchanged | skipped


def _run_gh(args: list[str], *, cwd: Path | None = None) -> str:
    """Выполнить `gh` и вернуть stdout; любая неудача → :class:`GhError`."""
    cmd = ["gh", *args]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
            cwd=str(cwd) if cwd is not None else None,
        )
    except FileNotFoundError as exc:
        raise GhError("`gh` CLI не найден. Установи GitHub CLI.") from exc
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip() or str(exc)
        raise GhError(f"`gh {' '.join(args)}` завершилась с ошибкой: {stderr}") from exc
    return proc.stdout


def _repo_args(repo: str | None) -> list[str]:
    return ["--repo", repo] if repo else []


def resolve_repo(*, repo: str | None = None, cwd: Path | None = None) -> str:
    """Вернуть ``OWNER/NAME``: явный ``repo`` или вывод `gh repo view` в ``cwd``."""
    if repo:
        return repo
    out = _run_gh(["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], cwd=cwd)
    name = out.strip()
    if not name:
        raise GhError("не удалось определить репозиторий: передай --repo OWNER/NAME")
    return name


def pr_changed_files(pr: int, *, repo: str | None = None, cwd: Path | None = None) -> list[str]:
    """Список repo-относительных путей, затронутых pull request'ом."""
    out = _run_gh(["pr", "view", str(pr), *_repo_args(repo), "--json", "files"], cwd=cwd)
    payload: Any = json.loads(out or "{}")
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return []
    return [str(item["path"]) for item in files if isinstance(item, dict) and item.get("path")]


def find_marker_comment(
    pr: int,
    marker: str,
    *,
    repo: str | None = None,
    cwd: Path | None = None,
) -> dict[str, Any] | None:
    """Найти собственный комментарий PR по маркеру.

    Возвращает сырой объект комментария GitHub или ``None``. Если маркеров
    почему-то несколько (гонка двух прогонов), берётся самый старый — тогда
    последующие прогоны сходятся к одному комментарию, а не мигают между ними.
    """
    repo_name = resolve_repo(repo=repo, cwd=cwd)
    out = _run_gh(
        ["api", "--paginate", f"repos/{repo_name}/issues/{pr}/comments"],
        cwd=cwd,
    )
    payload: Any = json.loads(out or "[]")
    if not isinstance(payload, list):
        return None
    matches = [c for c in payload if isinstance(c, dict) and marker in str(c.get("body") or "")]
    if not matches:
        return None
    matches.sort(key=lambda c: int(c.get("id") or 0))
    return matches[0]


def upsert_marker_comment(
    pr: int,
    body: str,
    marker: str,
    *,
    repo: str | None = None,
    cwd: Path | None = None,
) -> CommentRef:
    """Создать или обновить комментарий PR под маркером ``marker``.

    Идемпотентность держится на маркере в теле комментария: он же и ключ
    поиска. Тело совпало байт в байт — ничего не пишем и возвращаем
    ``action="unchanged"``, чтобы прогон не плодил бесполезных правок в ленте
    чужого PR.
    """
    if marker not in body:
        raise GhError("тело комментария обязано содержать маркер идемпотентности")
    repo_name = resolve_repo(repo=repo, cwd=cwd)
    existing = find_marker_comment(pr, marker, repo=repo_name, cwd=cwd)

    if existing is not None and str(existing.get("body") or "") == body:
        return CommentRef(
            comment_id=int(existing["id"]),
            url=str(existing.get("html_url") or ""),
            action="unchanged",
        )

    # `gh api -F body=@file` читает значение из файла — иначе тело с переводами
    # строк и обратными кавычками пришлось бы экранировать для argv.
    with tempfile.TemporaryDirectory(prefix="cod-doc-drift-gate-") as td:
        body_file = Path(td) / "comment.md"
        body_file.write_text(body, encoding="utf-8")
        if existing is None:
            endpoint = f"repos/{repo_name}/issues/{pr}/comments"
            method = "POST"
            action = "created"
        else:
            endpoint = f"repos/{repo_name}/issues/comments/{int(existing['id'])}"
            method = "PATCH"
            action = "updated"
        out = _run_gh(
            ["api", "--method", method, endpoint, "-F", f"body=@{body_file}"],
            cwd=cwd,
        )

    payload: Any = json.loads(out or "{}")
    if not isinstance(payload, dict) or "id" not in payload:
        raise GhError(f"неожиданный ответ GitHub на {method} {endpoint}: {out[:200]!r}")
    return CommentRef(
        comment_id=int(payload["id"]),
        url=str(payload.get("html_url") or ""),
        action=action,
    )
