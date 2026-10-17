"""Формат changeset v1: каноническая форма, адрес, хранение (RFC 29 §10.4, CLO-010).

Changeset — неизменяемый объект; его адрес — ``sha256`` канонического JSON, а на
remote он лежит как ``b"CDC1" + zstd(canonical_json)``. Четыре байта — версия
формата, место под шифрование (§5).

Каноническая форма (§10.4): UTF-8 без экранирования (``ensure_ascii=False``),
ключи по алфавиту на всех уровнях, ``separators=(",", ":")``, время —
``YYYY-MM-DDTHH:MM:SS.ffffffZ`` в UTC, ``ops`` по ``(hlc, op_id)``. Порядок
``parents`` значим (первый родитель merge-changeset'а — локальная голова, §3.6)
и не сортируется.

Байты канонической формы — контракт между репликами: правка, меняющая их для
того же содержимого, меняет адреса всех уже записанных объектов. Её стережёт
golden-тест ``tests/services/sync/test_changeset.py``; такая правка — новая
версия формата (``CDC2``), а не рефакторинг.

Чтение (``decode``) проверяет всё, что можно проверить без БД: версию формата,
целостность zstd-кадра, совпадение sha с адресом, структуру §10.4 и то, что
байты действительно канонические. Любое расхождение — ``ObjectMissingError``
(``SYNC-102``): объект по этому адресу непригоден, как если бы его не было.
Построение changeset'а из невалидных данных — ``ValueError``: это ошибка
упаковщика, а не remote.

Упаковка журнала ``sync_op`` в changeset — ``push`` (CLO-012); здесь только
формат.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from compression import zstd
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Final

from cod_doc.infra.hlc import Hlc
from cod_doc.services.sync_service.errors import ObjectMissingError

__all__ = [
    "FORMAT_MAGIC",
    "FORMAT_VERSION",
    "MAX_CANONICAL_BYTES",
    "Changeset",
    "FieldValue",
    "JsonValue",
    "Op",
    "canonical_bytes",
    "canonical_json",
    "decode",
    "encode",
    "format_time",
    "from_dict",
    "sha",
    "to_dict",
]

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
#: Значение поля на входе: JSON плюс время, которое приводится к форме §10.4.
type FieldValue = (
    bool
    | int
    | float
    | str
    | datetime
    | date
    | Sequence[FieldValue]
    | Mapping[str, FieldValue]
    | None
)

FORMAT_VERSION: Final = 1
FORMAT_MAGIC: Final = b"CDC1"
#: Потолок распакованного объекта: защита от zstd-бомбы с чужого remote.
MAX_CANONICAL_BYTES = 64 * 1024 * 1024
#: Потолок вложенности JSON. Свой changeset глубже 4 уровней (объект → ops →
#: операция → fields) плюс вложенность значений колонок, а те плоские или
#: неглубокие; 64 — с запасом. Без потолка глубокий массив с чужого remote
#: роняет json.loads и _normalize RecursionError'ом вместо SYNC-102.
MAX_JSON_DEPTH = 64

OP_KINDS: Final = frozenset({"upsert", "delete"})
_SHA_RE = re.compile(r"[0-9a-f]{64}")
#: Символы, меняющие состояние сканера глубины. Класс из одиночных символов —
#: поиск линейный, без возвратов.
_JSON_SCAN_RE = re.compile(r'["\\\[\]{}]')
#: Сырые управляющие символы: в канонической форме их нет ни внутри строк
#: (json.dumps экранирует их), ни снаружи (разделители без пробелов).
_CONTROL_RE = re.compile(r"[\x00-\x1f]")
_CHANGESET_KEYS: Final = frozenset(
    {
        "v",
        "project",
        "parents",
        "replica",
        "actor",
        "epoch",
        "schema_head",
        "hlc_max",
        "digest",
        "ops",
    }
)
_OP_KEYS: Final = frozenset(
    {"op_id", "hlc", "entity_kind", "entity_uid", "kind", "fields", "base", "author"}
)


def format_time(value: datetime | date) -> str:
    """Время в форме §10.4; наивное ``datetime`` считается UTC, как в ``sync_capture``."""
    if not isinstance(value, datetime):
        return value.isoformat()
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    t = aware.astimezone(UTC)
    # strftime("%Y") не добивает нулями годы < 1000 на части платформ.
    return (
        f"{t.year:04d}-{t.month:02d}-{t.day:02d}T"
        f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}.{t.microsecond:06d}Z"
    )


def _normalize(value: object, where: str) -> JsonValue:
    match value:
        case None | bool() | int() | str():
            return value
        case float():
            if not math.isfinite(value):
                msg = f"{where}: нечисловое float {value!r} не представимо в JSON"
                raise ValueError(msg)
            return value
        case datetime() | date():
            return format_time(value)
        case Mapping():
            out: dict[str, JsonValue] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    msg = f"{where}: ключ {key!r} не строка"
                    raise ValueError(msg)
                out[key] = _normalize(item, f"{where}.{key}")
            return out
        case list() | tuple():
            return [_normalize(item, f"{where}[]") for item in value]
        case _:
            msg = f"{where}: тип {type(value).__name__} не сериализуется"
            raise ValueError(msg)


def _require_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{name}: нужна непустая строка, получено {value!r}"
        raise ValueError(msg)
    return value


def _require_sha(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
        msg = f"{name}: нужен sha256 в нижнем регистре, получено {value!r}"
        raise ValueError(msg)
    return value


def _require_hlc(value: object, name: str) -> str:
    text = _require_text(value, name)
    try:
        Hlc.parse(text)
    except ValueError as exc:
        msg = f"{name}: {exc}"
        raise ValueError(msg) from exc
    return text


def _require_object(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, Mapping):
        msg = f"{name}: нужен объект, получено {type(value).__name__}"
        raise ValueError(msg)
    normalized = _normalize(value, name)
    assert isinstance(normalized, dict)
    return normalized


@dataclass(frozen=True, slots=True)
class Op:
    """Операция журнала в changeset'е: ``sync_op`` без локальных колонок."""

    op_id: str
    hlc: str
    entity_kind: str
    entity_uid: str
    kind: str
    fields: Mapping[str, FieldValue]
    base: Mapping[str, FieldValue]
    author: str

    def __post_init__(self) -> None:
        for name in ("op_id", "entity_kind", "entity_uid", "author"):
            _require_text(getattr(self, name), name)
        _require_hlc(self.hlc, "hlc")
        if not isinstance(self.kind, str) or self.kind not in OP_KINDS:
            msg = f"kind: ожидается одно из {sorted(OP_KINDS)}, получено {self.kind!r}"
            raise ValueError(msg)
        # Время и кортежи приводятся к JSON сразу: тогда Op, собранный из журнала,
        # и Op, прочитанный с remote, равны и дают одни и те же байты.
        object.__setattr__(self, "fields", _require_object(self.fields, "fields"))
        object.__setattr__(self, "base", _require_object(self.base, "base"))

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "op_id": self.op_id,
            "hlc": self.hlc,
            "entity_kind": self.entity_kind,
            "entity_uid": self.entity_uid,
            "kind": self.kind,
            "fields": _require_object(self.fields, "fields"),
            "base": _require_object(self.base, "base"),
            "author": self.author,
        }


