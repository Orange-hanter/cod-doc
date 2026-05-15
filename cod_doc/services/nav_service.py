"""Documentation Navigator: journey map + AI gap analysis with persistent cache.

The journey defines a recommended sequence for building project documentation
from scratch (Vision → Architecture → Data Model → Modules → Guides → Standards).
The AI analysis is cached to a JSON file so reasoning persists across sessions
and is reused while the doc set has not changed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from cod_doc.services import doc_service as docs

if TYPE_CHECKING:
    from cod_doc.config import Config


_JOURNEY: list[dict] = [
    {
        "id": "vision",
        "icon": "🎯",
        "label": "Vision",
        "description": "Why this project exists, its goals and audience",
        "types": ["vision"],
        "patterns": ["vision"],
        "generate_hint": "Project vision — purpose, goals, target audience, key constraints",
    },
    {
        "id": "architecture",
        "icon": "🏗",
        "label": "Architecture",
        "description": "System structure, components, and design decisions",
        "types": ["architecture"],
        "patterns": ["arch", "architecture"],
        "generate_hint": "System architecture overview — components, interactions, technology choices",
    },
    {
        "id": "data-model",
        "icon": "📊",
        "label": "Data Model",
        "description": "Domain entities, schemas, and data relationships",
        "types": ["module-spec"],
        "patterns": ["data", "domain", "entities", "schema", "model"],
        "generate_hint": "Data model — domain entities, relationships, key fields and invariants",
    },
    {
        "id": "modules",
        "icon": "📦",
        "label": "Modules",
        "description": "How each component or module works internally",
        "types": ["module-spec"],
        "patterns": ["modules/", "module/", "services/", "components/"],
        "generate_hint": "Module specification — responsibilities, interfaces, dependencies, key flows",
    },
    {
        "id": "guides",
        "icon": "📖",
        "label": "Guides",
        "description": "How-to guides and onboarding for developers",
        "types": ["guide"],
        "patterns": ["guide", "guides/", "quickstart", "onboarding", "tutorial", "howto"],
        "generate_hint": "Developer guide — step-by-step setup and common workflows",
    },
    {
        "id": "standards",
        "icon": "📏",
        "label": "Standards",
        "description": "Coding conventions, ADRs, and process standards",
        "types": ["standard", "adr"],
        "patterns": ["standard", "standards/", "adr", "convention", "decision"],
        "generate_hint": "Standards and conventions — coding style, process rules, key decisions",
    },
]

# Step IDs for the data-model exclusion guard
_DATA_MODEL_PATTERNS = set(_JOURNEY[2]["patterns"])


@dataclass
class JourneyStep:
    id: str
    icon: str
    label: str
    description: str
    generate_hint: str
    status: str  # "complete" | "partial" | "next" | "missing"
    count: int
    doc_keys: list[str]
    step_num: int


@dataclass
class NavGap:
    type: str
    suggested_title: str
    reason: str
    priority: int
    suggested_sources: list[str]


@dataclass
class NavAnalysis:
    summary: str
    gaps: list[NavGap]
    next_title: str
    next_type: str
    next_intent: str
    next_sources: list[str]
    strengths: list[str]
    cached: bool
    analyzed_at: str
    error: str = ""


def fmt_relative(iso_ts: str) -> str:
    """Render an ISO-8601 UTC timestamp as a short relative phrase.

    Examples: ``just now``, ``5 min ago``, ``2 hours ago``, ``3 days ago``.
    Falls back to the raw date when the value cannot be parsed.
    """
    if not iso_ts:
        return ""
    try:
        ts = datetime.fromisoformat(iso_ts).astimezone(UTC)
    except (ValueError, TypeError):
        return iso_ts
    delta = (datetime.now(UTC) - ts).total_seconds()
    if delta < 60:
        return "just now"
    if delta < 3600:
        m = int(delta // 60)
        return f"{m} min ago"
    if delta < 86400:
        h = int(delta // 3600)
        return f"{h} hour{'s' if h > 1 else ''} ago"
    d = int(delta // 86400)
    if d < 30:
        return f"{d} day{'s' if d > 1 else ''} ago"
    return ts.strftime("%Y-%m-%d")


def _fingerprint(doc_list: list) -> str:
    keys = sorted(d.doc_key for d in doc_list)
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


def compute_journey(session: Session, project_id: int) -> list[JourneyStep]:
    """Build the journey step list based on which doc types are present."""
    all_docs = docs.list_for_project(session, project_id)
    steps: list[JourneyStep] = []
    next_assigned = False

    for i, step in enumerate(_JOURNEY, 1):
        matched: list[str] = []
        for doc in all_docs:
            type_ok = doc.type.value in step["types"]
            pat_ok = any(p in doc.doc_key.lower() for p in step["patterns"])
            if type_ok or pat_ok:
                # modules step: exclude docs that belong to data-model step
                if step["id"] == "modules" and any(
                    p in doc.doc_key.lower() for p in _DATA_MODEL_PATTERNS
                ):
                    continue
                matched.append(doc.doc_key)

        count = len(matched)
        if count >= 2:
            status = "complete"
        elif count == 1:
            status = "partial"
        elif not next_assigned:
            status = "next"
            next_assigned = True
        else:
            status = "missing"

        steps.append(
            JourneyStep(
                id=step["id"],
                icon=step["icon"],
                label=step["label"],
                description=step["description"],
                generate_hint=step["generate_hint"],
                status=status,
                count=count,
                doc_keys=matched[:6],
                step_num=i,
            )
        )

    return steps


# ── Cache helpers ──────────────────────────────────────────────────────────


def _load_cache(cache_path: Path, fp: str) -> NavAnalysis | None:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text())
        if data.get("fingerprint") != fp:
            return None
        created = datetime.fromisoformat(data["created_at"])
        age_h = (datetime.now(UTC) - created.astimezone(UTC)).total_seconds() / 3600
        if age_h > 24:
            return None
        a = data["analysis"]
        return NavAnalysis(
            summary=a["summary"],
            gaps=[
                NavGap(
                    type=g["type"],
                    suggested_title=g["suggested_title"],
                    reason=g["reason"],
                    priority=g["priority"],
                    suggested_sources=g["suggested_sources"],
                )
                for g in a["gaps"]
            ],
            next_title=a["next_title"],
            next_type=a["next_type"],
            next_intent=a["next_intent"],
            next_sources=a["next_sources"],
            strengths=a["strengths"],
            cached=True,
            analyzed_at=a["analyzed_at"],
        )
    except Exception:
        return None


def _save_cache(cache_path: Path, fp: str, analysis: NavAnalysis) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fingerprint": fp,
        "created_at": datetime.now(UTC).isoformat(),
        "analysis": {
            "summary": analysis.summary,
            "gaps": [
                {
                    "type": g.type,
                    "suggested_title": g.suggested_title,
                    "reason": g.reason,
                    "priority": g.priority,
                    "suggested_sources": g.suggested_sources,
                }
                for g in analysis.gaps
            ],
            "next_title": analysis.next_title,
            "next_type": analysis.next_type,
            "next_intent": analysis.next_intent,
            "next_sources": analysis.next_sources,
            "strengths": analysis.strengths,
            "analyzed_at": analysis.analyzed_at,
        },
    }
    cache_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


# ── AI analysis ────────────────────────────────────────────────────────────

_GAP_PROMPT_TEMPLATE = """\
You are a documentation advisor for a software project.

