"""SensitivityScanner — secret + PII detection over document bodies.

COD-025 / SD-001. Implements [standards/sensitive-data.md §2](../../docs/system/standards/sensitive-data.md).

The scanner is **advisory** — it raises no errors and does not block writes;
callers (validation.audit_sensitivity, future `cod-doc audit --sensitivity`
CLI) decide whether a finding warrants a warning or an error.

Detected patterns (≥4 secret kinds + PII heuristic):

- `aws_access_key`     — `AKIA[0-9A-Z]{16}`
- `github_pat`         — `ghp_[A-Za-z0-9]{36}`
- `slack_token`        — `xox[baprs]-[A-Za-z0-9-]{8,}`
- `pem_private_key`    — PEM block `-----BEGIN ... PRIVATE KEY-----`
- `jwt_token`          — base64url `header.payload.signature`
- `generic_high_entropy` — long base64-ish/hex string with high Shannon entropy
- `pii_contact`        — email + phone-number co-occurrence within 80 chars

Findings are line-numbered and snippets are partially redacted before being
returned, so log streams cannot leak the secret verbatim.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True, slots=True)
class SensitivityFinding:
    """One detected sensitive-content occurrence in a document body."""

    kind: str
    line: int  # 1-based line number
    snippet: str  # short, partially-redacted excerpt safe to log
    confidence: float  # 0.0–1.0 — heuristic certainty


# --------------------------------------------------------------------------- #
# Patterns                                                                      #
# --------------------------------------------------------------------------- #


_AWS_ACCESS_KEY_RE: Final = re.compile(r"\bAKIA[0-9A-Z]{16}\b")
_GITHUB_PAT_RE: Final = re.compile(r"\bghp_[A-Za-z0-9]{36}\b")
_SLACK_TOKEN_RE: Final = re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}\b")
_PEM_BLOCK_RE: Final = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_JWT_RE: Final = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")

# Generic high-entropy run of base64-ish characters, 32–80 chars long.
# We deliberately bound the upper end to avoid matching huge documents.
_GENERIC_TOKEN_RE: Final = re.compile(r"\b[A-Za-z0-9+/=_-]{32,80}\b")
_GENERIC_ENTROPY_THRESHOLD: Final = 4.5  # bits/char; random base64 ≈ 5.0
_GENERIC_MAX_PER_DOC: Final = 25  # cap to keep scans bounded

_EMAIL_RE: Final = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE_RE: Final = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_PII_WINDOW: Final = 80  # chars between email and phone to flag co-occurrence


def _shannon_entropy(token: str) -> float:
    """Shannon entropy in bits per character. 0 for empty/single-char tokens."""
    if len(token) < 2:
        return 0.0
    counts: dict[str, int] = {}
    for ch in token:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(token)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _redact_middle(token: str, *, prefix: int = 4, suffix: int = 4) -> str:
    """Return `prefix + '…' + suffix` so the secret value is partially masked."""
    if len(token) <= prefix + suffix + 1:
        return "[redacted]"
    return f"{token[:prefix]}…{token[-suffix:]}"


def _line_of(content: str, offset: int) -> int:
    """1-based line number for a byte offset in `content`."""
    return content.count("\n", 0, offset) + 1


# --------------------------------------------------------------------------- #
# Public API                                                                    #
# --------------------------------------------------------------------------- #


class SensitivityScanner:
    """Stateless scanner over markdown / code-fence bodies.

    Use `scan(content)` to obtain findings. The scanner does not strip code
    blocks before scanning — secrets in code samples are exactly the case we
    want to catch.
    """

    def scan(self, content: str) -> list[SensitivityFinding]:
        out: list[SensitivityFinding] = []
        if not content:
            return out

        # Specific high-confidence patterns first.
        out.extend(self._scan_pattern(content, _AWS_ACCESS_KEY_RE, "aws_access_key", 0.99))
        out.extend(self._scan_pattern(content, _GITHUB_PAT_RE, "github_pat", 0.99))
        out.extend(self._scan_pattern(content, _SLACK_TOKEN_RE, "slack_token", 0.95))
        out.extend(self._scan_pattern(content, _PEM_BLOCK_RE, "pem_private_key", 0.99))
        out.extend(self._scan_pattern(content, _JWT_RE, "jwt_token", 0.9))

        # Generic high-entropy strings (cap to avoid runaway noise).
        seen_offsets = {f.line for f in out}
        emitted = 0
        for m in _GENERIC_TOKEN_RE.finditer(content):
            if emitted >= _GENERIC_MAX_PER_DOC:
                break
            token = m.group(0)
            if _shannon_entropy(token) < _GENERIC_ENTROPY_THRESHOLD:
                continue
            line = _line_of(content, m.start())
            if line in seen_offsets:
                continue  # likely overlaps with a specific match
            out.append(
                SensitivityFinding(
                    kind="generic_high_entropy",
                    line=line,
                    snippet=_redact_middle(token),
                    confidence=0.55,
                )
            )
            emitted += 1

        out.extend(self._scan_pii(content))
        out.sort(key=lambda f: (f.line, f.kind))
        return out

    # -- Helpers ------------------------------------------------------------- #

    def _scan_pattern(
        self,
        content: str,
        pattern: re.Pattern[str],
        kind: str,
        confidence: float,
    ) -> list[SensitivityFinding]:
        out: list[SensitivityFinding] = []
        for m in pattern.finditer(content):
            token = m.group(0)
            out.append(
                SensitivityFinding(
                    kind=kind,
                    line=_line_of(content, m.start()),
                    snippet=_redact_middle(token),
                    confidence=confidence,
                )
            )
        return out

    def _scan_pii(self, content: str) -> list[SensitivityFinding]:
        out: list[SensitivityFinding] = []
        emails = list(_EMAIL_RE.finditer(content))
        phones = list(_PHONE_RE.finditer(content))
        if not emails or not phones:
            return out
        # Flag once per email/phone co-occurrence window. Avoid duplicate
        # findings on the same line.
        flagged_lines: set[int] = set()
        for em in emails:
            for ph in phones:
                if abs(em.start() - ph.start()) > _PII_WINDOW:
                    continue
                line = _line_of(content, min(em.start(), ph.start()))
                if line in flagged_lines:
                    continue
                flagged_lines.add(line)
                out.append(
                    SensitivityFinding(
                        kind="pii_contact",
                        line=line,
                        snippet=f"{em.group(0).split('@')[0]}@…+{_redact_middle(ph.group(0))}",
                        confidence=0.7,
                    )
                )
        return out


# Module-level convenience function (mirrors the dataclass scanner pattern).
def scan(content: str) -> list[SensitivityFinding]:
    """Run the default `SensitivityScanner` over `content`."""
    return SensitivityScanner().scan(content)


# --------------------------------------------------------------------------- #
# Clearance helper (SD-003 prep)                                                #
# --------------------------------------------------------------------------- #


_CLEARANCE_RANK: Final = {
    "public": 0,
    "internal": 1,
    "confidential": 2,
    "restricted": 3,
}


def clearance_meets(actor_clearance: str | None, document_sensitivity: str | None) -> bool:
    """Return True iff `actor_clearance` ≥ `document_sensitivity`.

    Used by future ContextService.get to filter retrieval; exposed as a pure
    helper so write-path validation, redaction, and retrieval all share one
    source of truth. `None` actor_clearance is treated as `public` (most
    restrictive); `None` doc_sensitivity defaults to `internal`.
    """
    actor_rank = _CLEARANCE_RANK.get((actor_clearance or "public").lower(), 0)
    doc_rank = _CLEARANCE_RANK.get((document_sensitivity or "internal").lower(), 1)
    return actor_rank >= doc_rank


__all__ = [
    "SensitivityFinding",
    "SensitivityScanner",
    "clearance_meets",
    "scan",
]
