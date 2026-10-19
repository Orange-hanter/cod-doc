"""``FileRemote`` — remote в каталоге ФС, ``file:///path`` (RFC 29 §3.5, CLO-011).

Для тестов, NAS и как эталон контракта ``RemoteStore``.

**CAS между процессами.** Сравнение и запись идут под ``fcntl.flock`` на
файле-замке рядом с ключом (``.<имя>.lock``); новое значение пишется во
временный файл того же каталога, ``fsync`` и ``rename`` — читатель без
замка видит либо старое значение, либо новое, но не половину. flock
снимается ядром при смерти процесса, поэтому упавший писатель не оставляет
вечный замок (у замка через ``O_EXCL`` на это нужна уборка по таймауту).
Ограничения: только POSIX (``fcntl``, ``dir_fd``); на сетевой ФС flock надёжен
там, где его честно реализует сервер (NFSv4, SMB с oplock'ами) — для общего
доступа нескольких машин remote — S3 или умный сервер.

RFC (§3.5, таблица транспортов) называл ``os.link`` + rename; flock выбран
потому, что ``link`` создаёт ключ, но не умеет атомарно сравнить и заменить
существующий — для ``cas_ref`` с ``expected`` всё равно нужен замок.

**Каталог remote — недоверенный.** Его содержимое пишут другие машины, поэтому
путь не собирается строкой: от корня спускаемся по одному сегменту
``os.open(сегмент, O_DIRECTORY | O_NOFOLLOW, dir_fd=родитель)``, а файлы
открываются, создаются, переименовываются и удаляются относительно
дескриптора своего каталога (openat-стиль). Символическая ссылка на любом
уровне ниже корня — отказ (``ELOOP``), и это не TOCTOU: проверка и действие —
один системный вызов. Сам корень может быть ссылкой (его задал пользователь),
он разрешается один раз при создании. Файл открывается с ``O_NOFOLLOW |
O_NONBLOCK`` (FIFO не подвешивает чтение), ``fstat`` до чтения отвергает
не-обычный файл и файл больше потолка, а читается не больше ``потолок + 1``
байт. Нарушение раскладки (ссылка, каталог или FIFO на месте файла) —
``RemoteUnavailableError`` (SYNC-101); файл больше потолка —
``ObjectMissingError`` (SYNC-102), как и прочий непригодный объект.

**etag** — ``sha256`` байтов файла. Совпадение содержимого = совпадение
версии; ABA (X → Y → X) CAS пропускает, и это безопасно: ref указывает на
неизменяемый объект по содержимому, а JSON аренды с тем же телом — то же
состояние. Так же устроен ETag в S3 (MD5 тела без multipart).

**Объекты** неизменяемы: существующий адрес не перезаписывается, гонка двух
писателей одного объекта безвредна — пишут одни и те же байты, и каждый
делает ``rename`` целым файлом. Содержимое против адреса здесь не
сверяется — это делает ``changeset.decode`` при чтении.

Имена служебных файлов (``.…lock``, ``.…tmp``) начинаются с точки, а имена
ключей — нет (``check_name``), поэтому они не пересекаются.
"""

from __future__ import annotations

