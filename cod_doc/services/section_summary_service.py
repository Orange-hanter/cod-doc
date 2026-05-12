"""Per-section story summaries — AI-generated, persisted in a JSON sidecar.

The stories page groups user stories into sections (US-1, US-2, …). For
projects with dozens of sections, scrolling raw cards is slow — the summary
is a 2-3 sentence AI-distilled "what is this section about" snippet shown
beside the section header, generated on demand and cached until the section
contents change.

Storage: ``.cod-doc/section_summaries.json`` per project, keyed by section.
Each entry carries a SHA-256 fingerprint of the contributing story IDs so
the UI can flag stale entries when stories are added / removed.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cod_doc.config import Config


@dataclass
class SectionSummary:
    section: str
    text: str
    generated_at: str
    fingerprint: str
    stale: bool = False


def _fingerprint(story_ids: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(story_ids)).encode()).hexdigest()[:16]


def _load_all(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_all(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def load(path: Path, section: str, current_story_ids: list[str]) -> SectionSummary | None:
    """Return the saved summary for ``section`` if it exists.

    ``stale`` is set to True when the set of story IDs in the section has
    changed since the summary was generated — the UI uses that to nudge
    the user to regenerate.
    """
    all_data = _load_all(path)
    entry = all_data.get(section)
    if not entry:
        return None
    cur_fp = _fingerprint(current_story_ids)
    return SectionSummary(
        section=section,
        text=entry.get("text", ""),
        generated_at=entry.get("generated_at", ""),
        fingerprint=entry.get("fingerprint", ""),
        stale=entry.get("fingerprint", "") != cur_fp,
    )


def load_all(path: Path, sections: dict[str, list[str]]) -> dict[str, SectionSummary]:
    """Bulk-load summaries for a set of sections.

    ``sections`` maps section_id → list of story_ids in that section, used
    to compute the stale flag without re-reading the file once per section.
    """
    all_data = _load_all(path)
    result: dict[str, SectionSummary] = {}
    for section, story_ids in sections.items():
        entry = all_data.get(section)
        if not entry:
            continue
        cur_fp = _fingerprint(story_ids)
        result[section] = SectionSummary(
            section=section,
            text=entry.get("text", ""),
            generated_at=entry.get("generated_at", ""),
            fingerprint=entry.get("fingerprint", ""),
            stale=entry.get("fingerprint", "") != cur_fp,
        )
    return result


_SUMMARY_PROMPT = """\
You are a product analyst. The user gives you a numbered set of user stories
from a single section of a project's backlog. Produce a TIGHT 2-3 sentence
summary in the same language as the stories that describes:
- the section's overarching theme (one phrase),
- the dominant persona(s),
- the high-level outcome/goal the stories collectively deliver.

Stories:
{stories_block}

Rules:
- 2-3 sentences total, no bullet points, no headings.
- Same language as the stories (Russian if stories are in Russian).
- No story IDs or quotes — describe collectively.
- Concrete, not generic ("automation of plant device onboarding" beats
  "improvements to a system").
"""


def generate(
    path: Path,
    section: str,
    story_id_to_narrative: dict[str, str],
    cfg: "Config",
) -> SectionSummary:
    """Run AI summary for ``section`` and persist it.

    ``story_id_to_narrative`` provides the narratives for every story in the
    section, ordered by ID. Raises AIBackendError on backend failure.
    """
    from cod_doc.services.ai_text import _call_lite_raw

    if not story_id_to_narrative:
        raise ValueError("Cannot summarize an empty section.")

    lines = [f"{i}. {nar}" for i, (_id, nar) in enumerate(story_id_to_narrative.items(), 1)]
    prompt = _SUMMARY_PROMPT.format(stories_block="\n".join(lines))
    text = _call_lite_raw(prompt, cfg, max_tokens=400).strip()

    # Strip surrounding quotes or markdown bullets the model occasionally adds.
    if text.startswith(("```", "**", "- ")):
        text = text.lstrip("`*- \n").rstrip("` \n")
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()

    fp = _fingerprint(list(story_id_to_narrative.keys()))
    summary = SectionSummary(
        section=section,
        text=text,
        generated_at=datetime.now(UTC).isoformat(),
        fingerprint=fp,
        stale=False,
    )

    all_data = _load_all(path)
    all_data[section] = {
        "text": summary.text,
        "generated_at": summary.generated_at,
        "fingerprint": summary.fingerprint,
    }
    _save_all(path, all_data)
    return summary