Current documentation inventory:
{inventory}

Analyze coverage. Return ONLY a valid JSON object — no markdown fences, no explanation:
{{
  "summary": "2-3 sentence assessment of current coverage and overall completeness",
  "gaps": [
    {{
      "type": "one of: vision|architecture|module-spec|guide|standard|adr",
      "suggested_title": "concrete document title",
      "reason": "why this is needed (1 sentence)",
      "priority": 1,
      "suggested_sources": ["existing_doc_key"]
    }}
  ],
  "next_title": "title of the single most important document to create next",
  "next_type": "doc type (vision|architecture|module-spec|guide|standard|adr)",
  "next_intent": "1-2 sentences describing what the AI generator should produce",
  "next_sources": ["doc_key1", "doc_key2"],
  "strengths": ["what is already well covered (short phrase)"]
}}

priority: 1=must-have, 2=important, 3=nice-to-have. Return max 5 gaps sorted by priority."""


def peek_cached_analysis(
    session: Session,
    project_id: int,
    cache_path: Path,
) -> NavAnalysis | None:
    """Return cached analysis if the fingerprint matches and TTL is fresh.

    Never triggers an AI call. Used by the navigator GET route to render the
    analysis inline (eliminating the HTMX round-trip + spinner on repeat visits).
    """
    all_docs = docs.list_for_project(session, project_id)
    fp = _fingerprint(all_docs)
    return _load_cache(cache_path, fp)


def analyze_gaps(
    session: Session,
    project_id: int,
    cfg: Config,
    cache_path: Path,
    *,
    force: bool = False,
) -> NavAnalysis:
    """Run AI gap analysis with persistent per-project JSON cache.

    Returns cached result when the doc fingerprint matches and cache is < 24h old.
    Set ``force=True`` to bypass the cache and re-run the analysis.
    """
    all_docs = docs.list_for_project(session, project_id)
    fp = _fingerprint(all_docs)

    if not force:
        cached = _load_cache(cache_path, fp)
        if cached:
            return cached

    # Build inventory for the prompt
    lines = []
    for doc in all_docs:
        preamble = (doc.preamble or "")[:120].replace("\n", " ")
        lines.append(f"- [{doc.type.value}] {doc.doc_key}: {doc.title} — {preamble}")
    inventory = "\n".join(lines) if lines else "(empty — no documents yet)"

    from cod_doc.services.ai_text import AIBackendError, _call_lite_raw

    prompt = _GAP_PROMPT_TEMPLATE.format(inventory=inventory)

    try:
        raw = _call_lite_raw(prompt, cfg, max_tokens=1200)
        if raw.startswith("```"):
            raw_lines = raw.splitlines()
            raw = "\n".join(
                raw_lines[1:-1] if raw_lines[-1].strip() == "```" else raw_lines[1:]
            )
        data = json.loads(raw)
        analysis = NavAnalysis(
            summary=data.get("summary", ""),
            gaps=[
                NavGap(
                    type=g.get("type", ""),
                    suggested_title=g.get("suggested_title", ""),
                    reason=g.get("reason", ""),
                    priority=int(g.get("priority", 2)),
                    suggested_sources=list(g.get("suggested_sources", [])),
                )
                for g in data.get("gaps", [])[:5]
            ],
            next_title=str(data.get("next_title", "")),
            next_type=str(data.get("next_type", "guide")),
            next_intent=str(data.get("next_intent", "")),
            next_sources=list(data.get("next_sources", [])),
            strengths=list(data.get("strengths", [])),
            cached=False,
            analyzed_at=datetime.now(UTC).isoformat(),
        )
    except (AIBackendError, json.JSONDecodeError, Exception) as exc:
        analysis = NavAnalysis(
            summary="",
            gaps=[],
            next_title="",
            next_type="guide",
            next_intent="",
            next_sources=[],
            strengths=[],
            cached=False,
            analyzed_at=datetime.now(UTC).isoformat(),
            error=str(exc),
        )

    _save_cache(cache_path, fp, analysis)
    return analysis
