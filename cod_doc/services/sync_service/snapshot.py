"""Снапшот проекта: формат, сборка, загрузка и запись по push (RFC 29 §3.5, §3.6; CLO-029).

Снапшот — уезжающее состояние проекта на changeset ``head``: ровно то, что
покрывает дайджест (§3.7). Clone стартует с него и дотягивает хвост
changeset'ов (``clone``), вместо того чтобы проигрывать всю историю.

**Формат** ``snapshots/<epoch>/<head>.db.zst`` — ``b"CDS1"`` + один zstd-кадр
потока строк; строка — канонический JSON §10.4 и перевод строки:

1. заголовок ``{"v": 1, "kind": "header", "project", "head", "epoch",
   "schema_head", "digest"}``; ``digest`` — дайджест changeset'а ``head``;
2. ``["row", таблица, uid, поля]`` — строка проекта, как её отдала бы
   операция: поля ``SHIPPED_COLUMNS`` без придержанных реестром, FK — uid
   конца, время — §10.4 (``sync_capture.shipped_rows``). Restricted и всё под
   ним в снапшот не попадает — тот же гейт, что у захвата и дайджеста.
   Таблицы — в порядке загрузки (``load_order``: родитель раньше ребёнка),
   строки таблицы — по первичному ключу;
3. ``["alias", {...}]`` — алиасы склейки (``reason = merged``, CLO-023)
   живых строк;
4. ``["end", {"lines", "sha256"}]`` — число и sha256 всех строк до неё:
   обрезанный поток не загрузится.

Слова «SQLite» из §3.5 нет намеренно: файл SQLite с чужого remote —
исполняемая схема (триггеры, представления), а поток строк проходит тот же
разбор, что changeset.

**Что снапшоту не доверяется.** Дайджест головы покрывает только строки,
поэтому всё, что он не покрывает, в снапшоте не едет или проверяется по
адресуемым данным:

- журнал ``sync_op`` (история LWW, штампы создания для склейки ключей,
  цепочки статуса) и индекс ``changeset`` клон собирает сам из changeset'ов
  remote (``load_history``): обход ``parents`` от головы, каждый объект — по
  адресу (``decode`` сверяет sha), штампы — ``apply.check_clock``. Журнал
  клона тот же, что у реплики, проигравшей всю историю, а «применённым»
  помечается только то, что достижимо от проверенной головы;
- алиас принимается, только если выживший uid — загруженная строка той же
  таблицы, проигравший — не живая строка и встречается в проверенном журнале
  этой таблицы.

**Потолки.** Сжатый снапшот — ``MAX_SNAPSHOT_BYTES`` (1 ГиБ, как у
транспорта); сборка останавливается, как только его превысит. Распаковка
потоковая: в памяти одна строка (≤ ``MAX_LINE_BYTES``) и кусок кадра, всего
распаковывается не больше ``MAX_RAW_BYTES``. Обход истории — не больше
``_local.MAX_HISTORY`` changeset'ов.

**Недоверенный ввод.** Строка снапшота проходит тот же путь, что чужая
вставка (``apply.create_row``): только уезжающие колонки, FK — в проекте
клона, гейт реестра; ревизия — ``journal.apply_row``. Итог сверяется
целиком, до коммита: дайджест загруженного состояния обязан совпасть с
дайджестом changeset'а ``head``, прочитанного с remote по адресу. Иначе —
``SYNC-102`` и откат: снапшот не может провести состояние, которого нет в
цепочке changeset'ов.

**Запись** (``write_if_due``) — после успешного push: если на remote нет
снапшота или с последнего набралось ``SNAPSHOT_EVERY`` операций журнала.
Снапшот собирается только с состояния, равного голове (голова реплики =
запушенный changeset, хвоста нет, дайджест совпал); указатель
``meta.json["snapshot"]`` двигается CAS'ом. Сбой записи push не отменяет.
"""

from __future__ import annotations

import functools
import hashlib
import json
import logging
from compression import zstd
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

