"""ADR-003: smoke for cod-doc adr CLI group."""

from __future__ import annotations

from click.testing import CliRunner

from cod_doc.cli import main


def test_adr_help_lists_five_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["adr", "--help"])
    assert result.exit_code == 0
    out = result.output
    for cmd in ("new", "list", "show", "supersede", "graph"):
        assert cmd in out, f"adr {cmd} missing from --help: {out}"


def test_adr_new_help_has_all_fields() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["adr", "new", "--help"])
    assert result.exit_code == 0
    for opt in (
        "--project",
        "--title",
        "--status",
        "--decided-at",
        "--context",
        "--decision",
        "--alternatives",
        "--consequences",
        "--adr-id",
    ):
        assert opt in result.output, f"{opt} missing"


def test_adr_supersede_help_takes_two_positional_args() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["adr", "supersede", "--help"])
    assert result.exit_code == 0
    assert "SUPERSEDING_ADR_ID" in result.output.upper() or "superseding_adr_id" in result.output


def test_adr_graph_help_supports_format_choice() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["adr", "graph", "--help"])
    assert result.exit_code == 0
    assert "mermaid" in result.output
    assert "json" in result.output
