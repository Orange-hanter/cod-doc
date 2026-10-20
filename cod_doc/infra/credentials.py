"""Файл кредов remote ``$COD_DOC_HOME/credentials`` (RFC 29 §10.12, CLO-051).

Формат — YAML-словарь «URL remote → поля кредов», ровно как в §10.12::

    # ~/.cod-doc/credentials (chmod 600)
    https://sync.example.dev:
      token: cdt_…            # Bearer-токен https-remote (remote login, CLO-038)
    s3://cod-doc-remotes:
      access_key: …           # или стандартная цепочка AWS/MinIO
      secret_key: …

Ключ — URL remote или его префикс по границе сегмента: запись
``s3://cod-doc-remotes`` подходит remote'у ``s3://cod-doc-remotes/team``, но не
``s3://cod-doc-remotes-2``; из подходящих побеждает самая длинная. Хвостовой
``/`` в ключе не значим. Значения полей — строки (числовой ключ доступа
берут в кавычки). Какие поля законны, решает транспорт: S3 — ``access_key``,
``secret_key``, ``session_token``, ``endpoint_url``, ``region``; https —
``token``. JSON — подмножество YAML, так что писатель (``remote login``,
CLO-038) может выводить файл любым дампером, а этот модуль — его единственный
читатель.

**Защита файла.** Открытие с ``O_NOFOLLOW`` (симлинк — отказ), только обычный
файл, владелец — текущий пользователь, права не шире ``0600`` (группа и прочие
— ни одного бита), размер не больше :data:`MAX_CREDENTIALS_BYTES`. Иначе
:class:`CredentialsFileError` (``SYNC-110``) с путём файла и тем, что с ним не
так. Содержимое в ошибки не попадает никогда: ни значения, ни строка, на
которой споткнулся YAML (у ``yaml.YAMLError`` в тексте цитата строки, поэтому
берётся только номер), ни ключ записи, если он не URL remote. Модуль ничего
не логирует.

Файла нет — :func:`credentials_for` отдаёт ``None``, и S3-транспорт идёт по
стандартной цепочке AWS. Секреты в ``config.yaml`` не живут вовсе
(``SECRET_FIELDS`` не расширяется, §10.12).
"""

from __future__ import annotations

import errno
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import yaml

from cod_doc.infra.remote.errors import RemoteConfigError

__all__ = [
    "CREDENTIALS_FILENAME",
    "MAX_CREDENTIALS_BYTES",
    "CredentialsFileError",
    "credentials_file",
    "credentials_for",
    "load_credentials",
    "normalize_key",
    "store_token",
]

CREDENTIALS_FILENAME: Final = "credentials"
#: Потолок файла: десятки remote'ов занимают единицы КБ.
MAX_CREDENTIALS_BYTES: Final = 64 * 1024
#: Биты группы и прочих: хоть один — файл отвергается.
_FOREIGN_BITS: Final = 0o077
#: Схемы, у которых бывают креды; ``file://`` их не читает.
#: ``http`` — только loopback (туннель, тесты): ``HttpRemote`` иной http не откроет.
_SCHEMES: Final = ("s3", "https", "http")
_FIELD_RE: Final = re.compile(r"[a-z][a-z0-9_]{0,63}")

type Entries = dict[str, dict[str, str]]


class CredentialsFileError(RemoteConfigError):
    """SYNC-110: файл кредов небезопасен или не разбирается; ``path`` — его путь."""

    def __init__(self, path: Path, problem: str) -> None:
        self.path = path
        super().__init__(f"{path}: {problem}")


def credentials_file() -> Path:
    """Путь к файлу кредов в актуальном ``COD_DOC_HOME`` (рядом с ``config.yaml``)."""
    from cod_doc.config import config_dir

    return config_dir() / CREDENTIALS_FILENAME


def normalize_key(url: str) -> str:
    """Ключ записи для URL remote: без хвостового ``/``."""
    return url.rstrip("/")