def _op_sort_key(op: Op) -> tuple[str, str]:
    return op.hlc, op.op_id


@dataclass(frozen=True, slots=True)
class Changeset:
    """Changeset v1 (§10.4). ``ops`` хранятся в каноническом порядке ``(hlc, op_id)``."""

    project: str
    parents: Sequence[str]
    replica: str
    actor: str
    epoch: str
    schema_head: str
    hlc_max: str
    digest: str
    ops: Sequence[Op]
    v: int = field(default=FORMAT_VERSION)

    def __post_init__(self) -> None:
        if type(self.v) is not int or self.v != FORMAT_VERSION:
            msg = f"v: поддерживается версия {FORMAT_VERSION}, получено {self.v!r}"
            raise ValueError(msg)
        for name in ("project", "replica", "actor", "epoch", "schema_head"):
            _require_text(getattr(self, name), name)
        _require_sha(self.digest, "digest")
        hlc_max = _require_hlc(self.hlc_max, "hlc_max")

        if isinstance(self.parents, str) or not isinstance(self.parents, Sequence):
            msg = "parents: нужен список sha"
            raise ValueError(msg)
        parents = tuple(_require_sha(p, "parents[]") for p in self.parents)
        if len(set(parents)) != len(parents):
            msg = f"parents: повтор родителя {list(parents)}"
            raise ValueError(msg)

        if not all(isinstance(op, Op) for op in self.ops):
            msg = "ops: нужны объекты Op"
            raise ValueError(msg)
        ops = tuple(sorted(self.ops, key=_op_sort_key))
        if len({op.op_id for op in ops}) != len(ops):
            msg = "ops: повтор op_id"
            raise ValueError(msg)
        # hlc_max — производное поле: расхождение с операциями значит, что
        # упаковщик собрал changeset неверно, а у получателя оно сломало бы
        # observe() часов.
        if ops and ops[-1].hlc != hlc_max:
            msg = f"hlc_max: {hlc_max!r} ≠ максимальному hlc операций {ops[-1].hlc!r}"
            raise ValueError(msg)

        object.__setattr__(self, "parents", parents)
        object.__setattr__(self, "ops", ops)


