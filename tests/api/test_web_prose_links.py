"""ADO-142: живые ссылки в прозе — что становится ссылкой, а что остаётся текстом."""

from __future__ import annotations

from typing import TYPE_CHECKING

from cod_doc.api.web.markdown import render_inline, render_markdown
from cod_doc.api.web.prose_links import ProseLinks, _on_disk, collect
from cod_doc.domain.entities import EntityKind

if TYPE_CHECKING:
    from pathlib import Path

REPO = "https://github.com/o/r"


def _links(**kw) -> ProseLinks:  # type: ignore[no-untyped-def]
    return ProseLinks(
        slug="demo",
        repo_url=kw.get("repo_url", REPO),
        entities=kw.get(
            "entities",
            {"ADO-012": EntityKind.TASK, "US-004": EntityKind.STORY, "ADR-007": EntityKind.ADR},
        ),
        paths=frozenset(kw.get("paths", {"server/dataQuality.ts", "README.md"})),
    )


def test_known_entities_link_to_their_pages() -> None:
    html = _links().apply("<p>См. ADO-012, US-004 и ADR-007.</p>")
    assert '<a class="ref-link ref-task" href="/p/demo/tasks/ADO-012" data-peek>ADO-012</a>' in html
    assert 'href="/p/demo/stories/US-004">US-004</a>' in html
    assert 'href="/p/demo/adr/ADR-007">ADR-007</a>' in html
    # Шторкой открывается только задача.
    assert html.count("data-peek") == 1


def test_unknown_entity_stays_text() -> None:
    assert _links().apply("<p>ADO-999 и UTF-8</p>") == "<p>ADO-999 и UTF-8</p>"


def test_issue_number_links_only_with_repo_url() -> None:
    html = _links().apply("<p>Смёржено в #257.</p>")
    assert f'href="{REPO}/issues/257"' in html
    assert ">#257</a>." in html
    assert _links(repo_url=None).apply("<p>#257</p>") == "<p>#257</p>"


def test_html_entity_is_not_an_issue_number() -> None:
    # render_markdown экранирует апостроф в `&#x27;` / `&#39;`.
    src = "<p>it&#39;s &#x27;x&#x27; a#1 #1a</p>"
    assert _links().apply(src) == src


def test_url_drops_trailing_punctuation_and_quotes() -> None:
    html = _links().apply("<p>(см. https://example.com/a?b=1).</p>")
    assert '<a href="https://example.com/a?b=1" target="_blank" rel="noopener">' in html
    assert "</a>)." in html
    quoted = _links().apply("<p>&quot;https://example.com/x&quot;</p>")
    assert 'href="https://example.com/x"' in quoted
    assert "&quot;</p>" in quoted


def test_existing_path_links_to_repo_and_invented_stays_text() -> None:
    html = _links().apply("<p>Правка в server/dataQuality.ts и server/missing.ts</p>")
    assert f'href="{REPO}/blob/HEAD/server/dataQuality.ts"' in html
    assert "server/missing.ts</p>" in html
    assert "missing.ts</a>" not in html


def test_path_without_repo_url_stays_text() -> None:
    assert _links(repo_url=None).apply("<p>README.md</p>") == "<p>README.md</p>"


def test_path_wins_over_entity_inside_it() -> None:
    html = _links(paths={"docs/ADO-012.md"}).apply("<p>docs/ADO-012.md</p>")
    assert f'href="{REPO}/blob/HEAD/docs/ADO-012.md"' in html
    assert "/tasks/ADO-012" not in html


def test_code_links_and_attributes_are_untouched() -> None:
    src = (
        '<p><code>ADO-012 #1</code> <a href="/x" title="ADO-012">ADO-012</a></p>'
        "<pre><code>README.md</code></pre>"
    )
    assert _links().apply(src) == src


def test_inline_markdown_is_rewritten_after_rendering() -> None:
    links = _links()
    html = links.apply(render_markdown("**ADO-012** закрыта в #3, `ADO-012` — код"))
    assert '<strong><a class="ref-link ref-task"' in html
    assert f'href="{REPO}/issues/3"' in html
    assert "<code>ADO-012</code>" in html
    assert links.apply(render_inline("US-004")).endswith("US-004</a>")


def test_collect_finds_candidates_in_raw_text() -> None:
    ids, paths = collect(["ADO-012 в server/a.ts", None, "README.md, US-4"])
    assert ids == {"ADO-012", "US-4"}
    assert paths == {"server/a.ts", "README.md"}


def test_on_disk_checks_files_under_root_only(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("x", encoding="utf-8")
    (root / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "secret.md").write_text("x", encoding="utf-8")
    found = _on_disk(root, {"README.md", "./src/a.py", "src", "../secret.md", "nope.md"})
    assert found == {"README.md", "./src/a.py"}
    assert _on_disk(None, {"README.md"}) == set()
