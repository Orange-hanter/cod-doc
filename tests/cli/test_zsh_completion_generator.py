"""Юнит-тесты рендерера: экранирование, формы спек, целостность таблиц.

Грамматика ``_arguments`` неумолима: лишнее двоеточие в описании сдвигает
поля спеки, а незакрытая кавычка ломает весь файл — и падает это не тестом, а
молчаливым отказом дополнения в шелле. Поэтому крайние случаи ловим здесь, на
игрушечной группе, а не на живом дереве.
"""

from __future__ import annotations

import re

import click
import pytest

from cod_doc.cli import main as cli_root
from cod_doc.cli.completion.sources import NO_COMPLETE, PARAM_SOURCES, PATH_SOURCES
from cod_doc.cli.completion.zsh import (
    DESC_LIMIT,
    CompletionNameCollisionError,
    describe,
    func_name,
    message,
    render_zsh,
    zq,
)

_MINIMAL_PRELUDE = "# prelude\n"


@click.group()
def toy() -> None:
    """Игрушечная группа."""


@toy.command("edge")
@click.option("--colon", help="Ключ: значение [скобка] с 'кавычкой' и \\слэшем")
@click.option("--pick", type=click.Choice(["a b", "c:d", "plain"]))
@click.option("--flag/--no-flag", default=True, help="Пара")
@click.option("--rep", "-r", multiple=True, help="Повторяемая")
@click.option("--infile", type=click.Path(exists=True))
@click.option("--outdir", type=click.Path(file_okay=False, dir_okay=True))
@click.option("--pair", "-P", help="Короткая и длинная")
@click.argument("victim")
@click.argument("rest", nargs=-1)
def edge(**_: object) -> None:
    """Край."""


@toy.command("loose")
@click.argument("maybe", required=False)
def loose(**_: object) -> None:
    """Необязательный позиционный."""


def _render_toy() -> str:
    return render_zsh(toy, prelude=_MINIMAL_PRELUDE)


def _spec_lines(rendered: str, needle: str) -> str:
    """Первая строка спеки с подстрокой, без отступа и переноса строки."""
    line = next(line for line in rendered.splitlines() if needle in line)
    return line.strip().removesuffix("\\").strip()


# ── экранирование ────────────────────────────────────────────────────────────


def test_colon_and_brackets_are_replaced_not_escaped() -> None:
    line = _spec_lines(_render_toy(), "--colon")
    body = re.search(r"\[(.*?)\]", line)
    assert body is not None
    desc = body.group(1)
    assert ":" not in desc, "двоеточие в описании сдвинуло бы поля спеки"
    assert "(скобка)" in desc, "квадратные скобки обязаны стать круглыми"
    assert "/слэшем" in desc, "обратный слэш zsh показал бы буквально"
    # Единственный уцелевший бэкслэш — из экранирования кавычки '\'' .
    assert desc.replace("'\\''", "").count("\\") == 0


def test_single_quotes_are_doubled_for_zsh() -> None:
    rendered = _render_toy()
    # Каждая кавычка внутри спеки должна встречаться только как '\''.
    for line in rendered.splitlines():
        if "кавычкой" in line:
            assert "'\\''" in line
            break
    else:
        pytest.fail("спека с апострофом не найдена")


def test_describe_truncates_deterministically() -> None:
    long = "я" * (DESC_LIMIT * 2)
    assert len(describe(long)) == DESC_LIMIT
    assert describe(long).endswith("…")


def test_describe_flattens_whitespace() -> None:
    assert describe("первая\n  вторая\tтретья") == "первая вторая третья"


def test_zq_wraps_and_escapes() -> None:
    assert zq("a'b") == "'a'\\''b'"


def test_message_slot_is_restricted_charset() -> None:
    param = click.Option(["--x"], metavar="ПУТЬ К ФАЙЛУ")
    assert re.fullmatch(r"[a-z0-9-]+", message(param))


# ── формы спек ───────────────────────────────────────────────────────────────


def test_short_and_long_share_one_exclusion_group() -> None:
    line = _spec_lines(_render_toy(), "--pair")
    assert "'(-P --pair)'{-P+,--pair=}" in line