from sqlalchemy import func, insert, inspect, select, update
from sqlalchemy.exc import IntegrityError

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.db import transactional
from cod_doc.infra.hlc import Hlc, clock_for, self_replica
from cod_doc.infra.models import (
    Base,
    EntityAliasModel,
    ProjectModel,
    SyncOpModel,
)
from cod_doc.infra.remote.base import META_KEY, SnapshotRef, snapshot_key
from cod_doc.infra.remote.file import MAX_SNAPSHOT_BYTES
from cod_doc.services.sync_service import _local, apply, digest, journal
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.changeset import (
    _PARSE_ERRORS,
    MAX_CANONICAL_BYTES,
    Changeset,
    _check_depth,
    canonical_json,
    decode,
    format_time,
)
from cod_doc.services.sync_service.errors import (
    ObjectMissingError,
    SchemaMismatchError,
    SyncError,
)
from cod_doc.services.sync_service.pull import MAX_PARENTS

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping

    from sqlalchemy import Table
    from sqlalchemy.orm import Mapper, Session, sessionmaker

    from cod_doc.infra.remote.base import JsonValue, RemoteStore

__all__ = [
    "FORMAT_MAGIC",
    "SNAPSHOT_EVERY",
    "Header",
    "LoadResult",
    "SnapshotTooLargeError",
    "build",
    "load",
    "load_history",
    "load_order",
    "peek",
    "pointer",
    "write_if_due",
]

logger = logging.getLogger("cod_doc.services.sync_service.snapshot")

FORMAT_VERSION: Final = 1
FORMAT_MAGIC: Final = b"CDS1"
#: Каденция (§3.6): снапшот пишет push, если с прошлого набралось столько
#: операций журнала. Без снапшота clone проекта со 100k операций проигрывал бы
#: их все.
SNAPSHOT_EVERY: Final = 5000
#: Потолок одной строки потока — как у распакованного changeset'а.
MAX_LINE_BYTES = MAX_CANONICAL_BYTES
#: Потолок распакованного потока: защита от zstd-бомбы.
MAX_RAW_BYTES = 16 * 1024 * 1024 * 1024
#: Кусок распаковки за один вызов.
_CHUNK: Final = 1024 * 1024
_BATCH: Final = 500

_HEADER: Final = "header"
_ROW: Final = "row"
_ALIAS: Final = "alias"
_END: Final = "end"
#: Порядок секций потока: строки, алиасы.
_SECTIONS: Final = (_ROW, _ALIAS)
_HEADER_KEYS: Final = frozenset({"v", "kind", "project", "head", "epoch", "schema_head", "digest"})
_ALIAS_KEYS: Final = frozenset({"entity_kind", "alias", "uid", "reason"})
#: Только склейка: ``pinned`` (провизорный ID → постоянный) реплику не покидает.
_MERGED: Final = "merged"
_PROJECT: Final = "project"
_REVISION: Final = "revision"
_ROW_LEN: Final = 4
_PAIR_LEN: Final = 2
#: ``meta.json``: указатель на последний снапшот.
_META_SNAPSHOT: Final = "snapshot"


class SnapshotTooLargeError(RuntimeError):
    """Снапшот не помещается в потолок транспорта — не пишется; push при этом не падает."""


def _bad(why: str) -> ObjectMissingError:
    return ObjectMissingError(f"снапшот: {why}")


# --- порядок загрузки ---------------------------------------------------------------


def _info(table: str) -> cap.TableInfo:
    import cod_doc.infra.models.link_suggestions  # noqa: F401 — ленивая модель

    for mapper in Base.registry.mappers:
        if cast("Table", mapper.local_table).name == table:
            info = cap.table_info(cast("Mapper[object]", mapper))
            if info is not None:
                return info
    msg = f"таблица {table!r} не захватывается"
    raise LookupError(msg)


