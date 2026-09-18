"""Рендерер zsh-completion из живого click-дерева.

Чистая функция: на входе корневая ``click.Group`` и текст ``prelude.zsh``, на
выходе строка. Писать на диск умеет только ``__main__``; сверять с
закоммиченным артефактом — ``tests/cli/test_zsh_completion_drift.py``.

Форма вывода — идиома ``_git``/``_docker``: ``_arguments -C`` с диспетчером по
``->state`` и отдельной функцией на каждую команду. Альтернативу (один
монолитный ``case``) отвергли: 130+ функций отлаживаются поштучно
(``_cod_doc_task_show`` можно позвать руками), ``-C`` даёт свой ``curcontext``
на команду, и на каждое нажатие TAB исполняется только цепочка диспетчеров,
а не спеки всех ста команд разом.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Final

import click

from cod_doc.cli.completion.sources import (
    COMPLETION_QUERIES,
    EXTRA_FILTER,
    NO_COMPLETE,
    PARAM_SOURCES,
    PATH_SOURCES,
    PROJECT_FILTER,
)

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

#: Имя корневой функции; для файла ``#compdef`` оно же имя файла.
ROOT_FUNC: Final = "_cod-doc"

#: Префикс всех порождённых функций (дефис в имени команды → подчёркивание).
PREFIX: Final = "_cod_doc"

#: Потолок длины описания в спеке. Описания косметические; обрезаем
#: детерминированно, чтобы правка второго абзаца docstring'а не двигала байты.
DESC_LIMIT: Final = 68

#: Длина короткой опции («-p»): в спеке она получает «+», длинная — «=».
SHORT_OPT_LEN: Final = 2

_WS: Final = re.compile(r"\s+")
_MSG_BAD: Final = re.compile(r"[^a-z0-9-]+")
_SQL_SLOT: Final = re.compile(r"@@SQL:([a-z_]+)@@")
_SAFE_CHOICE: Final = re.compile(r"[A-Za-z0-9._@%+/=,-]+")

#: Грамматика ``_arguments``: «[» и «]» ограничивают описание, «:» разделяет
#: поля спеки, «\» экранирует. Эти четыре символа не экранируем, а ЗАМЕНЯЕМ —
#: обратный слэш внутри скобок zsh потом показывает буквально, и описание
#: выглядит сломанным. Кавычки и «$» оставляем: спека целиком завёрнута в
#: одинарные кавычки, подстановок внутри не бывает.
_DESC_TRANS: Final = str.maketrans({":": ";", "[": "(", "]": ")", "\\": "/"})

_HEADER: Final = """#compdef cod-doc
# ╔═══════════════════════════════════════════════════════════════════════════╗
# ║  СГЕНЕРИРОВАНО — не редактировать руками.                                 ║
# ║                                                                           ║
# ║    генератор  : cod_doc/cli/completion/zsh.py                             ║
# ║    источники  : cod_doc/cli/completion/sources.py                         ║
# ║    runtime    : cod_doc/cli/completion/prelude.zsh                        ║
# ║    регенерация: python -m cod_doc.cli.completion --write                  ║
# ║    сторож     : tests/cli/test_zsh_completion_drift.py                    ║
# ╚═══════════════════════════════════════════════════════════════════════════╝
"""

_SEPARATOR: Final = "\n# ─────────────────── дерево команд (сгенерировано) ────────────────────\n"


class CompletionNameCollisionError(RuntimeError):
    """Две команды схлопнулись в одно имя zsh-функции.

    ``ingest ai_review`` даёт ``_cod_doc_ingest_ai_review``; гипотетическая
    ``ingest ai-review`` дала бы то же имя и молча перетёрла бы первую.
    Лучше упасть в CI, чем отлаживать это в шелле.
    """


class PreludeSlotError(RuntimeError):
    """Плейсхолдер ``@@SQL:…@@`` и ``COMPLETION_QUERIES`` разошлись."""


# ── примитивы экранирования ──────────────────────────────────────────────────


def zq(text: str) -> str:
    """Обернуть в одинарные кавычки zsh; внутренняя кавычка → ``'\\''``."""
    return "'" + text.replace("'", "'\\''") + "'"


def describe(text: str | None) -> str:
    """click-help → безопасное описание для ``_arguments``/``_describe``."""
    if not text:
        return ""
    flat = _WS.sub(" ", text).strip().translate(_DESC_TRANS)
    if len(flat) > DESC_LIMIT:
        flat = flat[: DESC_LIMIT - 1].rstrip() + "…"
    return flat


