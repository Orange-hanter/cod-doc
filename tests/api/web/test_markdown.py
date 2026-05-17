"""PCA-420: ordered-list rendering in cod_doc.api.web.markdown."""

from __future__ import annotations

from cod_doc.api.web.markdown import render_markdown


class TestOrderedList:
    def test_single_item(self) -> None:
        html = render_markdown("1. Hello")
        assert "<ol>" in html
        assert "<li>Hello</li>" in html

    def test_multi_item_dot(self) -> None:
        src = "1. First\n2. Second\n3. Third"
        html = render_markdown(src)
        assert "<ol>" in html
        assert html.count("<li>") == 3
        assert "<li>First</li>" in html
        assert "<li>Third</li>" in html

    def test_multi_item_paren(self) -> None:
        src = "1) Alpha\n2) Beta"
        html = render_markdown(src)
        assert "<ol>" in html
        assert "<li>Alpha</li>" in html
        assert "<li>Beta</li>" in html

    def test_start_attribute_for_non_one(self) -> None:
        src = "5. Fifth item\n6. Sixth item"
        html = render_markdown(src)
        assert 'start="5"' in html

    def test_start_attribute_absent_for_one(self) -> None:
        src = "1. Item"
        html = render_markdown(src)
        assert 'start=' not in html

    def test_list_split_by_blank_line(self) -> None:
        src = "1. Part one\n\n5. Part two\n6. Part three"
        html = render_markdown(src)
        # Two separate OL elements
        assert html.count("<ol") == 2
        assert 'start="5"' in html

    def test_inline_formatting_inside_item(self) -> None:
        src = "1. **Bold** item"
        html = render_markdown(src)
        assert "<strong>Bold</strong>" in html

    def test_ol_followed_by_ul(self) -> None:
        src = "1. Ordered\n\n- Unordered"
        html = render_markdown(src)
        assert "<ol>" in html
        assert "<ul>" in html

    def test_ul_not_captured_as_ol(self) -> None:
        src = "- bullet one\n- bullet two"
        html = render_markdown(src)
        assert "<ul>" in html
        assert "<ol>" not in html

    def test_two_digit_prefix(self) -> None:
        src = "10. Tenth\n11. Eleventh"
        html = render_markdown(src)
        assert 'start="10"' in html
        assert "<li>Tenth</li>" in html

    def test_plain_text_not_mistaken_for_ol(self) -> None:
        # "123 Main Street" should NOT be an OL item (no dot/paren after digits)
        html = render_markdown("123 Main Street")
        assert "<ol>" not in html
        assert "<p>" in html

    def test_mixed_ol_and_paragraph(self) -> None:
        src = "Intro paragraph.\n\n1. Step one\n2. Step two\n\nClosing."
        html = render_markdown(src)
        assert html.count("<ol>") == 1
        assert html.count("<p>") == 2


# --------------------------------------------------------------------------- #
# autolink_adr_refs: bare `ADR-NNN` in rendered HTML → anchor                   #
# --------------------------------------------------------------------------- #


class TestAutolinkADR:
    def test_bare_token_rewritten(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        html = "<p>See ADR-007 above.</p>"
        out = autolink_adr_refs(html, slug="myp")
        assert '<a class="adr-ref" href="/p/myp/adr/ADR-007">ADR-007</a>' in out

    def test_skips_inside_anchor(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        html = '<p><a href="/x">ADR-007 already linked</a> and bare ADR-008</p>'
        out = autolink_adr_refs(html, slug="p")
        # The already-linked ADR-007 is left alone.
        assert '<a href="/x">ADR-007 already linked</a>' in out
        # The bare ADR-008 is rewritten.
        assert '<a class="adr-ref" href="/p/p/adr/ADR-008">ADR-008</a>' in out

    def test_skips_inside_code(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        html = "<p>Outside: ADR-001 — inside: <code>ADR-002</code></p>"
        out = autolink_adr_refs(html, slug="p")
        assert '<a class="adr-ref" href="/p/p/adr/ADR-001">ADR-001</a>' in out
        # Inside <code> stays unwrapped.
        assert "<code>ADR-002</code>" in out
        assert "/adr/ADR-002" not in out

    def test_skips_inside_pre(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        html = "<pre><code>ADR-003</code></pre><p>Real: ADR-004</p>"
        out = autolink_adr_refs(html, slug="p")
        # <pre> content is untouched.
        assert "<pre><code>ADR-003</code></pre>" in out
        assert '<a class="adr-ref" href="/p/p/adr/ADR-004">ADR-004</a>' in out

    def test_noop_when_no_adr_token(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        html = "<p>Hello world</p>"
        assert autolink_adr_refs(html, slug="x") == html

    def test_full_pipeline_render_then_autolink(self) -> None:
        from cod_doc.api.web.markdown import autolink_adr_refs

        body = "Decided by ADR-005.\n\n`ADR-006` is a fence example."
        html = render_markdown(body)
        out = autolink_adr_refs(html, slug="proj")
        assert '<a class="adr-ref" href="/p/proj/adr/ADR-005">ADR-005</a>' in out
        # ADR-006 is inside <code>, must stay.
        assert "<code>ADR-006</code>" in out
        assert "/adr/ADR-006" not in out
