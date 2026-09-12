---
type: standard
scope: sensitive-data
status: draft
source_of_truth: true
owner: cod-doc core
created: 2026-04-19
last_updated: 2026-04-19
related_docs:
  - ../audit/2026-04-19-initial-audit.md
---

# Sensitive Data Standard

> Documents may contain PII, secrets, business-sensitive information.
> The standard limits what to give to whom, and how to mark it.

## 1. The `sensitivity` field in Document

```yaml
sensitivity: public | internal | confidential | restricted
```

| Level | What it means |
|---------|-----------|
| `public` | Can be published externally (open-source, landing page) |
| `internal` | Inside the organization; default |
| `confidential` | Only the listed `audience` |
| `restricted` | Access only by an explicit `actor allow-list` |

## 2. Forbidden content

The following are forbidden in document bodies (at any level):

- API keys, tokens, passwords in plaintext.
- Full PII (full names + contacts + addresses) of clients in large lists.
- DB dumps exceeding 1000 rows.

Detection — the `SensitivityScanner` service (regex + entropy for secrets; a sample-check for PII).

## 3. Behavior of `context.get`

- If `actor=mcp:<external>` — sections with `sensitivity ≥ confidential` are not returned; a "redacted" marker is left.
- If `actor=agent:<role>` — `agent_definition.sensitivity_clearance` is checked (a new field).

## 4. Behavior of `export`

- A markdown projection with `sensitivity ≥ confidential` is marked in the frontmatter and is **not** exported to the public CHANGELOG.
- On `cod-doc projection freeze`, confidential sections are rendered as stubs `> [content redacted: confidential — see DB]` for the public copy (optional).

## 5. Audit-checks

`cod-doc audit --sensitivity`:

- A document without a `sensitivity` field → warning (default `internal`).
- Secret patterns found in `public`/`internal` → error.
- A document with `sensitivity=public` references a confidential one → warning.

## 6. Out of scope

- DB encryption at-rest — an infrastructure concern, not a package one.
- Compliance (GDPR, SOC2) — requires a separate audit; this standard provides building blocks, not certification.
