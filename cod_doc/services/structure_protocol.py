"""Shared structure protocol: validation, paths, hashes, payload limits.

Mirrors ai-reviewer ``lib/structure-protocol.mjs`` / ``structure-evidence.mjs``.
JSON is canonical. Unknown additive fields are ignored; unsupported majors fail.
"""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import zlib
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

PROTOCOL_VERSION: Final[int] = 1
NORMALIZER_VERSION: Final[int] = 1
FACTS_SCHEMA: Final[str] = "code-structure/structure-facts.v1"
ASSESSMENT_SCHEMA: Final[str] = "code-structure/structure-assessment.v1"
OBLIGATIONS_SCHEMA: Final[str] = "code-structure/obligations-export.v1"

TRUST_TIERS: Final[tuple[str, ...]] = ("signed_ci", "trusted_local", "untrusted")
PUBLISHABLE_TRUST: Final[frozenset[str]] = frozenset({"signed_ci", "trusted_local"})
TEMPORAL_ALIGNMENTS: Final[tuple[str, ...]] = ("aligned", "mismatch", "unknown")
CLAIM_KINDS: Final[tuple[str, ...]] = (
    "entity_exists",
    "exports",
    "signature",
    "depends_on",
    "forbids_dependency",
    "scenario",
)
CLAIM_STATUSES: Final[tuple[str, ...]] = ("draft", "confirmed")
EDGE_OBLIGATION_KINDS: Final[frozenset[str]] = frozenset(
    {"error_path", "boundary_value", "invariant"}
)

MAX_UNCOMPRESSED_BYTES: Final[int] = 20 * 1024 * 1024
MAX_COMPRESSED_BYTES: Final[int] = 10 * 1024 * 1024
MAX_DECOMPRESSION_RATIO: Final[int] = 50
MAX_ENTITIES: Final[int] = 5000
MAX_EDGES: Final[int] = 15_000
MAX_TESTS: Final[int] = 10_000
MAX_OBLIGATIONS: Final[int] = 10_000
MAX_PATH_LENGTH: Final[int] = 1000
MAX_STRING_LENGTH: Final[int] = 10_000
MAX_DEPTH: Final[int] = 30
MAX_CONTEXT_SEEDS: Final[int] = 20
MAX_CONTEXT_EDGES: Final[int] = 50
MAX_CONTEXT_OBLIGATIONS: Final[int] = 10
MAX_CONTEXT_GAPS: Final[int] = 15
MAX_CONTEXT_BYTES: Final[int] = 32 * 1024
RETENTION_PER_BRANCH: Final[int] = 10

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HEAD_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
_WIN_ABS_RE = re.compile(r"^[A-Za-z]:/")


