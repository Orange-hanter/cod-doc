"""``FileRemote`` — remote в каталоге ФС, ``file:///path`` (RFC 29 §3.5, CLO-011).

Для тестов, NAS и как эталон контракта ``RemoteStore``.

**CAS между процессами.** Сравнение и запись идут под ``fcntl.flock`` на
файле-замке рядом с ключом (``.<имя>.lock``); новое значение пишется во
временный файл того же каталога, ``fsync`` и ``os.replace`` — читатель без
замка видит либо старое значение, либо новое, но не половину. flock
снимается ядром при смерти процесса, поэтому упавший писатель не оставляет
вечный замок (у замка через ``O_EXCL`` на это нужна уборка по таймауту).
Ограничения: только POSIX (``fcntl``); на сетевой ФС flock надёжен там, где
его честно реализует сервер (NFSv4, SMB с oplock'ами) — для общего доступа
нескольких машин remote — S3 или умный сервер.

RFC (§3.5, таблица транспортов) называл ``os.link`` + rename; flock выбран
потому, что ``link`` создаёт ключ, но не умеет атомарно сравнить и заменить
существующий — для ``cas_ref`` с ``expected`` всё равно нужен замок.

**etag** — ``sha256`` байтов файла. Совпадение содержимого = совпадение
версии; ABA (X → Y → X) CAS пропускает, и это безопасно: ref указывает на
неизменяемый объект по содержимому, а JSON аренды с тем же телом — то же
состояние. Так же устроен ETag в S3 (MD5 тела без multipart).

**Объекты** неизменяемы: существующий адрес не перезаписывается, гонка двух
писателей одного объекта безвредна — пишут одни и те же байты, и каждый
делает ``os.replace`` целым файлом. Содержимое против адреса здесь не
сверяется — это делает ``changeset.decode`` при чтении.

Имена служебных файлов (``.…lock``, ``.…tmp``) начинаются с точки, а имена
ключей — нет (``check_name``), поэтому они не пересекаются.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Self
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from cod_doc.infra.remote.base import (
    JsonValue,
    RefValue,
    SnapshotRef,
    check_json_key,
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
#: JSON аренд и meta — крошечные; потолок отсекает подложенный мусор.
MAX_JSON_BYTES = 1024 * 1024


def _etag(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        msg = f"не читается {path}: {exc}"
        raise RemoteUnavailableError(msg) from exc


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_atomic(path: Path, data: bytes) -> None:
    """Временный файл в том же каталоге → fsync → ``os.replace`` → fsync каталога."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    _fsync_dir(path.parent)


def _dumps(body: Mapping[str, JsonValue]) -> bytes:
    return json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


