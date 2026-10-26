"""Ссылки на хостинг репозитория проекта (GitHub/GitLab) — ADO-148, ADO-142.

Карточка задачи (коммит, PR, файлы) и проза (`#N`, пути к файлам) строят
ссылки от `repo_url` проекта. Всё, что туда попадает из БД или текста
людей и агентов, проверяется здесь: не hex-sha, неположительный номер,
`..` в пути и мусор вместо адреса репозитория дают «ссылки нет», а не
битую или уводящую куда-то ссылку.
"""

from __future__ import annotations

import re
from urllib.parse import quote, urlsplit

from cod_doc.config import normalize_repo_url

_SHA_RE = re.compile(r"^[0-9a-f]{7,64}$")


def _is_gitlab(repo_url: str) -> bool:
    # urlsplit, а не split("/")[2]: правленный руками реестр не должен ронять
    # карточку IndexError'ом на адресе без схемы.
    return "gitlab" in (urlsplit(repo_url).hostname or "")


def _repo_prefix(repo_url: str | None) -> str | None:
    """Основа ссылок на хостинг; не веб-адрес репозитория — None, ссылок нет."""
    if not repo_url:
        return None
    try:
        base = normalize_repo_url(repo_url)
    except ValueError:
        return None
    return f"{base}/-" if _is_gitlab(base) else base


def commit_url(repo_url: str | None, sha: str | None) -> str | None:
    """Коммит на хостинге репозитория (ADO-148); без `repo_url` — None.

    sha в БД пишут и MCP/CLI, а не только форма: не hex — ссылки нет, иначе
    `abc/../x` увёл бы её за пределы `/commit/`.
    """
    prefix = _repo_prefix(repo_url)
    if prefix is None or not sha or not _SHA_RE.match(sha.lower()):
        return None
    return f"{prefix}/commit/{sha.lower()}"


def pr_url(repo_url: str | None, pr: int | None) -> str | None:
    """PR (GitHub) или MR (GitLab) по номеру; без `repo_url` — None."""
    prefix = _repo_prefix(repo_url)
    if prefix is None or pr is None or pr <= 0:
        return None
    return f"{prefix}/merge_requests/{pr}" if prefix.endswith("/-") else f"{prefix}/pull/{pr}"


def file_url(repo_url: str | None, ref: str | None, path: str) -> str | None:
    """Файл в репозитории на коммите закрытия (иначе — на HEAD ветки по умолчанию).

    Путь из `affects_files` — произвольная строка автора задачи: сегменты
    `.`/`..` выбрасываются (браузер нормализовал бы их и увёл ссылку за
    `/blob/`), остальные кодируются — `#` и `?` не обрезают путь.
    """
    prefix = _repo_prefix(repo_url)
    segments = [quote(seg, safe="") for seg in path.split("/") if seg not in {"", ".", ".."}]
    if prefix is None or not segments:
        return None
    sha = ref.lower() if ref and _SHA_RE.match(ref.lower()) else "HEAD"
    return f"{prefix}/blob/{sha}/{'/'.join(segments)}"


def issue_url(repo_url: str | None, number: int | None) -> str | None:
    """`#N` из прозы: issue (GitHub перенаправляет на PR с тем же номером).

    У GitLab `#N` — issue, у MR своя нумерация (`!N`): ссылка на issues.
    """
    prefix = _repo_prefix(repo_url)
    if prefix is None or number is None or number <= 0:
        return None
    return f"{prefix}/issues/{number}"