def test_bool_pair_gets_both_halves_in_one_group() -> None:
    line = _spec_lines(_render_toy(), "--no-flag")
    assert "'(--flag --no-flag)'{--flag,--no-flag}" in line
    assert ":flag" not in line, "флаг значения не берёт"


def test_multiple_option_is_star_prefixed_without_exclusion() -> None:
    line = _spec_lines(_render_toy(), "--rep")
    assert line.strip().startswith("'*'{-r+,--rep=}")


def test_choice_values_are_quoted_when_unsafe() -> None:
    """Спека целиком в одинарных кавычках, поэтому внутренние идут как '\\''."""
    line = _spec_lines(_render_toy(), "--pick")
    # То, что увидит _arguments после снятия внешних кавычек.
    assert line.replace("'\\''", "'")[1:-1].endswith(":pick:('a b' 'c:d' plain)")


def test_path_types_map_to_files_actions() -> None:
    rendered = _render_toy()
    assert _spec_lines(rendered, "--infile").endswith(":infile:_files'")
    assert _spec_lines(rendered, "--outdir").endswith(":outdir:_files -/'")


def test_required_and_variadic_positionals() -> None:
    rendered = _render_toy()
    assert "'1:victim'" in rendered
    assert "'*:rest'" in rendered


def test_optional_positional_uses_double_colon() -> None:
    assert "'1::maybe'" in _render_toy()


def test_help_spec_is_emitted_although_click_hides_it_from_params() -> None:
    assert "'(- *)--help[Show this message and exit.]'" in _render_toy()


def test_func_name_translates_hyphens() -> None:
    assert func_name(("task", "remove-dep")) == "_cod_doc_task_remove_dep"
    assert func_name(()) == "_cod-doc"


# ── коллизии имён ────────────────────────────────────────────────────────────


def test_colliding_command_names_raise() -> None:
    """`ingest ai_review` и `ingest ai-review` дали бы одну zsh-функцию."""

    @click.group()
    def clash() -> None:
        """Группа с коллизией."""

    @clash.command("ai_review")
    def _underscore() -> None:
        """Подчёркивание."""

    @clash.command("ai-review")
    def _hyphen() -> None:
        """Дефис."""

    with pytest.raises(CompletionNameCollisionError):
        render_zsh(clash, prelude=_MINIMAL_PRELUDE)


def test_live_tree_has_no_name_collisions() -> None:
    render_zsh(cli_root, prelude=_MINIMAL_PRELUDE)


# ── целостность таблиц источников ────────────────────────────────────────────


def _live_params() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def visit(cmd: click.Command, path: tuple[str, ...]) -> None:
        for param in cmd.params:
            if param.name:
                found.add((" ".join(path), param.name))
        if isinstance(cmd, click.Group):
            for name, sub in cmd.commands.items():
                visit(sub, (*path, name))

    visit(cli_root, ())
    return found


@pytest.mark.parametrize("key", sorted(PATH_SOURCES))
def test_path_sources_key_exists_in_tree(key: tuple[str, str]) -> None:
    assert key in _live_params(), f"PATH_SOURCES протух: {key} нет в дереве команд"


@pytest.mark.parametrize("key", sorted(NO_COMPLETE))
def test_no_complete_key_exists_in_tree(key: tuple[str, str]) -> None:
    assert key in _live_params(), f"NO_COMPLETE протух: {key} нет в дереве команд"


@pytest.mark.parametrize("dest", sorted(PARAM_SOURCES))
def test_param_sources_dest_exists_in_tree(dest: str) -> None:
    live = {name for _, name in _live_params()}
    assert dest in live, f"PARAM_SOURCES протух: дест {dest!r} нет ни у одной команды"


def test_project_option_always_completes_project_slugs() -> None:
    """-p обязателен на 88 командах — ни одна не должна остаться без источника."""
    rendered = render_zsh(cli_root, prelude=_MINIMAL_PRELUDE)
    for line in rendered.splitlines():
        if "{-p+,--project=}" in line:
            assert "_cod_doc_projects" in line, line