def command_desc(cmd: click.Command) -> str:
    """Описание команды: short_help, иначе первый абзац docstring'а."""
    text = cmd.short_help or cmd.help or ""
    return describe(text.strip().split("\n\n", 1)[0])


def message(param: click.Parameter) -> str:
    """Слот ``:message:`` — только ``[a-z0-9-]``, иначе поедет грамматика."""
    raw = (param.metavar or param.name or "arg").lower().replace("_", "-")
    return _MSG_BAD.sub("-", raw).strip("-") or "arg"


def func_name(path: Sequence[str]) -> str:
    """``('task', 'remove-dep')`` → ``_cod_doc_task_remove_dep``.

    Корень — исключение: файл ``#compdef`` обязан определить функцию, названную
    как сам файл, поэтому у него дефис (``_cod-doc``), как у ``_git``.
    """
    if not path:
        return ROOT_FUNC
    return PREFIX + "".join("_" + part.replace("-", "_") for part in path)


def helper_name(path: Sequence[str], suffix: str) -> str:
    """Имя служебной функции: всегда через ``_cod_doc_``, без дефиса корня."""
    return PREFIX + "".join("_" + part.replace("-", "_") for part in path) + "_" + suffix


def _choice_word(value: str) -> str:
    """Значение ``Choice`` внутри ``(a b c)``; небезопасное — в кавычки."""
    text = str(value)
    if _SAFE_CHOICE.fullmatch(text):
        return text
    return zq(text)


# ── выбор action'а для параметра ─────────────────────────────────────────────


def action_for(path: tuple[str, ...], param: click.Parameter) -> str:
    """Хвостовая часть спеки: функция-источник, список значений или пусто."""
    key = (" ".join(path), param.name or "")
    if key in NO_COMPLETE:
        return ""
    if key in PATH_SOURCES:
        return PATH_SOURCES[key]
    if param.name in PARAM_SOURCES:
        return PARAM_SOURCES[param.name]

    ptype = param.type
    if isinstance(ptype, click.Choice):
        return "(" + " ".join(_choice_word(c) for c in ptype.choices) + ")"
    if isinstance(ptype, click.Path):
        return "_files -/" if ptype.dir_okay and not ptype.file_okay else "_files"
    if isinstance(ptype, click.File):
        return "_files"
    return ""


# ── спеки ────────────────────────────────────────────────────────────────────


def _takes_value(opt: click.Option) -> bool:
    """Флаги и счётчики значения не берут — двоеточия в спеке им не нужны."""
    return not opt.is_flag and not opt.count


def _forms(opt: click.Option) -> list[str]:
    """Варианты написания: ``-p+`` (слитно ``-pfoo``), ``--project=``.

    Короткая форма идёт первой — так спека читается как в ``_git``.
    """
    if not _takes_value(opt):
        return [*opt.opts, *opt.secondary_opts]
    ordered = sorted(opt.opts, key=len)
    return [f"{o}+" if len(o) == SHORT_OPT_LEN else f"{o}=" for o in ordered]


def _exclusion(opt: click.Option) -> str:
    """``*`` для повторяемых, иначе группа взаимного исключения написаний.

    Порядок внутри группы тот же, что у ``_forms``: короткая форма первой.
    """
    if opt.multiple:
        return "*"
    return "(" + " ".join(sorted([*opt.opts, *opt.secondary_opts], key=len)) + ")"


def _brace_spec(exclusion: str, forms: list[str], body: str) -> str:
    """Спека с несколькими написаниями: ``'(-p --project)'{-p+,--project=}'[…]'``.

    Скобка раскрывается zsh в несколько полных спек, каждая со своей копией
    группы исключения — только так «-p» и «--project» становятся для
    ``_arguments`` одной и той же опцией.
    """
    if len(forms) == 1:
        return zq(f"{exclusion}{forms[0]}{body}")
    braced = zq(exclusion) + "{" + ",".join(forms) + "}"
    return braced + zq(body) if body else braced


def help_spec(cmd: click.Command) -> str | None:
    """``--help`` click добавляет на разборе, в ``params`` его нет."""
    if not cmd.add_help_option:
        return None
    names = cmd.context_settings.get("help_option_names") or ["--help"]
    return _brace_spec("(- *)", list(names), "[Show this message and exit.]")


