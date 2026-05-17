"""COD-013 / RFL-071: parser block of LinkService.

These tests exercise the pure regex `parse()` function — no DB,
no fixtures. Kept apart from the resolver / cascade tests so a
parser-only failure isn't buried under the much larger DB suite.
"""

from __future__ import annotations

from cod_doc.domain.entities import LinkKind
from cod_doc.services import link_service as links


def test_parse_canonical_doc_ref() -> None:
    body = "See [[doc:modules/M1-auth/overview]] for details."
    parsed = links.parse(body)

    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.CANONICAL
    assert p.raw == "[[doc:modules/M1-auth/overview]]"
    assert p.target_doc_key == "modules/M1-auth/overview"
    assert p.anchor is None


def test_parse_canonical_section_ref() -> None:
    body = "See [[doc:modules/M1-auth/overview#data-model]]."
    parsed = links.parse(body)

    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.SECTION
    assert p.target_doc_key == "modules/M1-auth/overview"
    assert p.anchor == "data-model"


def test_parse_task_ref() -> None:
    parsed = links.parse("Blocked by [[task:AUTH-025]] until release.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.TASK
    assert parsed[0].target_task_id == "AUTH-025"


def test_parse_story_ref() -> None:
    parsed = links.parse("Implements [[story:US-014]].")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.STORY
    assert parsed[0].target_story_id == "US-014"


def test_parse_wiki_link() -> None:
    """Plain `[[Some Title]]` form, no `kind:` prefix."""
    parsed = links.parse("Reference: [[M1 AUTH v2]].")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.WIKI
    assert parsed[0].raw == "[[M1 AUTH v2]]"
    # Wiki targets resolve later — parser captures the raw label only.
    assert parsed[0].target_doc_key is None


def test_parse_markdown_relative() -> None:
    parsed = links.parse("Read [Auth Overview](../modules/M1-auth/overview.md).")
    assert len(parsed) == 1
    p = parsed[0]
    assert p.kind is LinkKind.MARKDOWN
    assert p.raw == "[Auth Overview](../modules/M1-auth/overview.md)"
    # doc_key derived by stripping leading ../ and trailing .md
    assert p.target_doc_key == "modules/M1-auth/overview"


def test_parse_markdown_with_anchor() -> None:
    parsed = links.parse("See [data model](../modules/M1-auth/overview.md#data-model).")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.SECTION
    assert parsed[0].target_doc_key == "modules/M1-auth/overview"
    assert parsed[0].anchor == "data-model"


def test_parse_url() -> None:
    parsed = links.parse("Tracking issue: https://github.com/x/y/issues/1.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.URL
    assert parsed[0].raw.startswith("https://github.com")


def test_parse_markdown_url_combines_to_url_kind() -> None:
    parsed = links.parse("See [GitHub](https://github.com/owner/repo).")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.URL
    assert "github.com/owner/repo" in parsed[0].raw


def test_parse_multiple_links_in_order() -> None:
    body = "See [[doc:a]], then [[task:T-001]] and [[doc:b#sec]]. External: https://example.com/x."
    parsed = links.parse(body)
    assert [p.kind for p in parsed] == [
        LinkKind.CANONICAL,
        LinkKind.TASK,
        LinkKind.SECTION,
        LinkKind.URL,
    ]
    assert [p.target_doc_key for p in parsed[:3]] == ["a", None, "b"]
    assert parsed[2].anchor == "sec"


def test_parse_skips_code_blocks() -> None:
    """Links inside fenced code blocks are not parsed."""
    body = (
        "Real link [[doc:real]].\n\n"
        "```python\n"
        "code = '[[doc:fake]]'\n"
        "```\n\n"
        "After code: [[task:TST-002]]."
    )
    parsed = links.parse(body)
    keys = [p.target_doc_key for p in parsed]
    task_ids = [p.target_task_id for p in parsed]
    assert "fake" not in keys
    assert "real" in keys
    assert "TST-002" in task_ids


def test_parse_adr_wiki_explicit() -> None:
    parsed = links.parse("See [[adr:ADR-007]] for rationale.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.ADR
    assert parsed[0].target_adr_id == "ADR-007"


def test_parse_adr_wiki_bare() -> None:
    """A wiki-form `[[ADR-NNN]]` (without the `adr:` prefix) is also recognized."""
    parsed = links.parse("Refer to [[ADR-007]] above.")
    assert len(parsed) == 1
    assert parsed[0].kind is LinkKind.ADR
    assert parsed[0].target_adr_id == "ADR-007"


def test_parse_adr_bare_token() -> None:
    """Plain prose `ADR-007` outside code/links is autodetected as ADR ref."""
    parsed = links.parse("This is governed by ADR-007 since April.")
    adr = [p for p in parsed if p.kind is LinkKind.ADR]
    assert len(adr) == 1
    assert adr[0].target_adr_id == "ADR-007"
    assert adr[0].raw == "ADR-007"


def test_parse_adr_bare_skips_inside_code_fence() -> None:
    """`ADR-007` inside a fenced code block must NOT be auto-linked."""
    body = "Real ADR-001 here.\n\n```\nADR-002 in code\n```\n"
    parsed = links.parse(body)
    adr_ids = [p.target_adr_id for p in parsed if p.kind is LinkKind.ADR]
    assert adr_ids == ["ADR-001"]


def test_parse_adr_does_not_double_count_in_wiki_form() -> None:
    """`[[ADR-007]]` should produce ONE ParsedLink, not also a bare-token one."""
    parsed = links.parse("[[ADR-007]] is supreme.")
    adrs = [p for p in parsed if p.kind is LinkKind.ADR]
    assert len(adrs) == 1