@functools.cache
def load_order() -> tuple[str, ...]:
    """Таблицы снапшота: ``project`` первой, каждая после концов своих NOT NULL FK.

    Таблицы с полиморфной ссылкой — в конце (конец — любая сущность), журнал
    (``revision``) — последним: ревизия ищет свою сущность. Ничьи — по имени,
    чтобы порядок был один у всех реплик.
    """
    tables = sorted(cap.CAPTURED_TABLES)
    infos = {t: _info(t) for t in tables}
    tail = sorted(t for t in tables if infos[t].polymorphic and t not in reg.JOURNAL_IDS)
    journal_tables = sorted(t for t in tables if t in reg.JOURNAL_IDS)
    body = [t for t in tables if t not in tail and t not in journal_tables]
    needs = {
        t: {
            target
            for column, target in infos[t].fk_targets.items()
            if column not in infos[t].nullable and target != t and target in body
        }
        for t in body
    }
    order: list[str] = []
    while needs:
        ready = sorted(t for t, deps in needs.items() if deps <= set(order))
        if not ready:
            msg = f"цикл NOT NULL FK между таблицами {sorted(needs)}"
            raise AssertionError(msg)
        # project всегда первым: его строку clone создаёт сам.
        first = _PROJECT if _PROJECT in ready else ready[0]
        order.append(first)
        del needs[first]
    return (*order, *tail, *journal_tables)


@functools.cache
def deferred_columns() -> Mapping[str, frozenset[str]]:
    """Nullable FK на свою или более позднюю таблицу: ставятся вторым проходом."""
    order = {t: i for i, t in enumerate(load_order())}
    out: dict[str, frozenset[str]] = {}
    for table, pos in order.items():
        info = _info(table)
        out[table] = frozenset(
            column
            for column, target in info.fk_targets.items()
            if column in info.nullable and order.get(target, len(order)) >= pos
        )
    return out


# --- поток строк --------------------------------------------------------------------


class _Writer:
    """Потоковое сжатие строк с потолком сжатого результата."""

    def __init__(self, limit: int) -> None:
        self._compressor = zstd.ZstdCompressor()
        self._out = bytearray(FORMAT_MAGIC)
        self._hash = hashlib.sha256()
        self._limit = limit
        self.lines = 0

    def line(self, value: object) -> None:
        raw = canonical_json(cast("JsonValue", value)) + b"\n"
        if len(raw) > MAX_LINE_BYTES:
            msg = f"строка снапшота {len(raw)} байт > {MAX_LINE_BYTES}"
            raise SnapshotTooLargeError(msg)
        self._hash.update(raw)
        self._out += self._compressor.compress(raw)
        self.lines += 1
        self._check()

    def _check(self) -> None:
        if len(self._out) > self._limit:
            msg = f"снапшот больше {self._limit} байт сжатым"
            raise SnapshotTooLargeError(msg)

    def finish(self) -> bytes:
        end = canonical_json([_END, {"lines": self.lines, "sha256": self._hash.hexdigest()}])
        self._out += self._compressor.compress(end + b"\n")
        self._out += self._compressor.flush()
        self._check()
        return bytes(self._out)


def _chunks(data: bytes) -> Iterator[bytes]:
    """Распаковка кадра кусками; кадр обязан быть цельным и единственным."""
    if data[: len(FORMAT_MAGIC)] != FORMAT_MAGIC:
        raise _bad(f"неизвестный формат {bytes(data[:4])!r}, ожидается {FORMAT_MAGIC!r}")
    decompressor = zstd.ZstdDecompressor()
    pending: bytes | memoryview = memoryview(data)[len(FORMAT_MAGIC) :]
    total = 0
    while True:
        try:
            chunk = decompressor.decompress(pending, max_length=_CHUNK)
        except zstd.ZstdError as exc:
            raise _bad(f"zstd не распаковывается: {exc}") from exc
        pending = b""
        total += len(chunk)
        if total > MAX_RAW_BYTES:
            raise _bad(f"распакованный поток больше {MAX_RAW_BYTES} байт")
        if chunk:
            yield chunk
        if decompressor.eof:
            break
        if decompressor.needs_input:
            raise _bad("zstd-кадр обрезан")
    if decompressor.unused_data:
        raise _bad("после zstd-кадра лишние байты")


def _parse(raw: bytes) -> object:
    try:
        text = raw.decode("utf-8")
        _check_depth(text)
        return cast("object", json.loads(text))
    except _PARSE_ERRORS as exc:
        raise _bad(f"строка не JSON: {exc}") from exc