class StructureProtocolError(ValueError):
    """Invalid structure payload, path, trust tier, or size limit."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def stable_id(prefix: str, *parts: object) -> str:
    joined = "\0".join(str(part or "") for part in parts)
    return f"{prefix}:{sha256_text(joined)[:24]}"


def as_object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise StructureProtocolError(f"{label} must be an object")
    return {str(key): item for key, item in value.items()}


def as_list(value: object, *, label: str) -> list[object]:
    if not isinstance(value, list):
        raise StructureProtocolError(f"{label} must be an array")
    return list(value)


def as_int(value: object, *, label: str) -> int:
    """Сузить payload-значение до int: отсутствующее и пустое считаем нулём."""
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise StructureProtocolError(f"{label} must be a number")
    try:
        return int(value)
    except ValueError as exc:
        raise StructureProtocolError(f"{label} must be a number") from exc


def require_sha256(value: object, *, label: str) -> str:
    text = str(value or "")
    if not _SHA256_RE.fullmatch(text):
        raise StructureProtocolError(f"{label} must be sha256")
    return text


def require_head_sha(value: object, *, label: str = "headSha") -> str:
    text = str(value or "")
    if not _HEAD_SHA_RE.fullmatch(text):
        raise StructureProtocolError(f"{label} must be a git-like hex sha")
    return text


def normalize_repo_path(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw or "\0" in raw or raw.startswith("/") or _WIN_ABS_RE.match(raw):
        raise StructureProtocolError(f"unsafe repository path: {raw!r}")
    normalized = posixpath.normpath(raw)
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized == ".." or normalized.startswith("../"):
        raise StructureProtocolError(f"unsafe repository path: {raw!r}")
    if len(normalized) > MAX_PATH_LENGTH:
        raise StructureProtocolError("path length limit exceeded")
    return normalized


def _inspect(value: object, *, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise StructureProtocolError(f"structure payload nesting exceeds {MAX_DEPTH}")
    if isinstance(value, str) and len(value) > MAX_STRING_LENGTH:
        raise StructureProtocolError(
            f"structure payload string exceeds {MAX_STRING_LENGTH} characters"
        )
    if isinstance(value, list):
        for item in value:
            _inspect(item, depth=depth + 1)
    elif isinstance(value, dict):
        for item in value.values():
            _inspect(item, depth=depth + 1)


def _assert_kind(payload: Mapping[str, object], kind: str, schema_ref: str) -> None:
    version = payload.get("version")
    if version != PROTOCOL_VERSION:
        raise StructureProtocolError(f"unsupported {kind} version: {version}")
    if payload.get("kind") != kind:
        raise StructureProtocolError(f"expected kind={kind}")
    if payload.get("schemaRef") != schema_ref:
        raise StructureProtocolError(f"expected schemaRef={schema_ref}")


def _walk_paths(records: Sequence[object], field: str = "path") -> None:
    for record in records:
        if not isinstance(record, dict):
            continue
        path = record.get(field)
        if path is not None:
            normalize_repo_path(str(path))


def validate_structure_facts(payload: Mapping[str, object]) -> dict[str, object]:
    data = dict(payload)
    _assert_kind(data, "structure_facts", FACTS_SCHEMA)
    _inspect(data)
    require_sha256(data.get("fingerprint"), label="fingerprint")
    provenance = as_object(data.get("provenance"), label="provenance")
    require_head_sha(provenance.get("headSha"))
    facts = as_object(data.get("facts"), label="facts")
    boundaries = as_list(facts.get("boundaries"), label="facts.boundaries")
    entities = as_list(facts.get("entities"), label="facts.entities")
    as_list(facts.get("contracts"), label="facts.contracts")
    dependencies = as_list(facts.get("dependencies"), label="facts.dependencies")
    test_cases = as_list(facts.get("testCases"), label="facts.testCases")
    if len(entities) > MAX_ENTITIES:
        raise StructureProtocolError("entity limit exceeded")
    if len(dependencies) > MAX_EDGES:
        raise StructureProtocolError("dependency limit exceeded")
    if len(test_cases) > MAX_TESTS:
        raise StructureProtocolError("test case limit exceeded")
    _walk_paths(entities)
    _walk_paths(test_cases)
    for boundary in boundaries:
        if not isinstance(boundary, dict):
            continue
        for path in as_list(boundary.get("paths") or [], label="boundary.paths"):
            normalize_repo_path(str(path))
    as_list(data.get("identityEvents") or [], label="identityEvents")
    as_object(data.get("views") or {}, label="views")
    return data


def validate_structure_assessment(payload: Mapping[str, object]) -> dict[str, object]:
    data = dict(payload)
    _assert_kind(data, "structure_assessment", ASSESSMENT_SCHEMA)
    _inspect(data)
    for field in ("fingerprint", "factsFingerprint", "coverageHash", "thresholdsHash"):
        require_sha256(data.get(field), label=field)
    alignment = str(data.get("temporalAlignment") or "")
    if alignment not in TEMPORAL_ALIGNMENTS:
        raise StructureProtocolError("invalid temporalAlignment")
    as_list(data.get("coverageObservations"), label="coverageObservations")
    assessments = as_object(data.get("assessments"), label="assessments")
    as_list(assessments.get("contractScenarios"), label="assessments.contractScenarios")
    as_list(data.get("hints"), label="hints")
    as_list(data.get("receipts"), label="receipts")
    return data


def validate_obligations_export(payload: Mapping[str, object]) -> dict[str, object]:
    data = dict(payload)
    _assert_kind(data, "obligations_export", OBLIGATIONS_SCHEMA)
    _inspect(data)
    obligations = as_list(data.get("obligations"), label="obligations")
    if len(obligations) > MAX_OBLIGATIONS:
        raise StructureProtocolError("obligation limit exceeded")
    for item in obligations:
        obligation = as_object(item, label="obligation")
        if (
            not obligation.get("id")
            or not obligation.get("contentHash")
            or not obligation.get("statement")
        ):
            raise StructureProtocolError("obligation id, contentHash and statement are required")
        require_sha256(obligation.get("contentHash"), label="obligation.contentHash")
    return data


def parse_structure_payload(text: str) -> dict[str, object]:
    raw = text.encode("utf-8")
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise StructureProtocolError(f"structure payload exceeds {MAX_UNCOMPRESSED_BYTES} bytes")
    loaded = json.loads(text)
    payload = as_object(loaded, label="payload")
    kind = payload.get("kind")
    if kind == "structure_facts":
        return validate_structure_facts(payload)
    if kind == "structure_assessment":
        return validate_structure_assessment(payload)
    if kind == "obligations_export":
        return validate_obligations_export(payload)
    raise StructureProtocolError(f"unsupported structure payload kind: {kind}")


def compress_payload(payload: Mapping[str, object]) -> tuple[bytes, str, int]:
    raw = canonical_json(dict(payload)).encode("utf-8")
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise StructureProtocolError(f"structure payload exceeds {MAX_UNCOMPRESSED_BYTES} bytes")
    compressed = zlib.compress(raw, level=9)
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise StructureProtocolError("compressed structure payload exceeds limit")
    return compressed, sha256_bytes(raw), len(raw)


def decompress_payload(
    compressed: bytes, *, expected_sha256: str | None = None
) -> dict[str, object]:
    if len(compressed) > MAX_COMPRESSED_BYTES:
        raise StructureProtocolError("compressed structure payload exceeds limit")
    decoder = zlib.decompressobj()
    raw = decoder.decompress(compressed, MAX_UNCOMPRESSED_BYTES + 1)
    if decoder.unconsumed_tail or len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise StructureProtocolError("structure payload exceeds uncompressed limit")
    leftover = decoder.flush()
    raw += leftover
    if len(raw) > MAX_UNCOMPRESSED_BYTES:
        raise StructureProtocolError("structure payload exceeds uncompressed limit")
    if len(compressed) > 0 and len(raw) / len(compressed) > MAX_DECOMPRESSION_RATIO:
        raise StructureProtocolError("structure payload decompression ratio exceeded")
    digest = sha256_bytes(raw)
    if expected_sha256 and digest != expected_sha256:
        raise StructureProtocolError("payload sha256 mismatch")
    return as_object(json.loads(raw.decode("utf-8")), label="payload")


def normalize_trust_tier(value: object) -> str:
    tier = str(value or "untrusted")
    if tier not in TRUST_TIERS:
        raise StructureProtocolError(f"invalid trustTier: {tier}")
    return tier


def can_publish_current(trust_tier: str) -> bool:
    return trust_tier in PUBLISHABLE_TRUST


def finding_fingerprint(rule_id: str, subject_refs: Sequence[str]) -> str:
    refs = tuple(sorted({str(item) for item in subject_refs if item}))
    return stable_id("structure-finding", rule_id, *refs)