def option_spec(path: tuple[str, ...], opt: click.Option) -> str:
    """Одна спека ``_arguments`` для опции (уже в кавычках zsh)."""
    desc = describe(opt.help)
    body = f"[{desc}]" if desc else ""
    if _takes_value(opt):
        action = action_for(path, opt)
        body += f":{message(opt)}" + (f":{action}" if action else "")

    return _brace_spec(_exclusion(opt), _forms(opt), body)


def argument_spec(path: tuple[str, ...], arg: click.Argument, index: int) -> str:
    """Спека позиционного аргумента; ``nargs=-1`` → ``*``."""
    action = action_for(path, arg)
    tail = f":{message(arg)}" + (f":{action}" if action else "")
    if arg.nargs == -1:
        return zq(f"*{tail}")
    # Второе двоеточие помечает необязательный позиционный: '1::master-path:_files'.
    return zq(f"{index}{'' if arg.required else ':'}{tail}")


def _sorted_options(cmd: click.Command) -> list[click.Option]:
    """``--project`` вперёд, остальное — в порядке объявления.

    ``-p`` обязателен на 88 командах и не имеет ни envvar, ни default, так
    что первым в списке он экономит больше всего нажатий.
    """
    opts = [p for p in cmd.params if isinstance(p, click.Option) and not p.hidden]
    project = [o for o in opts if o.name in {"project", "project_opt"}]
    rest = [o for o in opts if o not in project]
    return [*project, *rest]


def _option_specs(cmd: click.Command, path: tuple[str, ...]) -> list[str]:
    """Спеки опций команды плюс синтетический ``--help`` в хвосте."""
    specs = [option_spec(path, opt) for opt in _sorted_options(cmd)]
    help_ = help_spec(cmd)
    if help_:
        specs.append(help_)
    return specs


def _specs_for(cmd: click.Command, path: tuple[str, ...]) -> list[str]:
    """Все спеки листовой команды — сперва опции, затем позиционные."""
    specs = _option_specs(cmd, path)
    index = 0
    for param in cmd.params:
        if not isinstance(param, click.Argument):
            continue
        index += 1
        specs.append(argument_spec(path, param, index))
    return specs


# ── блоки кода ───────────────────────────────────────────────────────────────


def _arguments_call(specs: list[str], *, dispatch: bool) -> str:
    """Собрать многострочный вызов ``_arguments`` с переносами."""
    flags = "-C -s -S" if dispatch else "-s -S"
    body = " \\\n    ".join(specs)
    return f"  _arguments {flags} \\\n    {body} && ret=0"


def _guard(name: str, *, guarded: bool = True) -> str:
    """Заголовок определения функции.

    Корневую функцию guard'ить НЕЛЬЗЯ. Файл ``#compdef`` автозагружается так,
    что его тело становится телом функции-заглушки: к моменту исполнения
    ``$+functions[_cod-doc]`` уже равно 1. С guard'ом определение было бы
    пропущено, а завершающий ``_cod-doc "$@"`` позвал бы заглушку ещё раз —
    и так до «maximum nested function level reached». Поэтому корень
    переопределяет себя безусловно, ровно как ``_git`` (см. его строку 8380).
    """
    if not guarded:
        return f"{name}()"
    return f"(( $+functions[{name}] )) || {name}()"


def _leaf_block(cmd: click.Command, path: tuple[str, ...]) -> str:
    name = func_name(path)
    specs = _specs_for(cmd, path)
    return "\n".join(
        [
            f"{_guard(name)} {{",
            '  local curcontext="$curcontext" ret=1',
            "  local -a line",
            "  typeset -A opt_args",
            _arguments_call(specs, dispatch=False),
            "  return ret",
            "}",
            "",
        ]
    )


def _dispatch_case(path: tuple[str, ...], names: list[str]) -> list[str]:
    width = max((len(n) for n in names), default=0)
    lines = []
    for name in names:
        target = func_name((*path, name))
        lines.append(f"        ({name}){' ' * (width - len(name))} {target} && ret=0 ;;")
    lines.append(f"        (*){' ' * max(width - 1, 0)} _default && ret=0 ;;")
    return lines