def _lines(data: bytes) -> Generator[object]:
    """Разобранные строки потока до ``end``; целостность сверяется в конце.

    Ошибка конца (обрезан, хэш не сошёлся) бросается после выдачи всех строк —
    загрузка идёт одной транзакцией, и исключение откатывает её целиком.
    """
    digest_ = hashlib.sha256()
    buf = bytearray()
    count = 0
    end: object = None
    for chunk in _chunks(data):
        buf += chunk
        while True:
            cut = buf.find(b"\n")
            if cut < 0:
                if len(buf) > MAX_LINE_BYTES:
                    raise _bad(f"строка больше {MAX_LINE_BYTES} байт")
                break
            raw = bytes(buf[:cut])
            del buf[: cut + 1]
            if end is not None:
                raise _bad("строки после end")
            if len(raw) > MAX_LINE_BYTES:
                raise _bad(f"строка больше {MAX_LINE_BYTES} байт")
            value = _parse(raw)
            if isinstance(value, list) and value and value[0] == _END:
                end = value
                continue
            digest_.update(raw + b"\n")
            count += 1
            yield value
    if buf:
        raise _bad("поток оборван посреди строки")
    expected = [_END, {"lines": count, "sha256": digest_.hexdigest()}]
    if end != expected:
        raise _bad("нет строки end или она не сходится с потоком — снапшот обрезан или подменён")


# --- заголовок ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Header:
    project: str
    head: str
    epoch: str
    schema_head: str
    digest: str


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise _bad(f"{what}: нужна непустая строка")
    return value


def _sha(value: object, what: str) -> str:
    text = _text(value, what)
    try:
        from cod_doc.infra.remote.base import check_sha

        return check_sha(text, what)
    except ValueError as exc:
        raise _bad(str(exc)) from None


def _header(value: object) -> Header:
    if not isinstance(value, dict) or set(value) != _HEADER_KEYS:
        raise _bad(f"первая строка — не заголовок с полями {sorted(_HEADER_KEYS)}")
    if value["v"] != FORMAT_VERSION or value["kind"] != _HEADER:
        raise _bad(f"версия {value['v']!r}: поддерживается {FORMAT_VERSION}")
    return Header(
        project=_text(value["project"], "project"),
        head=_sha(value["head"], "head"),
        epoch=_text(value["epoch"], "epoch"),
        schema_head=_text(value["schema_head"], "schema_head"),
        digest=_sha(value["digest"], "digest"),
    )


def peek(data: bytes) -> tuple[Header, dict[str, object]]:
    """Заголовок и поля строки ``project`` — не загружая остальное (clone узнаёт слаг)."""
    lines = _lines(data)
    try:
        header = _header(next(lines, None))
        first = next(lines, None)
    finally:
        lines.close()
    if (
        not isinstance(first, list)
        or len(first) != _ROW_LEN
        or first[:3] != [_ROW, _PROJECT, header.project]
        or not isinstance(first[3], dict)
    ):
        raise _bad("первая строка данных — не строка project этого снапшота")
    return header, cast("dict[str, object]", first[3])


# --- сборка -------------------------------------------------------------------------


def build(session: Session, project: _local.ProjectRef, *, head: str, digest_hex: str) -> bytes:
    """Снапшот состояния проекта в транзакции ``session`` (§3.5).

    Вызывающий гарантирует, что состояние — ровно ``head`` (``write_if_due``
    сверяет дайджест в той же транзакции).
    """
    writer = _Writer(MAX_SNAPSHOT_BYTES)
    writer.line(
        {
            "v": FORMAT_VERSION,
            "kind": _HEADER,
            "project": project.uid,
            "head": head,
            "epoch": _local.EPOCH,
            "schema_head": _local.schema_head(session),
            "digest": digest_hex,
        }
    )
    # Строки снапшота: по ним отбираются алиасы. Память — пара (таблица, uid)
    # на строку проекта, не сами строки.
    shipped: set[tuple[str, str]] = set()
    for table, uid, fields_json in cap.shipped_rows(session, project.row_id, load_order()):
        shipped.add((table, uid))
        writer.line([_ROW, table, uid, to_wire(table, json.loads(fields_json))])
    aliases = session.scalars(
        select(EntityAliasModel)
        .where(EntityAliasModel.project_id == project.row_id)
        .order_by(EntityAliasModel.entity_kind, EntityAliasModel.alias)
    )
    for alias in aliases:
        if (
            alias.reason == _MERGED
            and (alias.entity_kind, alias.uid) in shipped
            and (alias.entity_kind, alias.alias) not in shipped
        ):
            writer.line(
                [
                    _ALIAS,
                    {
                        "entity_kind": alias.entity_kind,
                        "alias": alias.alias,
                        "uid": alias.uid,
                        "reason": alias.reason,
                    },
                ]
            )
    return writer.finish()