import contextlib
import errno
import fcntl
import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import TYPE_CHECKING, Self
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from cod_doc.infra.remote.base import (
    MAX_LIST_KEYS,
    JsonValue,
    RefValue,
    SnapshotRef,
    check_json_key,
    check_json_prefix,
    check_name,
    check_sha,
    check_snapshot,
    snapshot_key,
)
from cod_doc.infra.remote.errors import (
    ObjectMissingError,
    RemoteConfigError,
    RemoteUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

__all__ = ["FileRemote"]

_LOCAL_HOSTS = frozenset({"", "localhost"})
_SNAPSHOT_SUFFIX = ".db.zst"
#: Ref — одна строка sha.
MAX_REF_BYTES = 128
#: JSON аренд и meta — крошечные; потолок отсекает подложенный мусор.
MAX_JSON_BYTES = 1024 * 1024
#: Объект — сжатый changeset; распакованный ограничен 64 МиБ в decode, сжатый
#: несжимаемый вход крупнее на доли процента — двукратный запас.
MAX_OBJECT_BYTES = 128 * 1024 * 1024
#: Снапшот — сжатая SQLite authored-данных проекта.
MAX_SNAPSHOT_BYTES = 1024 * 1024 * 1024
_READ_CHUNK = 1024 * 1024
_LOCK_OPEN_ATTEMPTS = 50
_FILE_MODE = 0o644
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
_SYMLINK_ERRNOS = frozenset({errno.ELOOP, errno.ENOTDIR, errno.EMLINK})


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _unsafe(where: str, exc: OSError | None = None) -> RemoteUnavailableError:
    detail = f": {exc}" if exc is not None else ""
    msg = f"remote непригоден: {where} — ссылка или не тот тип файла{detail}"
    return RemoteUnavailableError(msg)


def _unavailable(where: str, exc: OSError) -> RemoteUnavailableError:
    return RemoteUnavailableError(f"{where}: {exc}")


def _dumps(body: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _read_capped(fd: int, cap: int, where: str) -> bytes:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise _unsafe(where)
    if info.st_size > cap:
        msg = f"{where}: {info.st_size} байт больше потолка {cap}"
        raise ObjectMissingError(msg)
    chunks: list[bytes] = []
    total = 0
    while total <= cap:
        chunk = os.read(fd, min(_READ_CHUNK, cap + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    if total > cap:  # файл вырос после fstat
        msg = f"{where}: больше потолка {cap} байт"
        raise ObjectMissingError(msg)
    return b"".join(chunks)


def _open_lock(dfd: int, name: str) -> int:
    """Открыть (создав) файл-замок в каталоге ``dfd``.

    На macOS (APFS) одновременный ``openat(O_CREAT)`` одного ещё не
    существующего имени из нескольких процессов изредка отдаёт ``ENOENT``,
    хотя каталог открыт и на месте: гонку ловил тест с четырьмя процессами.
    Повтор безопасен — каталог держится дескриптором, а файл-замок никто не
    удаляет; потолок попыток отделяет эту гонку от настоящей ошибки.
    """
    for _ in range(_LOCK_OPEN_ATTEMPTS - 1):
        try:
            return os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, _FILE_MODE, dir_fd=dfd)
        except FileNotFoundError:
            continue
    return os.open(name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, _FILE_MODE, dir_fd=dfd)


def _open_dir_at(fd: int, part: str, *, create: bool) -> int:
    """Подкаталог ``part`` каталога ``fd`` без следования ссылке; ``create`` — создать.

    ``FileNotFoundError`` наружу — только без ``create``; при ``create`` та же
    гонка APFS, что и в ``_open_lock``, лечится повтором mkdir + open.
    """
    attempts = _LOCK_OPEN_ATTEMPTS if create else 1
    for attempt in range(attempts):
        if create:
            with contextlib.suppress(FileExistsError):
                os.mkdir(part, 0o755, dir_fd=fd)
        try:
            return os.open(part, _DIR_FLAGS, dir_fd=fd)
        except FileNotFoundError:
            if attempt == attempts - 1:
                raise
    raise AssertionError  # pragma: no cover — цикл выше всегда возвращает или бросает


class FileRemote:
    """``RemoteStore`` поверх каталога; потокобезопасен и межпроцессно корректен."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @classmethod
    def from_url(cls, url: str) -> Self:
        parsed = urlparse(url)
        if parsed.scheme != "file" or parsed.netloc not in _LOCAL_HOSTS:
            msg = f"remote {url!r}: нужен file:///абсолютный/путь на этой машине"
            raise RemoteConfigError(msg)
        path = Path(url2pathname(unquote(parsed.path)))
        if not path.is_absolute():
            msg = f"remote {url!r}: путь не абсолютный"
            raise RemoteConfigError(msg)
        return cls(path)

    # --- адреса: (сегменты каталога, имя файла) -------------------------------

    def _ref_addr(self, project_uid: str, name: str) -> tuple[tuple[str, ...], str]:
        return (check_name(project_uid, "project_uid"), "refs"), check_name(name, "ref")

    def _object_addr(self, project_uid: str, sha: str) -> tuple[tuple[str, ...], str]:
        check_sha(sha)
        return (check_name(project_uid, "project_uid"), "objects", sha[:2]), f"{sha[2:]}.zst"

    def _json_addr(self, project_uid: str, key: str) -> tuple[tuple[str, ...], str]:
        *dirs, name = check_json_key(key)
        return (check_name(project_uid, "project_uid"), *dirs), name

    def _snapshot_addr(self, project_uid: str, ref: SnapshotRef) -> tuple[tuple[str, ...], str]:
        _, epoch, name = check_snapshot(ref).key.split("/")
        return (check_name(project_uid, "project_uid"), "snapshots", epoch), name

    # --- спуск по каталогам без ссылок ----------------------------------------

    @contextlib.contextmanager
    def _dir(self, parts: tuple[str, ...], *, create: bool) -> Iterator[int | None]:
        """Дескриптор каталога ``root/parts``; ``None``, если его нет и ``create=False``."""
        where = str(self.root)
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
        except FileNotFoundError as exc:
            if create:
                raise _unavailable(where, exc) from exc
            yield None
            return
        except OSError as exc:
            raise _unavailable(where, exc) from exc
        try:
            for part in parts:
                where = f"{where}/{part}"
                try:
                    child = _open_dir_at(fd, part, create=create)
                except FileNotFoundError:
                    yield None
                    return
                except OSError as exc:
                    if exc.errno in _SYMLINK_ERRNOS:
                        raise _unsafe(where, exc) from exc
                    raise _unavailable(where, exc) from exc
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    def _read(self, addr: tuple[tuple[str, ...], str], cap: int) -> bytes | None:
        parts, name = addr
        where = "/".join((*parts, name))
        with self._dir(parts, create=False) as dfd:
            if dfd is None:
                return None
            try:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dfd)
            except FileNotFoundError:
                return None
            except OSError as exc:
                if exc.errno in _SYMLINK_ERRNOS:
                    raise _unsafe(where, exc) from exc
                raise _unavailable(where, exc) from exc
            try:
                return _read_capped(fd, cap, where)
            except OSError as exc:
                raise _unavailable(where, exc) from exc
            finally:
                os.close(fd)

    def _is_file(self, addr: tuple[tuple[str, ...], str]) -> bool:
        parts, name = addr
        with self._dir(parts, create=False) as dfd:
            if dfd is None:
                return False
            try:
                info = os.stat(name, dir_fd=dfd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            return stat.S_ISREG(info.st_mode)

    @staticmethod
    def _write_in(dfd: int, name: str, data: bytes) -> None:
        """Временный файл в каталоге → fsync → rename поверх ``name`` → fsync каталога.

        ``rename`` заменяет запись каталога, не следуя ссылке на месте ``name``.
        """
        tmp = f".{name}.{secrets.token_hex(8)}.tmp"
        fd = os.open(
            tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, _FILE_MODE, dir_fd=dfd
        )
        try:
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view) :]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise
        os.fsync(dfd)

    def _write(self, addr: tuple[tuple[str, ...], str], data: bytes) -> None:
        parts, name = addr
        with self._dir(parts, create=True) as dfd:
            assert dfd is not None
            try:
                self._write_in(dfd, name, data)
            except OSError as exc:
                raise _unavailable("/".join((*parts, name)), exc) from exc

    # --- CAS ------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, addr: tuple[tuple[str, ...], str]) -> Iterator[int]:
        """flock на ``.<имя>.lock``; отдаёт дескриптор каталога ключа."""
        parts, name = addr
        with self._dir(parts, create=True) as dfd:
            assert dfd is not None
            try:
                lock = _open_lock(dfd, f".{name}.lock")
            except OSError as exc:
                raise _unavailable(f"замок {name}", exc) from exc
            try:
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield dfd
            finally:
                os.close(lock)  # закрытие дескриптора снимает flock

    def _cas_write(
        self, addr: tuple[tuple[str, ...], str], cap: int, expected_etag: str | None, data: bytes
    ) -> str | None:
        with self._locked(addr) as dfd:
            current = self._read(addr, cap)
            if (None if current is None else _etag(current)) != expected_etag:
                return None
            try:
                self._write_in(dfd, addr[1], data)
            except OSError as exc:
                raise _unavailable("/".join((*addr[0], addr[1])), exc) from exc
            return _etag(data)

    # --- refs -----------------------------------------------------------------

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        data = self._read(self._ref_addr(project_uid, name), MAX_REF_BYTES)
        if data is None:
            return None
        sha = data.decode("ascii", errors="replace").strip()
        try:
            check_sha(sha)
        except ValueError as exc:
            msg = f"ref {name} проекта {project_uid} повреждён: {exc}"
            raise ObjectMissingError(msg) from exc
        return RefValue(sha=sha, etag=_etag(data))

    def cas_ref(
        self, project_uid: str, name: str, expected: RefValue | None, new_sha: str
    ) -> RefValue | None:
        addr = self._ref_addr(project_uid, name)
        data = f"{check_sha(new_sha, 'new_sha')}\n".encode("ascii")
        etag = self._cas_write(
            addr, MAX_REF_BYTES, None if expected is None else expected.etag, data
        )
        return None if etag is None else RefValue(sha=new_sha, etag=etag)

    # --- objects --------------------------------------------------------------

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]:
        addrs = {sha: self._object_addr(project_uid, sha) for sha in shas}
        return {sha for sha, addr in addrs.items() if self._is_file(addr)}

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        addr = self._object_addr(project_uid, sha)
        if not self._is_file(addr):
            self._write(addr, data)

    def get_object(self, project_uid: str, sha: str) -> bytes:
        data = self._read(self._object_addr(project_uid, sha), MAX_OBJECT_BYTES)
        if data is None:
            msg = f"объекта {sha} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return data

    # --- json -----------------------------------------------------------------

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        data = self._read(self._json_addr(project_uid, key), MAX_JSON_BYTES)
        if data is None:
            return None
        try:
            body: object = json.loads(data.decode("utf-8"))
        except (ValueError, RecursionError) as exc:
            msg = f"{key} проекта {project_uid}: не JSON: {exc}"
            raise ObjectMissingError(msg) from exc
        if not isinstance(body, dict):
            msg = f"{key} проекта {project_uid}: нужен JSON-объект"
            raise ObjectMissingError(msg)
        return body, _etag(data)

    def cas_json(
        self,
        project_uid: str,
        key: str,
        expected_etag: str | None,
        body: Mapping[str, JsonValue],
    ) -> str | None:
        data = _dumps(body)
        if len(data) > MAX_JSON_BYTES:
            msg = f"{key}: тело {len(data)} байт больше потолка {MAX_JSON_BYTES}"
            raise ValueError(msg)
        return self._cas_write(
            self._json_addr(project_uid, key), MAX_JSON_BYTES, expected_etag, data
        )

    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool:
        addr = self._json_addr(project_uid, key)
        with self._locked(addr) as dfd:
            current = self._read(addr, MAX_JSON_BYTES)
            if current is None or _etag(current) != expected_etag:
                return False
            try:
                os.unlink(addr[1], dir_fd=dfd)
                os.fsync(dfd)
            except OSError as exc:
                raise _unavailable(key, exc) from exc
            return True

    def list_prefix(self, project_uid: str, prefix: str) -> list[str]:
        """Обычные файлы каталога ``prefix`` с именем ключа (``check_name``).

        Спуск — тем же ``_dir`` (``O_NOFOLLOW`` на каждом сегменте); ссылки,
        подкаталоги и служебные ``.…lock``/``.…tmp`` пропускаются.
        """
        parts = check_json_prefix(prefix)
        keys: list[str] = []
        with self._dir((check_name(project_uid, "project_uid"), *parts), create=False) as dfd:
            if dfd is None:
                return []
            with os.scandir(dfd) as entries:
                for entry in entries:
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        check_name(entry.name, "ключ")
                    except ValueError:
                        continue
                    keys.append(f"{prefix}{entry.name}")
                    if len(keys) > MAX_LIST_KEYS:
                        msg = f"{prefix} проекта {project_uid}: больше {MAX_LIST_KEYS} ключей"
                        raise RemoteUnavailableError(msg)
        return sorted(keys)

    # --- snapshots ------------------------------------------------------------

    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None:
        """Самая поздняя эпоха по имени (головы миграций сортируются по номеру),
        внутри эпохи — по времени записи, ничья — по sha. Ссылки пропускаются."""
        best: tuple[str, int, str] | None = None
        project = check_name(project_uid, "project_uid")
        with self._dir((project, "snapshots"), create=False) as dfd:
            if dfd is None:
                return None
            with os.scandir(dfd) as entries:
                epochs = [
                    e.name
                    for e in entries
                    if e.is_dir(follow_symlinks=False) and not e.name.startswith(".")
                ]
        for epoch in epochs:
            try:
                check_name(epoch, "epoch")
            except ValueError:
                continue
            with self._dir((project, "snapshots", epoch), create=False) as efd:
                if efd is None:
                    continue
                with os.scandir(efd) as entries:
                    for entry in entries:
                        sha = entry.name.removesuffix(_SNAPSHOT_SUFFIX)
                        if sha == entry.name or not entry.is_file(follow_symlinks=False):
                            continue
                        try:
                            check_sha(sha)
                            mtime = entry.stat(follow_symlinks=False).st_mtime_ns
                        except ValueError, FileNotFoundError:
                            continue
                        candidate = (epoch, mtime, sha)
                        if best is None or candidate > best:
                            best = candidate
        if best is None:
            return None
        epoch, _, sha = best
        return SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))

    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None:
        addr = self._snapshot_addr(project_uid, ref)
        if not self._is_file(addr):
            self._write(addr, data)

    def get_snapshot(self, project_uid: str, ref: SnapshotRef) -> bytes:
        data = self._read(self._snapshot_addr(project_uid, ref), MAX_SNAPSHOT_BYTES)
        if data is None:
            msg = f"снапшота {ref.key} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return data
