"""Формат changeset v1 (RFC 29 §10.4, CLO-010).

Golden-тест держит каноническую форму: любая правка сериализации, меняющая байты,
меняет адреса всех уже записанных changeset'ов, и реплики перестают находить
объекты друг друга. Такая правка — новая версия формата, а не рефакторинг.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time
from compression import zstd
from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from cod_doc.services.sync_service import changeset as cs_mod
from cod_doc.services.sync_service.changeset import (
    FORMAT_MAGIC,
    Changeset,
    Op,
    canonical_bytes,
    decode,
    encode,
    from_dict,
    sha,
)
from cod_doc.services.sync_service.errors import ObjectMissingError

_REPLICA = "0199f2b0-7c3a-7000-8000-000000000001"
_PARENT = "a" * 64
_DIGEST = "d" * 64


def _op(op_id: str, hlc_ms: int, **kw: object) -> Op:
    defaults: dict[str, object] = {
        "op_id": op_id,
        "hlc": f"{hlc_ms:013d}.0000.{_REPLICA}",
        "entity_kind": "task",
        "entity_uid": "0199f2b0-7c3a-7000-8000-0000000000aa",
        "kind": "upsert",
        "fields": {"status": "done"},
        "base": {"status": "in_progress"},
        "author": "agent:claude-opus",
    }
    defaults.update(kw)
    return Op(**defaults)  # type: ignore[arg-type]


def _golden() -> Changeset:
    return Changeset(
        project="0199f2b0-7c3a-7000-8000-0000000000ff",
        parents=(_PARENT,),
        replica=_REPLICA,
        actor="dakh",
        epoch="0050_sync_rank",
        schema_head="0050_sync_rank",
        hlc_max=f"{1760000000002:013d}.0000.{_REPLICA}",
        digest=_DIGEST,
        ops=(
            _op(
                "01K7Z0000000000000000000B2",
                1760000000002,
                kind="delete",
                fields={},
                base={"title": "Старая задача"},
            ),
            _op(
                "01K7Z0000000000000000000A1",
                1760000000001,
                fields={
                    "title": "Синхронизация — «ёлка»",
                    "status": "done",
                    "completed_at": datetime(
                        2026, 10, 17, 15, 4, 5, tzinfo=timezone(timedelta(hours=3))
                    ),
                    "estimate": 1.5,
                    "tags": ["b", "a"],
                    "meta": {"z": None, "a": True},
                },
            ),
        ),
    )


GOLDEN_CANONICAL = (
    '{"actor":"dakh","digest":"' + _DIGEST + '","epoch":"0050_sync_rank",'
    '"hlc_max":"1760000000002.0000.' + _REPLICA + '",'
    '"ops":['
    '{"author":"agent:claude-opus","base":{"status":"in_progress"},'
    '"entity_kind":"task","entity_uid":"0199f2b0-7c3a-7000-8000-0000000000aa",'
    '"fields":{"completed_at":"2026-10-17T12:04:05.000000Z","estimate":1.5,'
    '"meta":{"a":true,"z":null},"status":"done","tags":["b","a"],'
    '"title":"Синхронизация — «ёлка»"},'
    '"hlc":"1760000000001.0000.' + _REPLICA + '","kind":"upsert",'
    '"op_id":"01K7Z0000000000000000000A1"},'
    '{"author":"agent:claude-opus","base":{"title":"Старая задача"},'
    '"entity_kind":"task","entity_uid":"0199f2b0-7c3a-7000-8000-0000000000aa",'
    '"fields":{},"hlc":"1760000000002.0000.' + _REPLICA + '","kind":"delete",'
    '"op_id":"01K7Z0000000000000000000B2"}'
    "],"
    '"parents":["' + _PARENT + '"],'
    '"project":"0199f2b0-7c3a-7000-8000-0000000000ff",'
    '"replica":"' + _REPLICA + '","schema_head":"0050_sync_rank","v":1}'
).encode()

GOLDEN_SHA = "87d8b8ca4e1e8c5bbc18b418c33506f6c4a84d883d01dee3a32cdc57c40e3f82"


# --- каноническая форма ---------------------------------------------------------


def test_golden_canonical_bytes() -> None:
    assert canonical_bytes(_golden()) == GOLDEN_CANONICAL


def test_golden_sha() -> None:
    assert sha(_golden()) == GOLDEN_SHA


def test_sha_ignores_insertion_order_of_ops_and_keys() -> None:
    golden = _golden()
    shuffled = dataclasses.replace(
        golden,
        ops=tuple(
            dataclasses.replace(op, fields=dict(reversed(list(op.fields.items()))))
            for op in reversed(golden.ops)
        ),
    )
    assert sha(shuffled) == GOLDEN_SHA


def test_ops_tie_on_hlc_break_by_op_id() -> None:
    a = _op("01K7Z0000000000000000000A1", 1760000000001)
    b = _op("01K7Z0000000000000000000A0", 1760000000001)
    cs = dataclasses.replace(_golden(), ops=(a, b), hlc_max=a.hlc)
    assert [op.op_id for op in cs.ops] == [b.op_id, a.op_id]


def test_time_format_is_utc_microseconds_z() -> None:
    naive = datetime(2026, 1, 2, 3, 4, 5)
    aware = datetime(2026, 1, 2, 6, 4, 5, 7, tzinfo=timezone(timedelta(hours=3)))
    old = datetime(999, 1, 2, tzinfo=UTC)
    op = _op(
        "01K7Z0000000000000000000A1",
        1,
        fields={"a": naive, "b": aware, "c": old, "d": date(2026, 1, 2)},
    )
    cs = dataclasses.replace(_golden(), ops=(op,), hlc_max=op.hlc)
    fields = json.loads(canonical_bytes(cs))["ops"][0]["fields"]
    assert fields == {
        "a": "2026-01-02T03:04:05.000000Z",
        "b": "2026-01-02T03:04:05.000007Z",
        "c": "0999-01-02T00:00:00.000000Z",
        "d": "2026-01-02",
    }


def test_canonical_is_utf8_without_escapes() -> None:
    assert "«ёлка»".encode() in canonical_bytes(_golden())


# --- хранение -------------------------------------------------------------------


def test_encode_is_magic_plus_zstd_of_canonical() -> None:
    data = encode(_golden())
    assert data[:4] == FORMAT_MAGIC == b"CDC1"
    assert zstd.decompress(data[4:]) == GOLDEN_CANONICAL


def test_decode_roundtrip() -> None:
    decoded = decode(encode(_golden()), GOLDEN_SHA)
    assert sha(decoded) == GOLDEN_SHA
    assert canonical_bytes(decoded) == GOLDEN_CANONICAL


def _assert_sync_102(data: bytes, expected: str = GOLDEN_SHA) -> None:
    with pytest.raises(ObjectMissingError) as exc:
        decode(data, expected)
    assert exc.value.code == "SYNC-102"
    assert str(exc.value).startswith("SYNC-102")


def test_decode_rejects_foreign_sha() -> None:
    _assert_sync_102(encode(_golden()), "b" * 64)


def test_decode_rejects_substituted_content() -> None:
    other = dataclasses.replace(_golden(), actor="mallory")
    _assert_sync_102(encode(other))


def test_decode_rejects_bad_magic() -> None:
    _assert_sync_102(b"CDC2" + encode(_golden())[4:])


def test_decode_rejects_broken_zstd() -> None:
    _assert_sync_102(FORMAT_MAGIC + b"not zstd at all")


def test_decode_rejects_truncated_object() -> None:
    data = encode(_golden())
    _assert_sync_102(data[:-3])


def test_decode_rejects_trailing_garbage() -> None:
    _assert_sync_102(encode(_golden()) + zstd.compress(b"x"))


def test_decode_rejects_non_canonical_bytes_even_with_matching_sha() -> None:
    loose = json.dumps(json.loads(GOLDEN_CANONICAL), indent=1, ensure_ascii=False).encode()
    _assert_sync_102(FORMAT_MAGIC + zstd.compress(loose), hashlib.sha256(loose).hexdigest())


def test_decode_rejects_invalid_structure_with_matching_sha() -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    raw["v"] = 2
    body = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    _assert_sync_102(FORMAT_MAGIC + zstd.compress(body), hashlib.sha256(body).hexdigest())


def test_decode_rejects_oversized_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs_mod, "MAX_CANONICAL_BYTES", 64)
    _assert_sync_102(encode(_golden()))


# --- валидация §10.4 ------------------------------------------------------------


def test_from_dict_roundtrip() -> None:
    assert sha(from_dict(json.loads(GOLDEN_CANONICAL))) == GOLDEN_SHA


@pytest.mark.parametrize(
    "field",
    [
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
    ],
)
def test_from_dict_requires_every_field(field: str) -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    del raw[field]
    with pytest.raises(ValueError, match=field):
        from_dict(raw)


def test_from_dict_rejects_unknown_field() -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    raw["extra"] = 1
    with pytest.raises(ValueError, match="extra"):
        from_dict(raw)


@pytest.mark.parametrize(
    "field", ["op_id", "hlc", "entity_kind", "entity_uid", "kind", "fields", "base", "author"]
)
def test_from_dict_requires_every_op_field(field: str) -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    del raw["ops"][0][field]
    with pytest.raises(ValueError, match=field):
        from_dict(raw)


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"parents": ["not-a-sha"]}, "parents"),
        ({"parents": [_PARENT, _PARENT]}, "parents"),
        ({"digest": "D" * 64}, "digest"),
        ({"actor": ""}, "actor"),
        ({"hlc_max": f"{1760000000009:013d}.0000.{_REPLICA}"}, "hlc_max"),
        ({"hlc_max": "garbage"}, "hlc"),
    ],
)
def test_changeset_validation(change: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        dataclasses.replace(_golden(), **change)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"kind": "patch"}, "kind"),
        ({"hlc": "yesterday"}, "hlc"),
        ({"entity_uid": ""}, "entity_uid"),
        ({"fields": {1: "x"}}, "fields"),
        ({"fields": {"x": float("nan")}}, "fields"),
        ({"fields": {"x": object()}}, "fields"),
    ],
)
def test_op_validation(change: dict[str, object], match: str) -> None:
    with pytest.raises(ValueError, match=match):
        _op("01K7Z0000000000000000000A1", 1, **change)


def test_duplicate_op_ids_rejected() -> None:
    a = _op("01K7Z0000000000000000000A1", 1)
    b = _op("01K7Z0000000000000000000A1", 2)
    with pytest.raises(ValueError, match="op_id"):
        dataclasses.replace(_golden(), ops=(a, b), hlc_max=b.hlc)


def test_empty_ops_allowed_for_merge_changeset() -> None:
    cs = dataclasses.replace(_golden(), ops=(), parents=(_PARENT, "b" * 64))
    assert decode(encode(cs), sha(cs)).parents == (_PARENT, "b" * 64)


def test_parents_order_is_kept() -> None:
    cs = dataclasses.replace(_golden(), parents=("b" * 64, _PARENT))
    assert json.loads(canonical_bytes(cs))["parents"] == ["b" * 64, _PARENT]


# --- недоверенный вход: любой сбой разбора — SYNC-102, не сырое исключение -------


def _addressed(body: bytes) -> tuple[bytes, str]:
    return FORMAT_MAGIC + zstd.compress(body), hashlib.sha256(body).hexdigest()


def _canonical(raw: object) -> bytes:
    return json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def test_decode_rejects_deep_nesting_before_parsing() -> None:
    _assert_sync_102(*_addressed(b"[" * 200_000 + b"]" * 200_000))


def test_decode_rejects_deep_nesting_inside_fields() -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    deep: object = 1
    for _ in range(cs_mod.MAX_JSON_DEPTH):
        deep = [deep]
    raw["ops"][0]["fields"]["deep"] = deep
    _assert_sync_102(*_addressed(_canonical(raw)))


def test_depth_guard_ignores_brackets_inside_strings() -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    raw["ops"][0]["fields"]["title"] = '[{"\\' * 500
    data, address = _addressed(_canonical(raw))
    assert decode(data, address).ops[0].fields["title"] == '[{"\\' * 500


_ADVERSARIAL: list[bytes] = [
    b'"' + b"\\\\" * 200_000,  # незакрытая строка из пар обратных слэшей
    b'"' * 200_001,  # нечётная серия кавычек
    # каждая экранированная кавычка — новое начало незакрытого литерала: на
    # регулярке '"(?:[^"\\]|\\.)*"' это n попыток по n символов
    b'"' + b'\\"' * 100_000,
    b"[" * 100_000,
    b'["' + b"\\" * 400_001,  # нечётная серия слэшей без закрывающей кавычки
]


@pytest.mark.parametrize(
    "body", _ADVERSARIAL, ids=["backslashes", "quotes", "escaped-quotes", "brackets", "odd"]
)
def test_depth_scan_is_linear_on_adversarial_input(body: bytes) -> None:
    data, address = _addressed(body)
    started = time.perf_counter()
    _assert_sync_102(data, address)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize("char", [b"\x00", b"\n", b"\x1f"])
def test_decode_rejects_raw_control_chars(char: bytes) -> None:
    raw = GOLDEN_CANONICAL.replace(b'"actor":"dakh"', b'"actor":"da' + char + b'kh"')
    _assert_sync_102(*_addressed(raw))


def test_decode_stops_inflating_zstd_bomb(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cs_mod, "MAX_CANONICAL_BYTES", 1024 * 1024)
    bomb = FORMAT_MAGIC + zstd.compress(b"0" * (32 * 1024 * 1024))
    assert len(bomb) < 64 * 1024
    _assert_sync_102(bomb, "0" * 64)


@pytest.mark.parametrize(
    "body",
    [b"[]", b'"changeset"', b"null", b"42", b"\xff\xfe", b"{", b"NaN"],
    ids=["list", "string", "null", "int", "not-utf8", "truncated-json", "nan"],
)
def test_decode_rejects_non_object_top_level(body: bytes) -> None:
    _assert_sync_102(*_addressed(body))


def test_decode_rejects_huge_int() -> None:
    raw = GOLDEN_CANONICAL.replace(b'"estimate":1.5', b'"estimate":' + b"9" * 10_000)
    _assert_sync_102(*_addressed(raw))


def test_decode_rejects_float_overflow() -> None:
    raw = GOLDEN_CANONICAL.replace(b'"estimate":1.5', b'"estimate":1e999')
    _assert_sync_102(*_addressed(raw))


_WRONG_TYPES: list[tuple[str, object]] = [
    ("v", "1"),
    ("v", True),
    ("project", 7),
    ("parents", "a" * 64),
    ("parents", [1]),
    ("parents", {"a": 1}),
    ("replica", None),
    ("actor", ["dakh"]),
    ("epoch", {}),
    ("schema_head", 1.5),
    ("hlc_max", 1760000000002),
    ("digest", ["d" * 64]),
    ("ops", {"0": {}}),
    ("ops", ["op"]),
    ("ops", [None]),
]


@pytest.mark.parametrize(
    ("field", "value"), _WRONG_TYPES, ids=[f"{f}={v!r}"[:40] for f, v in _WRONG_TYPES]
)
def test_decode_rejects_wrong_type_per_field(field: str, value: object) -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    raw[field] = value
    _assert_sync_102(*_addressed(_canonical(raw)))


_WRONG_OP_TYPES: list[tuple[str, object]] = [
    ("op_id", 1),
    ("hlc", None),
    ("entity_kind", []),
    ("entity_uid", {}),
    ("kind", ["upsert"]),
    ("kind", {"upsert": 1}),
    ("fields", []),
    ("fields", "status=done"),
    ("base", None),
    ("author", False),
]


@pytest.mark.parametrize(
    ("field", "value"), _WRONG_OP_TYPES, ids=[f"{f}={v!r}"[:40] for f, v in _WRONG_OP_TYPES]
)
def test_decode_rejects_wrong_type_per_op_field(field: str, value: object) -> None:
    raw = json.loads(GOLDEN_CANONICAL)
    raw["ops"][0][field] = value
    _assert_sync_102(*_addressed(_canonical(raw)))