# --- загрузка -----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LoadResult:
    head: str
    rows: int


@dataclass
class _Loader:
    session: Session
    project: _local.ProjectRef
    touched: apply.Touched
    section: int = 0
    table_pos: int = 0
    rows: int = 0
    seen_project: bool = False

    def enter(self, kind: str) -> None:
        pos = _SECTIONS.index(kind)
        if pos < self.section:
            raise _bad(f"строка {kind} после секции {_SECTIONS[self.section]}")
        self.section = pos

    def row(self, item: list[object]) -> None:
        if len(item) != _ROW_LEN:
            raise _bad("строка row: нужно [row, таблица, uid, поля]")
        _, table, uid, fields = item
        if not isinstance(table, str) or table not in cap.CAPTURED_TABLES:
            raise _bad(f"таблица {table!r} не синхронизируется")
        if not isinstance(uid, str) or not uid or not isinstance(fields, dict):
            raise _bad(f"строка {table}: uid — строка, поля — объект")
        pos = load_order().index(table)
        if pos < self.table_pos:
            raise _bad(f"строка {table} вне порядка загрузки")
        self.table_pos = pos
        payload = cast("dict[str, object]", fields)
        if table == _PROJECT:
            self._project(uid, payload)
        elif not self.seen_project:
            raise _bad("первая строка данных — не project")
        elif table in reg.JOURNAL_IDS:
            journal.apply_row(self.session, self.project, uid, payload)
        else:
            later = deferred_columns()[table]
            first = {c: v for c, v in payload.items() if c not in later}
            has_uid = _info(table).has_uid
            obj = apply.create_row(
                self.session, self.project, table, uid if has_uid else None, first
            )
            apply.note_written(self.touched, table, obj)
        self.rows += 1

    def _project(self, uid: str, fields: dict[str, object]) -> None:
        if self.seen_project or uid != self.project.uid:
            raise _bad(f"строка project {uid}: не проект клона {self.project.uid} или повтор")
        self.seen_project = True
        info = cap.table_info(cast("Mapper[object]", inspect(ProjectModel)))
        assert info is not None
        obj = self.session.get(ProjectModel, self.project.row_id)
        assert obj is not None
        for column, value in apply.orm_values(
            self.session, self.project, info, fields, obj
        ).items():
            setattr(obj, info.attr(column), value)
        apply.finish_row(self.session, self.project, info, obj, uid, fields)

    def alias(self, item: list[object]) -> None:
        """Алиас склейки — только между загруженной строкой и uid из проверенного журнала."""
        body = item[1] if len(item) == _PAIR_LEN else None
        if not isinstance(body, dict) or set(body) != _ALIAS_KEYS:
            raise _bad(f"строка alias: нужны поля {sorted(_ALIAS_KEYS)}")
        kind, alias, uid = body["entity_kind"], body["alias"], body["uid"]
        if (
            body["reason"] != _MERGED
            or not isinstance(kind, str)
            or kind not in reg.UID_TABLES
            or kind not in cap.CAPTURED_TABLES
        ):
            raise _bad(f"алиас {body!r}: не склейка или таблица без uid")
        alias, uid = _text(alias, "alias.alias"), _text(uid, "alias.uid")
        tbl = Base.metadata.tables[kind]
        live = set(self.session.scalars(select(tbl.c.uid).where(tbl.c.uid.in_([alias, uid]))))
        if uid not in live or alias in live:
            raise _bad(f"алиас {kind} {alias} → {uid}: выживший не загружен или проигравший жив")
        seen = self.session.scalar(
            select(func.count())
            .select_from(SyncOpModel)
            .where(
                SyncOpModel.project_id == self.project.row_id,
                SyncOpModel.entity_kind == kind,
                SyncOpModel.entity_uid == alias,
            )
        )
        if not seen:
            raise _bad(f"алиас {kind} {alias}: такого uid нет в проверенной истории")
        self.session.add(
            EntityAliasModel(
                project_id=self.project.row_id,
                entity_kind=kind,
                alias=alias,
                uid=uid,
                reason=_MERGED,
            )
        )
        # Журнал проигравшего — история выжившего, как у _record_ops и склейки (CLO-023).
        self.session.execute(
            update(SyncOpModel)
            .where(
                SyncOpModel.project_id == self.project.row_id,
                SyncOpModel.entity_kind == kind,
                SyncOpModel.entity_uid == alias,
            )
            .values(entity_uid=uid)
        )


