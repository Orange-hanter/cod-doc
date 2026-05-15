"""COD-067: small LLM helper for "improve this text" UI actions.

Stays intentionally minimal — one prompt, one return value. Larger AI flows
(task generation, story extraction) live in dedicated services.

Tests monkey-patch ``improve_text`` directly rather than mocking the OpenAI
client, so the prompt text in this module is free to evolve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cod_doc.config import Config


class AIBackendError(RuntimeError):
    """The LLM backend is unavailable or returned an error."""


@dataclass
class ImproveResult:
    """Improved text plus the metering info needed to record a trace row."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_ms: int = 0
    tool_calls: list[dict[str, Any]] | None = field(default=None)


_SYSTEM_PROMPT = (
    "You are a senior technical-documentation editor. The user gives you a "
    "passage of project documentation and a short intent describing how it "
    "should be improved. Rewrite the passage according to that intent.\n\n"
    "Rules:\n"
    "- Preserve markdown formatting (lists, fences, headings, links).\n"
    "- Preserve the original language of the passage (do not translate).\n"
    "- Return ONLY the improved text — no explanation, no apology, no preface.\n"
    "- If the intent is empty, default to: tighten wording, fix grammar, "
    "keep the meaning."
)


def improve_text(text: str, intent: str, *, cfg: Config) -> str:
    """Backwards-compatible wrapper — return only the improved text."""
    return improve_text_traced(text, intent, cfg=cfg).text


def improve_text_traced(text: str, intent: str, *, cfg: Config) -> ImproveResult:
    """Run the improve pass and return the suggestion together with usage stats.

    The route uses this when it wants to emit a trace row. The token counters
    fall back to 0 when the provider does not return usage data.

    Raises ``AIBackendError`` on any backend failure or when no API key is
    configured. The caller is expected to surface the message to the user.
    """
    import time

    if not cfg.api_key:
        raise AIBackendError(
            "LLM backend not configured: set the API key in /settings."
        )
    if not text.strip():
        raise AIBackendError("Nothing to improve — text is empty.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    user_msg = f"Intent: {intent.strip() or '(default)'}\n\nText:\n{text}"

    started = time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=cfg.model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=cfg.max_tokens,
        )
    except Exception as exc:  # openai.OpenAIError, network errors, etc.
        raise AIBackendError(f"LLM call failed: {exc}") from exc
    duration_ms = int((time.monotonic() - started) * 1000)

    content = (completion.choices[0].message.content or "").strip()
    if not content:
        raise AIBackendError("LLM returned empty content.")

    usage = getattr(completion, "usage", None)
    return ImproveResult(
        text=content,
        model=cfg.model,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        duration_ms=duration_ms,
    )


# ── Doc meta suggestion ────────────────────────────────────────────────────

from dataclasses import dataclass


@dataclass
class DocMetaSuggestion:
    title: str
    doc_key: str
    type: str
    preamble: str


_DOC_SUGGEST_SYSTEM = """\
You are a documentation assistant. The user gives you a free-form description \
of a document they want to create (possibly in Russian or English). \
Infer the document metadata and return ONLY a JSON object — no other text.

JSON shape:
{
  "title": "<concise, professional document title>",
  "doc_key": "<kebab-slash path, e.g. guides/onboarding or api/auth-tokens>",
  "type": "<one of: module-spec|guide|architecture|vision|standard|execution-plan|decision|open-question>",
  "preamble": "<1-3 sentence summary of the document purpose, in the same language as the input>"
}

Rules:
- doc_key: lowercase, kebab, slashes allowed, no spaces, no file extension.
- type: pick the single best fit.
- title: in the same language as the user input.
- preamble: clean up and expand the user description into a proper one-paragraph summary.
- Respond ONLY with the JSON object, nothing else."""


def suggest_doc_meta(description: str, *, cfg: Config) -> DocMetaSuggestion:
    """Call lite model to infer doc title, doc_key, type, and preamble from a free-form description.

    Uses ``cfg.lite_model`` when set, falls back to ``cfg.model``.
    Raises ``AIBackendError`` on failure.
    """
    import json as _json
    import time as _time

    if not cfg.api_key:
        raise AIBackendError("LLM backend not configured: set the API key in /settings.")
    if not description.strip():
        raise AIBackendError("Description is empty.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    model = cfg.lite_model.strip() if cfg.lite_model and cfg.lite_model.strip() else cfg.model
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    started = _time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _DOC_SUGGEST_SYSTEM},
                {"role": "user", "content": description.strip()},
            ],
            max_tokens=512,
            temperature=0.3,
        )
    except Exception as exc:
        raise AIBackendError(f"LLM call failed: {exc}") from exc

    raw = (completion.choices[0].message.content or "").strip()
    if not raw:
        raise AIBackendError("LLM returned empty response.")

    # Strip markdown fences if model wraps JSON in ```json ... ```
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise AIBackendError(f"LLM returned invalid JSON: {exc}. Raw: {raw[:200]}") from exc

    return DocMetaSuggestion(
        title=str(data.get("title", "")).strip(),
        doc_key=str(data.get("doc_key", "")).strip().lower().replace(" ", "-"),
        type=str(data.get("type", "module-spec")).strip(),
        preamble=str(data.get("preamble", "")).strip(),
    )


# ── Doc section expansion ──────────────────────────────────────────────────