def to_dict(cs: Changeset) -> dict[str, JsonValue]:
    return {
        "v": cs.v,
        "project": cs.project,
        "parents": list(cs.parents),
        "replica": cs.replica,
        "actor": cs.actor,
        "epoch": cs.epoch,
        "schema_head": cs.schema_head,
        "hlc_max": cs.hlc_max,
        "digest": cs.digest,
        "ops": [op.to_dict() for op in cs.ops],
    }


def _check_keys(raw: Mapping[str, object], expected: frozenset[str], where: str) -> None:
    missing = sorted(expected - raw.keys())
    if missing:
        msg = f"{where}: нет полей {', '.join(missing)}"
        raise ValueError(msg)
    extra = sorted(str(k) for k in raw.keys() - expected)
    if extra:
        msg = f"{where}: лишние поля {', '.join(extra)}"
        raise ValueError(msg)


def _op_from_dict(raw: object) -> Op:
    if not isinstance(raw, Mapping):
        msg = "ops[]: нужен объект"
        raise ValueError(msg)
    _check_keys(raw, _OP_KEYS, "ops[]")
    fields = raw["fields"]
    base = raw["base"]
    if not isinstance(fields, Mapping) or not isinstance(base, Mapping):
        msg = "ops[]: fields и base — объекты"
        raise ValueError(msg)
    return Op(
        op_id=raw["op_id"],
        hlc=raw["hlc"],
        entity_kind=raw["entity_kind"],
        entity_uid=raw["entity_uid"],
        kind=raw["kind"],
        fields=fields,
        base=base,
        author=raw["author"],
    )


def from_dict(raw: object) -> Changeset:
    """Changeset из разобранного JSON с проверкой §10.4; ``ValueError`` на нарушение."""
    if not isinstance(raw, Mapping):
        msg = "changeset: нужен объект"
        raise ValueError(msg)
    _check_keys(raw, _CHANGESET_KEYS, "changeset")
    ops = raw["ops"]
    parents = raw["parents"]
    if not isinstance(ops, list):
        msg = "ops: нужен список"
        raise ValueError(msg)
    if not isinstance(parents, list):
        msg = "parents: нужен список"
        raise ValueError(msg)
    return Changeset(
        v=raw["v"],
        project=raw["project"],
        parents=parents,
        replica=raw["replica"],
        actor=raw["actor"],
        epoch=raw["epoch"],
        schema_head=raw["schema_head"],
        hlc_max=raw["hlc_max"],
        digest=raw["digest"],
        ops=[_op_from_dict(op) for op in ops],
    )