def _dumps(payload: Mapping[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _first_pass(loader: _Loader, data: bytes) -> None:
    lines = _lines(data)
    next(lines)  # заголовок разобран вызывающим
    for item in lines:
        if not isinstance(item, list) or not item or not isinstance(item[0], str):
            raise _bad("строка данных — не [вид, …]")
        kind = item[0]
        if kind not in _SECTIONS:
            raise _bad(f"неизвестный вид строки {kind!r}")
        loader.enter(kind)
        if kind == _ROW:
            loader.row(item)
        elif not loader.seen_project:
            raise _bad("нет строки project")
        else:
            loader.alias(item)
    if not loader.seen_project:
        raise _bad("нет строки project")


def _second_pass(loader: _Loader, data: bytes) -> None:
    """Отложенные nullable FK (на свою или более позднюю таблицу)."""
    later = deferred_columns()
    for item in _lines(data):
        if not isinstance(item, list) or not item or item[0] != _ROW:
            continue
        _, table, uid, fields = item
        table, uid = str(table), str(uid)
        wanted = {
            c: v
            for c, v in cast("dict[str, object]", fields).items()
            if c in later.get(table, ()) and v is not None
        }
        if not wanted:
            continue
        found = apply.entity_by_uid(loader.session, loader.project, table, uid)
        if found is None:
            raise _bad(f"строка {table} {uid} пропала между проходами")
        obj, info = found
        values = apply.orm_values(loader.session, loader.project, info, wanted, obj)
        for column, value in values.items():
            setattr(obj, info.attr(column), value)
        apply.finish_row(loader.session, loader.project, info, obj, uid, wanted)


@dataclass(frozen=True, slots=True)
class History:
    """Журнал клона, собранный из changeset'ов remote по адресам (``load_history``)."""

    head: Changeset
    changesets: int
    ops: int


def _record_history(session: Session, project: _local.ProjectRef, sha: str, cs: Changeset) -> int:
    """Changeset — в индекс (применён), его операции — в журнал историей (``changeset = sha``)."""
    rows = [
        {
            "op_id": op.op_id,
            "project_id": project.row_id,
            "replica_uid": Hlc.parse(op.hlc).replica_uid,
            "hlc": op.hlc,
            "entity_kind": op.entity_kind,
            "entity_uid": op.entity_uid,
            "kind": op.kind,
            "fields": _dumps(op.fields),
            "base": _dumps(op.base),
            "author": op.author,
            "changeset": sha,
        }
        for op in cs.ops
    ]
    if rows:
        session.execute(insert(SyncOpModel), rows)
    _local.record_changeset(session, project, sha, cs)
    return len(rows)


def load_history(
    sf: sessionmaker[Session], store: RemoteStore, project: _local.ProjectRef, head: str
) -> History:
    """Журнал и индекс changeset'ов клона — из объектов remote, от ``head`` по ``parents``.

    Каждый объект читается по адресу (``decode`` сверяет sha содержимого),
    принадлежит проекту, штампы — ``apply.check_clock``; «применённым» в индексе
    становится только достижимое от ``head``. Пишется пачками по ``_BATCH``
    объектов, сеть — между транзакциями; сбой оставляет недособранную БД, которую
    убирает clone. Операции — как у pull (``apply._record_ops``), алиасы склейки
    накладывает потом загрузка снапшота.
    """
    with transactional(sf, commit=False) as session:
        me = self_replica(session, project.row_id)
    head_cs: Changeset | None = None
    seen: set[str] = set()
    queue = [head]
    pending: list[tuple[str, Changeset]] = []
    ops = 0
    top: Hlc | None = None
    while queue:
        sha = queue.pop()
        if sha in seen:
            continue
        if len(seen) >= _local.MAX_HISTORY:
            raise _bad(f"история от головы длиннее {_local.MAX_HISTORY} changeset'ов")
        seen.add(sha)
        cs = decode(store.get_object(project.uid, sha), sha)
        if cs.project != project.uid or len(cs.parents) > MAX_PARENTS:
            raise _bad(f"объект {sha}: чужой проект или родителей больше {MAX_PARENTS}")
        apply.check_clock(cs, self_replica_uid=me)
        if head_cs is None:
            head_cs = cs
        if cs.ops:
            stamp = Hlc.parse(cs.hlc_max)
            top = stamp if top is None or stamp > top else top
        queue.extend(cs.parents)
        pending.append((sha, cs))
        if len(pending) >= _BATCH:
            ops += _flush_history(sf, project, pending, top)
            pending.clear()
    ops += _flush_history(sf, project, pending, top)
    assert head_cs is not None
    return History(head=head_cs, changesets=len(seen), ops=ops)


def _flush_history(
    sf: sessionmaker[Session],
    project: _local.ProjectRef,
    batch: list[tuple[str, Changeset]],
    top: Hlc | None,
) -> int:
    try:
        with transactional(sf) as session:
            written = sum(_record_history(session, project, sha, cs) for sha, cs in batch)
            if top is not None:
                clock_for(session, self_replica(session, project.row_id)).observe(top)
    except IntegrityError as exc:
        raise _bad(f"история: повтор операции в разных changeset'ах: {exc.orig}") from exc
    return written


def load(
    session: Session, project: _local.ProjectRef, data: bytes, *, head: str, head_cs: Changeset
) -> LoadResult:
    """Загрузить снапшот ``head`` в проект клона (транзакция вызывающего).

    Журнал и индекс уже собраны ``load_history`` от той же головы; ``head_cs`` —
    changeset ``head``, прочитанный с remote по адресу: его дайджест — эталон.
    Любое расхождение — ``SYNC-102`` до коммита.
    """
    header = _header(next(_lines(data), None))
    if header.project != project.uid or header.head != head:
        raise _bad(f"заголовок {header.project}/{header.head} ≠ {project.uid}/{head}")
    if header.digest != head_cs.digest:
        raise _bad("дайджест заголовка ≠ дайджесту changeset'а головы")
    if not _local.is_applied(session, head):
        raise _bad(f"голова {head[:12]} не проверена: история не собрана")
    if _local.schema_is_newer(header.schema_head, _local.schema_head(session)):
        msg = f"снапшот на схеме {header.schema_head}, у вас {_local.schema_head(session)} — cod-doc update"
        raise SchemaMismatchError(msg)
    loader = _Loader(session=session, project=project, touched=apply.Touched())
    try:
        with cap.suspended(session):
            _first_pass(loader, data)
            _second_pass(loader, data)
            apply.rebuild_derived(session, project, loader.touched)
    except IntegrityError as exc:
        raise _bad(f"строки нарушают ограничения схемы: {exc.orig}") from exc
    _local.set_local_head(session, project.row_id, head)
    loaded = digest.compute(session, project.row_id)
    if loaded != head_cs.digest:
        raise _bad(f"дайджест загруженного {loaded[:12]} ≠ дайджесту головы {head_cs.digest[:12]}")
    return LoadResult(head=head, rows=loader.rows)


# --- запись по push -----------------------------------------------------------------


def pointer(store: RemoteStore, project_uid: str) -> tuple[SnapshotRef, int] | None:
    """Указатель ``meta.json["snapshot"]``: снапшот и число операций журнала в нём."""
    return _pointer_of(store.get_json(project_uid, META_KEY))


def _pointer_of(
    meta: tuple[dict[str, JsonValue], str] | None,
) -> tuple[SnapshotRef, int] | None:
    raw = meta[0].get(_META_SNAPSHOT) if meta is not None else None
    if not isinstance(raw, dict):
        return None
    epoch, sha, ops = raw.get("epoch"), raw.get("sha"), raw.get("ops")
    if not isinstance(epoch, str) or not isinstance(sha, str) or type(ops) is not int:
        raise _bad(f"meta.json: указатель снапшота {raw!r} не по форме")
    try:
        return SnapshotRef(epoch=epoch, sha=sha, key=snapshot_key(epoch, sha)), ops
    except ValueError as exc:
        raise _bad(f"meta.json: {exc}") from None


def _settled(session: Session, project_id: int) -> int:
    stmt = (
        select(func.count())
        .select_from(SyncOpModel)
        .where(SyncOpModel.project_id == project_id, SyncOpModel.changeset.is_not(None))
    )
    return int(session.scalar(stmt) or 0)


def _build_at_head(
    sf: sessionmaker[Session], project: str, head: str, expected_digest: str, last_ops: int | None
) -> tuple[_local.ProjectRef, bytes, int] | None:
    """Снапшот, если пора и состояние реплики — ровно ``head``; иначе ``None``."""
    with transactional(sf, commit=False) as session:
        ref = _local.project_ref(session, project)
        total = _settled(session, ref.row_id)
        if last_ops is not None and total - last_ops < SNAPSHOT_EVERY:
            return None
        if (
            _local.local_head(session, ref.row_id) != head
            or digest.pending_count(session, ref.row_id)
            or digest.compute(session, ref.row_id) != expected_digest
        ):
            # Между фиксацией push и этой транзакцией реплика записала ещё:
            # состояние уже не голова — снапшот подождёт следующего push.
            return None
        return ref, build(session, ref, head=head, digest_hex=expected_digest), total


def write_if_due(
    sf: sessionmaker[Session], project: str, store: RemoteStore, *, head: str, digest_hex: str
) -> str | None:
    """Записать снапшот ``head``, если пора (§3.6); sha снапшота или ``None``."""
    with transactional(sf, commit=False) as session:
        uid = _local.project_ref(session, project).uid
    meta = store.get_json(uid, META_KEY)
    current = _pointer_of(meta)
    if current is not None and current[0].sha == head:
        return None
    built = _build_at_head(sf, project, head, digest_hex, None if current is None else current[1])
    if built is None:
        return None
    _ref, data, total = built
    snap = SnapshotRef(epoch=_local.EPOCH, sha=head, key=snapshot_key(_local.EPOCH, head))
    store.put_snapshot(uid, snap, data)
    # §10.1: первый писатель заводит meta.json целиком, дальше меняется только указатель.
    body: dict[str, JsonValue] = (
        dict(meta[0])
        if meta is not None
        else {
            "v": 1,
            "project_uid": uid,
            "epoch": _local.EPOCH,
            "schema_min": _local.EPOCH,
            "created": format_time(datetime.now(UTC)),
            "snapshot_every": SNAPSHOT_EVERY,
        }
    )
    body[_META_SNAPSHOT] = {"epoch": snap.epoch, "sha": head, "ops": total}
    if store.cas_json(uid, META_KEY, None if meta is None else meta[1], body) is None:
        # Указатель сдвинул другой push: его снапшот не старше нашего по смыслу,
        # а наш объект остаётся на remote без указателя (clone читает указатель).
        logger.info("снапшот %s записан, но meta.json сдвинул другой push", head[:12])
    return head


def write_quietly(
    sf: sessionmaker[Session], project: str, store: RemoteStore, *, head: str, digest_hex: str
) -> str | None:
    """``write_if_due`` после push: сбой снапшота push не отменяет — он уже на remote."""
    try:
        return write_if_due(sf, project, store, head=head, digest_hex=digest_hex)
    except (SyncError, SnapshotTooLargeError, OSError) as exc:
        logger.warning("снапшот не записан: %s", exc)
        return None