_DOC_EXPAND_SYSTEM = """\
You are a senior technical documentation author. The user gives you:
- A document preamble (summary of what the document is about)
- An optional doc_type hint (architecture | module-spec | guide | vision | standard | ...)
- An optional intent (angle / focus to expand from)

Generate a thorough document body that logically expands the preamble.

Reply with ONLY a JSON object, no other text:
{
  "sections": [
    {"heading": "<section heading>", "body": "<section body in markdown>"},
    ...
  ]
}

Length and depth by doc_type:
- architecture / module-spec / module-subdoc: 6-12 sections, each body 400-900 words. Include data models, sequence flows (use ```mermaid``` fenced diagrams), code examples, tables of fields/responsibilities, edge cases, concrete API/interface signatures.
- vision / guide / execution-plan: 5-8 sections, each body 250-500 words. Use concrete steps, tables, examples.
- standard / decision / open-question / other: 3-6 sections, each body 150-350 words.

Formatting:
- Use ### for subsections inside a section body.
- Use ```mermaid fences for diagrams in architecture docs.
- Use tables for field lists, response shapes, comparison matrices.
- Use ```language fenced code blocks for examples.

Rules:
- heading: short, clear, professional (same language as preamble).
- Sections must be coherent, non-repetitive, and build on the preamble.
- Do NOT add a section that restates the preamble; start from where it leaves off.
- Do NOT pad with filler — every paragraph must carry concrete information.
- When 'Type-specific content rules' appear in the user message, follow them STRICTLY — they define what content belongs in this doc type and what must NOT appear (e.g. a vision doc must contain no code/API/JSON).

Cross-doc references (MANDATORY):
- When referencing another document, use markdown link syntax: [label](doc/key) — never plain backticks. The system parses these to register real cross-doc links.

Mermaid diagrams (architecture / module-spec only):
- Each statement on its OWN LINE inside the ```mermaid fence. Never concatenate node / edge declarations with spaces. mermaid.js refuses to render a single-line graph.
- Correct example:
  ```mermaid
  graph TD
    User["User"] --> App["App"]
    App --> DB[(Database)]
  ```

- Respond ONLY with the JSON object."""


@dataclass
class SectionDraft:
    heading: str
    body: str


def expand_doc_sections(
    preamble: str,
    *,
    intent: str = "",
    cfg: Config,
    doc_type: str = "",
) -> list[SectionDraft]:
    """Ask the model to generate sections that expand a document preamble.

    ``doc_type`` (when supplied) selects the per-type token budget from the
    Config and is forwarded to the prompt so the model picks the right
    depth tier (architecture/module-spec get the heavy budget).

    Raises ``AIBackendError`` on failure.
    """
    import json as _json
    import time as _time

    if not cfg.api_key:
        raise AIBackendError("LLM backend not configured: set the API key in /settings.")
    if not preamble.strip():
        raise AIBackendError("Preamble is empty — add a description first.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    # Heavy doc types use the full model + token budget; smaller docs can use the lite model.
    is_heavy = doc_type in cfg._HEAVY_DOC_TYPES
    model = cfg.model if is_heavy else (
        cfg.lite_model.strip() if cfg.lite_model and cfg.lite_model.strip() else cfg.model
    )
    budget = cfg.doc_token_budget(doc_type) if doc_type else cfg.doc_max_tokens_default
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    from cod_doc.services.doc_type_guides import guide_for

    user_msg = f"Preamble:\n{preamble.strip()}"
    if doc_type:
        user_msg += f"\n\ndoc_type: {doc_type}"
        type_guide = guide_for(doc_type)
        if type_guide:
            user_msg += (
                f"\n\n=== Type-specific content rules for '{doc_type}' "
                f"(these OVERRIDE generic length guidance when in conflict) ===\n"
                f"{type_guide}"
            )
    if intent.strip():
        user_msg += f"\n\nIntent: {intent.strip()}"

    _t = _time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _DOC_EXPAND_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            max_tokens=budget,
            temperature=0.4,
        )
    except Exception as exc:
        raise AIBackendError(f"LLM call failed: {exc}") from exc

    raw = (completion.choices[0].message.content or "").strip()
    if not raw:
        raise AIBackendError("LLM returned empty response.")
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        data = _json.loads(raw)
    except _json.JSONDecodeError as exc:
        raise AIBackendError(f"LLM returned invalid JSON: {exc}") from exc

    return [
        SectionDraft(
            heading=str(s.get("heading", "")).strip(),
            body=str(s.get("body", "")).strip(),
        )
        for s in data.get("sections", [])
        if s.get("heading")
    ]


# ── Low-level lite-model helper ────────────────────────────────────────────


def _call_lite_raw(prompt: str, cfg: Config, *, max_tokens: int = 1024) -> str:
    """Call the lite model with a single user message and return the raw string.

    Uses ``cfg.lite_model`` when set, falls back to ``cfg.model``.
    Raises ``AIBackendError`` on any failure.
    """
    import time as _time

    if not cfg.api_key:
        raise AIBackendError("LLM backend not configured: set the API key in /settings.")

    try:
        from openai import OpenAI
    except ImportError as exc:
        raise AIBackendError("openai package is not installed.") from exc

    model = cfg.lite_model.strip() if cfg.lite_model and cfg.lite_model.strip() else cfg.model
    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)

    _t = _time.monotonic()
    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=0.3,
        )
    except Exception as exc:
        raise AIBackendError(f"LLM call failed: {exc}") from exc

    raw = (completion.choices[0].message.content or "").strip()
    if not raw:
        raise AIBackendError("LLM returned empty response.")
    return raw