def _read_bounded(path: Path) -> bytes | None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise CredentialsFileError(path, "символическая ссылка — нужен обычный файл") from None
        raise CredentialsFileError(path, f"не открывается ({exc.strerror})") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise CredentialsFileError(path, "не обычный файл")
        if st.st_uid != os.getuid():
            msg = f"владелец uid {st.st_uid}, а не текущий пользователь (uid {os.getuid()})"
            raise CredentialsFileError(path, msg)
        mode = stat.S_IMODE(st.st_mode)
        if mode & _FOREIGN_BITS:
            msg = f"права {mode:04o} шире 0600 — файл с ключами отвергнут; chmod 600 {path}"
            raise CredentialsFileError(path, msg)
        if st.st_size > MAX_CREDENTIALS_BYTES:
            raise CredentialsFileError(path, f"больше {MAX_CREDENTIALS_BYTES} байт")
        chunks: list[bytes] = []
        remaining = MAX_CREDENTIALS_BYTES + 1
        while remaining > 0 and (chunk := os.read(fd, remaining)):
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_CREDENTIALS_BYTES:
        raise CredentialsFileError(path, f"больше {MAX_CREDENTIALS_BYTES} байт")
    return data


def _parse_yaml(path: Path, data: bytes) -> object:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise CredentialsFileError(path, "не UTF-8") from None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # str(exc) цитирует строку файла — вместе с секретом; только номер строки.
        mark = getattr(exc, "problem_mark", None)
        where = f" (строка {mark.line + 1})" if mark is not None else ""
        raise CredentialsFileError(path, f"не разбирается как YAML{where}") from None


def _valid_key(key: object) -> str | None:
    if not isinstance(key, str):
        return None
    parsed = urlparse(key)
    if parsed.scheme not in _SCHEMES or not parsed.netloc or "@" in parsed.netloc:
        return None
    return normalize_key(key)


def _entries(path: Path, raw: object) -> Entries:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise CredentialsFileError(path, "нужен словарь «URL remote → поля кредов»")
    entries: Entries = {}
    for index, (key, fields) in enumerate(raw.items(), start=1):
        url = _valid_key(key)
        if url is None:
            # Ключ не URL remote — возможно, вставленный по ошибке секрет: не цитируем.
            msg = f"запись №{index}: ключ не URL remote вида s3://… или https://… без кредов"
            raise CredentialsFileError(path, msg)
        if not isinstance(fields, dict):
            raise CredentialsFileError(path, f"{url}: нужен словарь полей")
        clean: dict[str, str] = {}
        for name, value in fields.items():
            if not isinstance(name, str) or _FIELD_RE.fullmatch(name) is None:
                raise CredentialsFileError(path, f"{url}: недопустимое имя поля")
            if not isinstance(value, str):
                msg = f"{url}: поле {name} — не строка (возьмите значение в кавычки)"
                raise CredentialsFileError(path, msg)
            clean[name] = value
        if url in entries:
            raise CredentialsFileError(path, f"{url}: запись повторяется")
        entries[url] = clean
    return entries


def load_credentials(path: Path | None = None) -> Entries:
    """Все записи файла кредов по нормализованному URL; файла нет — ``{}``."""
    path = path if path is not None else credentials_file()
    data = _read_bounded(path)
    if data is None:
        return {}
    return _entries(path, _parse_yaml(path, data))


def store_token(url: str, token: str, *, path: Path | None = None) -> Path:
    """Записать токен https-remote в файл кредов (CLO-038, ``remote login``); вернуть путь.

    Остальные записи сохраняются; существующий файл проходит те же проверки,
    что при чтении (битый или чужой — ``CredentialsFileError``, а не тихая
    перезапись). Запись атомарна: временный файл ``0600`` и ``os.replace``.
    """
    path = path if path is not None else credentials_file()
    key = _valid_key(url)
    if key is None or not key.startswith(("https://", "http://")):
        msg = f"{url!r}: токен пишется только для https-remote"
        raise RemoteConfigError(msg)
    entries = load_credentials(path)
    entries.setdefault(key, {})["token"] = token
    path.parent.mkdir(parents=True, exist_ok=True)
    # mkstemp: уникальное имя и 0600 сразу; хвост прерванной записи не мешает следующей.
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            yaml.safe_dump(entries, fh, allow_unicode=True, sort_keys=True)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return path


def credentials_for(url: str, *, path: Path | None = None) -> dict[str, str] | None:
    """Креды remote ``url``: самый длинный подходящий ключ; нет такого — ``None``.

    ``None`` значит «файл молчит» — транспорт берёт креды сам (S3 — цепочка
    AWS). Небезопасный или битый файл — :class:`CredentialsFileError`, даже
    если записи для ``url`` в нём нет: молча обойти отвергнутый файл значило бы
    тихо сменить, чьими ключами идёт запрос.
    """
    target = normalize_key(url)
    best: str | None = None
    entries = load_credentials(path)
    for key in entries:
        if (target == key or target.startswith(key + "/")) and (
            best is None or len(key) > len(best)
        ):
            best = key
    return dict(entries[best]) if best is not None else None
