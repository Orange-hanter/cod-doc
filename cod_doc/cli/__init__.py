"""
CLI точка входа: cod-doc [команды]

cod-doc tui              — запустить TUI (wizard + dashboard)
cod-doc wizard           — запустить только wizard настройки
cod-doc project add      — добавить проект
cod-doc project list     — список проектов
cod-doc project init     — инициализировать .cod-doc/ в проекте
cod-doc agent run        — запустить агент для проекта
cod-doc serve            — запустить REST API сервер
cod-doc hash calc        — вычислить хэш файла
cod-doc hash update      — обновить хэши в MASTER.md
cod-doc task list        — список задач
cod-doc task show        — детали задачи
cod-doc task create      — создать задачу
cod-doc task status      — обновить статус задачи
cod-doc task complete    — завершить задачу
cod-doc plan show        — прогресс плана
cod-doc plan ready       — готовые задачи
cod-doc plan audit       — аудит плана
cod-doc plan export      — экспорт markdown
cod-doc plan critical-path — критический путь
cod-doc plan forward     — цепочка prerequisites
cod-doc plan reverse     — цепочка dependents
cod-doc story list       — список историй
cod-doc story show       — детали истории
cod-doc story create     — создать историю
cod-doc story status     — обновить статус истории
cod-doc story add-criterion — добавить критерий приёмки
cod-doc story link       — связать историю с задачей/документом
cod-doc story coverage   — покрытие истории
"""

from __future__ import annotations

import click

from cod_doc.cli.cmd_agent import agent
from cod_doc.cli.cmd_hash import hash
from cod_doc.cli.cmd_project import project
from cod_doc.cli.cmd_serve import mcp_server, serve
from cod_doc.cli.cmd_tui import tui, wizard
from cod_doc.cli.plan import plan
from cod_doc.cli.story import story
from cod_doc.cli.task import task
from cod_doc.config import Config
from cod_doc.logging_config import setup_logging


@click.group()
@click.option("--log-level", default=None, envvar="LOG_LEVEL", help="DEBUG|INFO|WARNING|ERROR")
@click.option("--log-format", default=None, envvar="LOG_FORMAT", help="text|json")
@click.pass_context
def main(ctx: click.Context, log_level: str | None, log_format: str | None) -> None:
    """🧭 COD-DOC — Context Orchestrator for Documentation."""
    setup_logging(level=log_level, fmt=log_format)
    ctx.ensure_object(dict)
    ctx.obj["config"] = Config.load()


# Регистрация подкоманд
main.add_command(tui)
main.add_command(wizard)
main.add_command(project)
main.add_command(agent)
main.add_command(hash)
main.add_command(serve)
main.add_command(mcp_server)
main.add_command(task)
main.add_command(plan)
main.add_command(story)


if __name__ == "__main__":
    main()