def _group_block(cmd: click.Group, path: tuple[str, ...], names: list[str]) -> str:
    name = func_name(path)
    state = helper_name(path, "cmd").lstrip("_")
    specs = _option_specs(cmd, path)
    specs.append(zq(f"1: :{helper_name(path, 'commands')}"))
    specs.append(zq(f"*:: :->{state}"))
    context = ":".join(["cod-doc", *path]) if path else "cod-doc"
    return "\n".join(
        [
            f"{_guard(name, guarded=bool(path))} {{",
            '  local curcontext="$curcontext" state line ret=1',
            "  typeset -A opt_args",
            _arguments_call(specs, dispatch=True),
            "",
            "  case $state in",
            f"    ({state})",
            f'      curcontext="${{curcontext%:*:*}}:{context.replace(":", "-")}-$words[1]:"',
            "      case $words[1] in",
            *_dispatch_case(path, names),
            "      esac",
            "      ;;",
            "  esac",
            "  return ret",
            "}",
            "",
        ]
    )


def _commands_block(path: tuple[str, ...], subs: list[tuple[str, click.Command]]) -> str:
    name = helper_name(path, "commands")
    tag = "-".join(["cod-doc", *path, "commands"])
    label = " ".join(["cod-doc", *path, "command"])
    rows = [f"    {zq(f'{sub_name}:{command_desc(sub)}')}" for sub_name, sub in subs]
    return "\n".join(
        [
            f"{_guard(name)} {{",
            "  local -a cmds",
            "  cmds=(",
            *rows,
            "  )",
            f"  _describe -t {tag} {zq(label)} cmds",
            "}",
            "",
        ]
    )


# ── обход дерева ─────────────────────────────────────────────────────────────


def _subcommands(group: click.Group) -> list[tuple[str, click.Command]]:
    return sorted((n, c) for n, c in group.commands.items() if not c.hidden)


def _walk(cmd: click.Command, path: tuple[str, ...]) -> Iterator[str]:
    """Детерминированный обход: сперва сама группа, затем её дети по алфавиту."""
    if isinstance(cmd, click.Group):
        subs = _subcommands(cmd)
        yield _group_block(cmd, path, [n for n, _ in subs])
        yield _commands_block(path, subs)
        for name, sub in subs:
            yield from _walk(sub, (*path, name))
        return
    yield _leaf_block(cmd, path)


def _assert_unique(root: click.Group) -> None:
    seen: dict[str, tuple[str, ...]] = {}

    def visit(cmd: click.Command, path: tuple[str, ...]) -> None:
        name = func_name(path)
        if name in seen:
            raise CompletionNameCollisionError(
                f"{name}: {' '.join(seen[name])!r} и {' '.join(path)!r} дают одно имя"
            )
        seen[name] = path
        if isinstance(cmd, click.Group):
            for sub_name, sub in _subcommands(cmd):
                visit(sub, (*path, sub_name))

    visit(root, ())


# ── prelude ──────────────────────────────────────────────────────────────────


def prelude_slots(prelude: str) -> set[str]:
    """Ключи ``@@SQL:…@@``, встречающиеся в тексте prelude."""
    return {match.group(1) for match in _SQL_SLOT.finditer(prelude)}


def expand_prelude(prelude: str) -> str:
    """Подставить SQL в ``@@SQL:<ключ>@@`` и развернуть плейсхолдеры фильтров.

    Неизвестный ключ — ошибка здесь и сейчас. Обратную проверку («каждый
    запрос где-то используется») делает тест: рендерить можно и с урезанным
    prelude, а вот молча подставить несуществующий SQL нельзя.
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in COMPLETION_QUERIES:
            raise PreludeSlotError(f"@@SQL:{key}@@ нет в COMPLETION_QUERIES")
        sql = COMPLETION_QUERIES[key]
        sql = sql.replace(PROJECT_FILTER, "$(_cod_doc_where_project)")
        sql = sql.replace(EXTRA_FILTER, "$filter")
        return _WS.sub(" ", sql).strip()

    return _SQL_SLOT.sub(repl, prelude)


# ── публичный вход ───────────────────────────────────────────────────────────


def render_zsh(root: click.Group, prelude: str) -> str:
    """Собрать полный текст ``_cod-doc``."""
    _assert_unique(root)
    blocks = list(_walk(root, ()))
    parts = [
        _HEADER,
        expand_prelude(prelude),
        _SEPARATOR,
        *blocks,
        # Файл #compdef на первом вызове исполняется целиком: тело становится
        # определением функции, и её надо позвать самим.
        f'{ROOT_FUNC} "$@"',
        "",
    ]
    return "\n".join(parts)
