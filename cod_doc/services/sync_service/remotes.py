"""Remotes проекта в ``~/.cod-doc/config.yaml`` (RFC 29 §3.9, §10.12; CLO-015).

``remotes: {имя: {url, kind}}`` у записи проекта. Только конфиг: БД не
трогается, сеть не ходит, токенов нет (они — ``remote login``, CLO-038). В MCP
не выставляется намеренно (§10.7): настройка remote — дело человека.

Схемы: ``file://`` и ``s3://`` (CLO-028; креды — ``~/.cod-doc/credentials``
по URL remote, CLO-051, иначе стандартная цепочка AWS) работают; ``https://`` (CLO-035) записывается с предупреждением, но push/pull
по нему до своей задачи отвечают ``SYNC-110``. ``s3://``-URL проверяется
сразу (бакет, сегменты префикса, нет кредов в URL), в сеть ``add`` не ходит.
Неизвестная схема и ``--kind``, не совпавший со схемой, — ``SYNC-110`` сразу.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final
from urllib.parse import urlparse

from cod_doc.infra.remote.base import check_name
from cod_doc.services.sync_service.errors import RemoteConfigError

__all__ = ["KINDS", "AddedRemote", "RemoteSpec", "add", "get", "list_for", "remove"]

#: Виды remote по схеме URL; ``auto`` в ``add`` выводит вид из схемы.
KINDS: Final = ("file", "s3", "https")
AUTO: Final = "auto"

#: §10.8: ``s3://`` — плоское хранилище, роли и токены там не проверяются.
S3_WARNING: Final = "s3-remote без разграничения доступа: любой с ключами бакета читает и пишет всё"
_PENDING: Final = {
    "https": "https-remote пока не поддержан (CLO-035): fetch/pull/push ответят SYNC-110",
}


@dataclass(frozen=True, slots=True)
class RemoteSpec:
    name: str
    url: str
    kind: str


@dataclass(frozen=True, slots=True)
class AddedRemote:
    remote: RemoteSpec
    #: Замена прежнего URL под тем же именем.
    replaced: bool
    warnings: list[str] = field(default_factory=list)


def _registered(project: str) -> dict[str, dict[str, str]]:
    from cod_doc.config import Config

    try:
        return Config.load().project_remotes(project)
    except KeyError:
        msg = f"проекта {project!r} нет в ~/.cod-doc/config.yaml — remote некуда записать"
        raise RemoteConfigError(msg) from None


def _kind_of(url: str, kind: str) -> str:
    scheme = urlparse(url).scheme
    if scheme not in KINDS:
        msg = f"remote {url!r}: неизвестная схема {scheme or '<нет>'!r}, нужна одна из {KINDS}"
        raise RemoteConfigError(msg)
    if kind not in (AUTO, *KINDS):
        msg = f"--kind {kind!r}: нужен один из {(AUTO, *KINDS)}"
        raise RemoteConfigError(msg)
    if kind not in (AUTO, scheme):
        msg = f"--kind {kind} не совпадает со схемой URL {scheme}://"
        raise RemoteConfigError(msg)
    return scheme


def add(project: str, name: str, url: str, *, kind: str = AUTO) -> AddedRemote:
    """Записать remote проекта (§10.8 ``remote add``); то же имя — замена URL."""
    from cod_doc.config import Config

    try:
        check_name(name, "имя remote")
    except ValueError as exc:
        raise RemoteConfigError(str(exc)) from None
    resolved = _kind_of(url, kind)
    if resolved == "file":
        from cod_doc.infra.remote.file import FileRemote

        FileRemote.from_url(url)  # путь абсолютный и на этой машине — иначе SYNC-110
    elif resolved == "s3":
        from cod_doc.infra.remote.s3 import parse_url

        parse_url(url)  # бакет и префикс допустимы, кредов в URL нет — иначе SYNC-110
    existing = _registered(project)
    Config.load().set_project_remote(project, name, {"url": url, "kind": resolved})
    warnings: list[str] = []
    if resolved == "s3":
        warnings.append(S3_WARNING)
    if resolved in _PENDING:
        warnings.append(_PENDING[resolved])
    return AddedRemote(
        remote=RemoteSpec(name=name, url=url, kind=resolved),
        replaced=name in existing,
        warnings=warnings,
    )


def list_for(project: str) -> list[RemoteSpec]:
    """Remotes проекта по имени."""
    return [
        RemoteSpec(name=name, url=spec.get("url", ""), kind=spec.get("kind", ""))
        for name, spec in sorted(_registered(project).items())
    ]


def get(project: str, name: str) -> RemoteSpec | None:
    """Remote проекта по имени; проект вне реестра или нет записи — ``None``."""
    from cod_doc.config import Config

    entry = Config.load().get_project(project)
    raw = (entry.model_extra or {}).get("remotes") if entry is not None else None
    spec = raw.get(name) if isinstance(raw, dict) else None
    url = spec.get("url") if isinstance(spec, dict) else None
    if not isinstance(url, str) or not url:
        return None
    kind = spec.get("kind") if isinstance(spec, dict) else None
    return RemoteSpec(name=name, url=url, kind=kind if isinstance(kind, str) else "")


def remove(project: str, name: str) -> None:
    """Удалить remote проекта; нет такого — ``SYNC-110``."""
    from cod_doc.config import Config

    _registered(project)
    if not Config.load().remove_project_remote(project, name):
        msg = f"remote {name!r} у проекта {project!r} не настроен"
        raise RemoteConfigError(msg)