def canonical_bytes(cs: Changeset) -> bytes:
    """Каноническая форма §10.4 — то, от чего берётся адрес."""
    return json.dumps(
        to_dict(cs),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json(value: FieldValue) -> bytes:
    """Произвольное значение в канонической форме §10.4 (дайджест, §3.7).

    Те же правила, что у ``canonical_bytes``: время приводится к форме §10.4,
    ключи по алфавиту, без пробелов и экранирования.
    """
    return json.dumps(
        _normalize(value, "value"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha(cs: Changeset) -> str:
    """Адрес changeset'а: ``sha256`` канонической формы, hex в нижнем регистре."""
    return hashlib.sha256(canonical_bytes(cs)).hexdigest()


def encode(cs: Changeset) -> bytes:
    """Объект для remote: ``CDC1`` + zstd канонической формы."""
    return FORMAT_MAGIC + zstd.compress(canonical_bytes(cs))


def _inflate(payload: bytes) -> bytes:
    limit = MAX_CANONICAL_BYTES
    decompressor = zstd.ZstdDecompressor()
    try:
        raw = decompressor.decompress(payload, max_length=limit + 1)
    except zstd.ZstdError as exc:
        msg = f"zstd не распаковывается: {exc}"
        raise ValueError(msg) from exc
    if len(raw) > limit:
        msg = f"распакованный объект больше {limit} байт"
        raise ValueError(msg)
    if not decompressor.eof:
        msg = "zstd-кадр обрезан"
        raise ValueError(msg)
    if decompressor.unused_data:
        msg = "после zstd-кадра лишние байты"
        raise ValueError(msg)
    return raw


def _check_depth(text: str) -> None:
    """Отказать до json.loads, если вложенность больше ``MAX_JSON_DEPTH``.

    Один линейный проход по ``"``, ``\\`` и скобкам с состоянием «внутри
    строки» / «следующий символ экранирован»; скобки внутри строк не считаются,
    отказ — сразу при превышении. Регулярка на строковые литералы здесь не
    годится: на незакрытой строке или длинной серии обратных слэшей она
    квадратична и обесценивает потолок размера.
    """
    if _CONTROL_RE.search(text) is not None:
        msg = "сырой управляющий символ: объект не в канонической форме"
        raise ValueError(msg)
    limit = MAX_JSON_DEPTH
    depth = 0
    in_string = False
    escaped_at = -1
    for match in _JSON_SCAN_RE.finditer(text):
        pos = match.start()
        if pos == escaped_at:
            continue
        char = match.group()
        if in_string:
            if char == "\\":
                escaped_at = pos + 1
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > limit:
                msg = f"вложенность JSON больше {limit}"
                raise ValueError(msg)
        elif char in "]}":
            depth -= 1


#: Всё, чем разбор и проверка недоверенного объекта могут упасть. Сверх
#: ValueError (UnicodeDecodeError, JSONDecodeError, лимит цифр int, проверки
#: §10.4) — страховка: исключение из кода разбора не должно уронить pull
#: сырым трейсбеком вместо SYNC-102.
_PARSE_ERRORS = (ValueError, TypeError, KeyError, OverflowError, RecursionError)


def decode(data: bytes, expected_sha: str) -> Changeset:
    """Прочитать объект с remote; ``ObjectMissingError`` (SYNC-102), если он непригоден."""
    if data[: len(FORMAT_MAGIC)] != FORMAT_MAGIC:
        msg = (
            f"объект {expected_sha}: неизвестный формат {bytes(data[:4])!r}, "
            f"ожидается {FORMAT_MAGIC!r}"
        )
        raise ObjectMissingError(msg)
    try:
        raw = _inflate(data[len(FORMAT_MAGIC) :])
    except ValueError as exc:
        msg = f"объект {expected_sha}: {exc}"
        raise ObjectMissingError(msg) from exc

    actual = hashlib.sha256(raw).hexdigest()
    if actual != expected_sha:
        msg = f"объект {expected_sha}: sha содержимого {actual}"
        raise ObjectMissingError(msg)

    try:
        text = raw.decode("utf-8")
        _check_depth(text)
        cs = from_dict(json.loads(text))
        canonical = canonical_bytes(cs)
    except _PARSE_ERRORS as exc:
        msg = f"объект {expected_sha}: не changeset v{FORMAT_VERSION}: {exc}"
        raise ObjectMissingError(msg) from exc
    # sha сошёлся, но байты не канонические: тот же changeset имел бы другой
    # адрес, и sha(decode(x)) расходился бы с адресом, по которому его читали.
    if canonical != raw:
        msg = f"объект {expected_sha}: содержимое не в канонической форме §10.4"
        raise ObjectMissingError(msg)
    return cs