class FileRemote:
    """``RemoteStore`` поверх каталога; потокобезопасен и межпроцессно корректен."""

    def __init__(self, root: Path) -> None:
        self.root = root

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

    # --- пути -----------------------------------------------------------------

    def _project(self, project_uid: str) -> Path:
        return self.root / check_name(project_uid, "project_uid")

    def _ref_path(self, project_uid: str, name: str) -> Path:
        return self._project(project_uid) / "refs" / check_name(name, "ref")

    def _object_path(self, project_uid: str, sha: str) -> Path:
        check_sha(sha)
        return self._project(project_uid) / "objects" / sha[:2] / f"{sha[2:]}.zst"

    def _json_path(self, project_uid: str, key: str) -> Path:
        return self._project(project_uid).joinpath(*check_json_key(key))

    def _snapshot_path(self, project_uid: str, ref: SnapshotRef) -> Path:
        return self._project(project_uid).joinpath(*check_snapshot(ref).key.split("/"))

    # --- CAS ------------------------------------------------------------------

    @contextlib.contextmanager
    def _locked(self, path: Path) -> Iterator[None]:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path.parent / f".{path.name}.lock", os.O_RDWR | os.O_CREAT, 0o644)
        except OSError as exc:
            msg = f"не открыть замок для {path}: {exc}"
            raise RemoteUnavailableError(msg) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            os.close(fd)  # закрытие дескриптора снимает flock

    def _cas_write(self, path: Path, expected_etag: str | None, data: bytes) -> str | None:
        with self._locked(path):
            current = _read(path)
            if (None if current is None else _etag(current)) != expected_etag:
                return None
            try:
                _write_atomic(path, data)
            except OSError as exc:
                msg = f"не записать {path}: {exc}"
                raise RemoteUnavailableError(msg) from exc
            return _etag(data)

    # --- refs -----------------------------------------------------------------

    def get_ref(self, project_uid: str, name: str) -> RefValue | None:
        path = self._ref_path(project_uid, name)
        data = _read(path)
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
        path = self._ref_path(project_uid, name)
        data = f"{check_sha(new_sha, 'new_sha')}\n".encode("ascii")
        etag = self._cas_write(path, None if expected is None else expected.etag, data)
        return None if etag is None else RefValue(sha=new_sha, etag=etag)

    # --- objects --------------------------------------------------------------

    def has_objects(self, project_uid: str, shas: Sequence[str]) -> set[str]:
        paths = {sha: self._object_path(project_uid, sha) for sha in shas}
        return {sha for sha, path in paths.items() if path.is_file()}

    def put_object(self, project_uid: str, sha: str, data: bytes) -> None:
        path = self._object_path(project_uid, sha)
        if path.is_file():
            return
        try:
            _write_atomic(path, data)
        except OSError as exc:
            msg = f"не записать объект {sha}: {exc}"
            raise RemoteUnavailableError(msg) from exc

    def get_object(self, project_uid: str, sha: str) -> bytes:
        data = _read(self._object_path(project_uid, sha))
        if data is None:
            msg = f"объекта {sha} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return data

    # --- json -----------------------------------------------------------------

    def get_json(self, project_uid: str, key: str) -> tuple[dict[str, JsonValue], str] | None:
        data = _read(self._json_path(project_uid, key))
        if data is None:
            return None
        if len(data) > MAX_JSON_BYTES:
            msg = f"{key} проекта {project_uid}: больше {MAX_JSON_BYTES} байт"
            raise ObjectMissingError(msg)
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
        path = self._json_path(project_uid, key)
        return self._cas_write(path, expected_etag, _dumps(body))

    def delete_json(self, project_uid: str, key: str, expected_etag: str) -> bool:
        path = self._json_path(project_uid, key)
        with self._locked(path):
            current = _read(path)
            if current is None or _etag(current) != expected_etag:
                return False
            try:
                path.unlink()
                _fsync_dir(path.parent)
            except OSError as exc:
                msg = f"не удалить {path}: {exc}"
                raise RemoteUnavailableError(msg) from exc
            return True

    # --- snapshots ------------------------------------------------------------

    def latest_snapshot(self, project_uid: str) -> SnapshotRef | None:
        """Самая поздняя эпоха по имени (головы миграций сортируются по номеру),
        внутри эпохи — по времени записи, ничья — по sha."""
        root = self._project(project_uid) / "snapshots"
        best: tuple[str, int, str] | None = None
        try:
            epochs = [p for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
        except FileNotFoundError:
            return None
        except OSError as exc:
            msg = f"не читается {root}: {exc}"
            raise RemoteUnavailableError(msg) from exc
        for epoch_dir in epochs:
            for path in epoch_dir.glob(f"*{_SNAPSHOT_SUFFIX}"):
                sha = path.name.removesuffix(_SNAPSHOT_SUFFIX)
                try:
                    check_sha(sha)
                    check_name(epoch_dir.name, "epoch")
                    mtime = path.stat().st_mtime_ns
                except ValueError, FileNotFoundError:
                    continue
                candidate = (epoch_dir.name, mtime, sha)
                if best is None or candidate > best:
                    best = candidate
        if best is None:
            return None
        epoch, _, sha = best
        return SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha))

    def put_snapshot(self, project_uid: str, ref: SnapshotRef, data: bytes) -> None:
        path = self._snapshot_path(project_uid, ref)
        if path.is_file():
            return
        try:
            _write_atomic(path, data)
        except OSError as exc:
            msg = f"не записать снапшот {ref.key}: {exc}"
            raise RemoteUnavailableError(msg) from exc

    def get_snapshot(self, project_uid: str, ref: SnapshotRef) -> bytes:
        data = _read(self._snapshot_path(project_uid, ref))
        if data is None:
            msg = f"снапшота {ref.key} нет в проекте {project_uid}"
            raise ObjectMissingError(msg)
        return data
