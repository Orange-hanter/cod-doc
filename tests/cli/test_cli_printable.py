"""Пользовательский слаг в сообщениях CLI не несёт управляющих символов в терминал."""

from __future__ import annotations

from click.testing import CliRunner

from cod_doc.cli import main
from cod_doc.cli._common import printable


def test_printable_escapes_control_chars_and_keeps_cyrillic() -> None:
    assert printable("\x1b[2Jслаг\n\x07") == "\\x1b[2Jслаг\\n\\x07"
    assert printable("cod-doc") == "cod-doc"


def test_plan_not_found_message_escapes_ansi(tmp_path) -> None:  # type: ignore[no-untyped-def]
    root = tmp_path / "pr"
    root.mkdir()
    runner = CliRunner()
    assert runner.invoke(main, ["project", "add", str(root), "--name", "pr"]).exit_code == 0

    result = runner.invoke(main, ["plan", "section", "list", "\x1b]0;pwn\x07", "-p", "pr"])
    assert result.exit_code == 1
    assert "\x1b" not in result.output
    assert "\\x1b]0;pwn\\x07" in result.output
