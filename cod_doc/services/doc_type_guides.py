"""Per-doc-type content templates injected into AI generation prompts.

The main ``_DOC_SYSTEM_PROMPT`` only specifies *length* tiers. These guides add
*semantic* rules — what each document type should and must NOT contain. Without
them the model defaults to a generic technical mishmash (e.g. dropping JSON
schemas into a vision doc).

Add a new type here when introducing it to the catalogue. Missing types fall
back to a generic 'no guidance' branch and the model will produce a serviceable
but undifferentiated doc.
"""

from __future__ import annotations

DOC_TYPE_GUIDES: dict[str, str] = {
    "vision": (
        "VISION documents are STRATEGIC narratives, NOT technical references.\n"
        "Required sections (in this order, may be renamed in source language):\n"
        "  1. Problem statement — what is broken / missing today\n"
        "  2. Target users / audience — who feels this pain\n"
        "  3. Goals — what success looks like at a high level\n"
        "  4. Non-goals — what is explicitly out of scope\n"
        "  5. Success metrics — measurable outcomes (north-star + 2-3 supporting)\n"
        "  6. Strategic positioning — how this differs from alternatives\n"
        "  7. Long-term direction — where this heads in 1-3 years\n"
        "STRICT RULES (violations make this NOT a vision doc):\n"
        "  - NO code blocks, NO JSON, NO API endpoints, NO SQL, NO config snippets.\n"
        "  - NO architecture diagrams or technical sequence flows.\n"
        "  - NO implementation details — stay at the 'why' level.\n"
        "  - Tone: clear, motivating, accessible to non-engineers (a PM, exec, "
        "designer should grasp every sentence)."
    ),
    "architecture": (
        "ARCHITECTURE documents describe HOW the system is structured at the macro level.\n"
        "Recommended sections:\n"
        "  1. Context overview — what surrounds the system (use ```mermaid``` C4-context)\n"
        "  2. Components / modules — purpose, boundaries, ownership\n"
        "  3. Data flows / sequence flows (use ```mermaid sequenceDiagram```)\n"
        "  4. Persistence / data layer — high-level storage choices\n"
        "  5. Technology choices and rationale\n"
        "  6. Deployment topology\n"
        "  7. Scalability / performance considerations\n"
        "  8. Security model — authn/authz, trust boundaries\n"
        "  9. Open questions / known limitations\n"
        "STRICT RULES:\n"
        "  - USE mermaid for component / flow / ER diagrams (≥ 2 diagrams typical).\n"
        "  - Code blocks ONLY for illustrative interface signatures, NOT implementations.\n"
        "  - Tables for component responsibilities and technology comparisons.\n"
        "  - Do NOT drop into module internals — those belong in module-spec."
    ),
    "module-spec": (
        "MODULE-SPEC documents describe a single module in implementation-grade detail.\n"
        "Recommended sections:\n"
        "  1. Purpose and responsibilities (1-2 paragraphs)\n"
        "  2. Public interface — types, classes, functions with full signatures\n"
        "  3. Data model — tables / entities (use markdown tables of fields)\n"
        "  4. Key flows — use ```mermaid sequenceDiagram``` per non-trivial flow\n"
        "  5. Dependencies on other modules (table: module / why)\n"
        "  6. Configuration & environment variables (table: name / type / default / purpose)\n"
        "  7. Error handling and edge cases\n"
        "  8. Testing strategy\n"
        "  9. Migration / versioning notes\n"
        "STRICT RULES:\n"
        "  - INCLUDE concrete code examples (```language fences) for usage / API shapes.\n"
        "  - INCLUDE tables for field lists, error codes, configuration options.\n"
        "  - INCLUDE mermaid for any flow involving ≥ 2 actors or async steps.\n"
        "  - Be specific — every claim should be verifiable against code."
    ),
    "module-subdoc": (
        "MODULE-SUBDOC documents are deep dives into one aspect of a module "
        "(e.g. its caching layer, its retry strategy).\n"
        "Recommended sections:\n"
        "  1. Scope — which slice of the parent module this covers\n"
        "  2. Mechanism / algorithm — narrative + diagram\n"
        "  3. Code walkthrough — annotated snippets\n"
        "  4. Tunables — knobs / configuration\n"
        "  5. Failure modes\n"
        "  6. Benchmarks / measurements (if relevant)\n"
        "STRICT RULES: same as module-spec — be concrete, code-grounded, with diagrams."
    ),
    "guide": (
        "GUIDE documents are practical, task-oriented how-to walkthroughs.\n"
        "Recommended sections:\n"
        "  1. What you will accomplish — one sentence outcome\n"
        "  2. Prerequisites — knowledge, tools, accounts, environment\n"
        "  3. Step-by-step procedure — numbered list, imperative voice\n"
        "  4. Verification — how to confirm it worked\n"
        "  5. Common pitfalls and troubleshooting\n"
        "  6. Next steps / related guides\n"
        "STRICT RULES:\n"
        "  - Numbered lists with imperative verbs ('Run …', 'Open …', 'Add …').\n"
        "  - Code blocks for EVERY command / config snippet — never inline plaintext.\n"
        "  - Avoid theory — refer out to architecture / module-spec for the 'why'.\n"
        "  - One guide = one outcome. If it covers 3 outcomes, propose 3 guides."
    ),
    "standard": (
        "STANDARD documents are normative rule sets that code / processes must follow.\n"
        "Recommended sections:\n"
        "  1. Scope — what code / process this applies to (and what is exempt)\n"
        "  2. Rules — numbered, each in RFC 2119 voice (MUST / SHOULD / MAY)\n"
        "  3. Rationale — short why for each rule, inline\n"
        "  4. Examples — ✅ DO vs ❌ DO NOT, in fenced code blocks\n"
        "  5. Exceptions — when the rule may be waived and the approval path\n"
        "  6. Enforcement — automated (linter / CI rule names) and human review\n"
        "STRICT RULES:\n"
        "  - Every rule starts with MUST / SHOULD / MAY.\n"
        "  - Pair every rule with at least one ✅ and one ❌ code example.\n"
        "  - Use tables for comparison matrices."
    ),
    "adr": (
        "ADR (Architecture Decision Record) — short, structured, immutable once accepted.\n"
        "Required sections in this exact order and naming:\n"
        "  1. Status — proposed | accepted | superseded by ADR-NNNN\n"
        "  2. Context — what forces are at play, what triggered this\n"
        "  3. Decision — what we chose, in one tight paragraph\n"
        "  4. Alternatives considered — 2-4 bullet points with one-line reasons rejected\n"
        "  5. Consequences — split into positive / negative / neutral\n"
        "  6. Related decisions — cross-references\n"
        "STRICT RULES:\n"
        "  - Concise — an ADR is rarely longer than one screen.\n"
        "  - NO implementation code unless it directly clarifies the decision.\n"
        "  - First-person plural voice ('we chose …')."
    ),
    "decision": (
        "DECISION documents capture a single design or product choice.\n"
        "Sections:\n"
        "  1. The question / choice being made\n"
        "  2. Context — why it came up now\n"
        "  3. Options considered — table (option | pro | con | cost)\n"
        "  4. Recommendation — with one-paragraph justification\n"
        "  5. Open questions / follow-ups"
    ),
    "open-question": (
        "OPEN-QUESTION documents track unresolved technical questions.\n"
        "Sections:\n"
        "  1. The question — stated precisely, ideally as a yes/no or A-vs-B form\n"
        "  2. Why it matters — concrete impact if unresolved\n"
        "  3. Known constraints — what we cannot do\n"
        "  4. Options under consideration\n"
        "  5. Owner + target resolution date"
    ),
    "execution-plan": (
        "EXECUTION-PLAN documents describe how a multi-task initiative will ship.\n"
        "Sections:\n"
        "  1. Goal — what 'done' looks like\n"
        "  2. Scope — in / out\n"
        "  3. Milestones — table with target dates and exit criteria\n"
        "  4. Task breakdown by milestone — numbered or table form\n"
        "  5. Dependencies (other teams, external)\n"
        "  6. Risks and mitigations\n"
        "  7. Success metrics\n"
        "STRICT RULES:\n"
        "  - Use tables for task lists with status / owner / estimate columns.\n"
        "  - Dates in ISO format (YYYY-MM-DD).\n"
        "  - Reference existing task IDs where applicable."
    ),
    "user-story": (
        "USER-STORY documents capture a single user-facing requirement.\n"
        "Sections:\n"
        "  1. Story — 'As a <role>, I want <action>, so that <value>'\n"
        "  2. Acceptance criteria — bulleted, testable\n"
        "  3. Out of scope (what this story explicitly does NOT cover)\n"
        "  4. UX notes / screens (textual)\n"
        "  5. Edge cases\n"
        "STRICT RULES: keep it human-language; avoid implementation talk."
    ),
    "task-section": (
        "TASK-SECTION documents collect a coherent group of implementation tasks.\n"
        "Use numbered task entries, each with: title, description, acceptance, deps."
    ),
    "execution-log": (
        "EXECUTION-LOG documents are append-only journals of what shipped when.\n"
        "Sections:\n"
        "  1. Chronological entries (use ### YYYY-MM-DD headers)\n"
        "  2. Each entry: what changed, why, who, links to PRs / commits / runs\n"
        "STRICT RULES: do NOT edit prior entries when adding new ones."
    ),
    "redirect": (
        "REDIRECT documents are stubs pointing at the canonical home of a topic.\n"
        "Body must be ≤ 3 sentences: 'See <doc_key> for ...'. No other content."
    ),
}


def guide_for(doc_type: str) -> str:
    """Return the per-type content guide, or ``""`` for unknown types."""
    return DOC_TYPE_GUIDES.get(doc_type, "")
