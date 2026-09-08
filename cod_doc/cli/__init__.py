"""
CLI точка входа: cod-doc [команды]

cod-doc tui              — запустить TUI (wizard + dashboard)
cod-doc wizard           — запустить только wizard настройки
cod-doc project add      — добавить проект
cod-doc project list     — список проектов
cod-doc project init     — инициализировать .cod-doc/ в проекте
cod-doc hub init         — создать/мигрировать глобальную hub-БД
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
cod-doc task checkout    — взять задачу в работу (pending → in-progress)
cod-doc task release     — снять замок с задачи
cod-doc story list       — список историй
cod-doc story show       — детали истории
cod-doc story create     — создать историю
cod-doc story status     — обновить статус истории
cod-doc story add-criterion — добавить критерий приёмки
cod-doc story set-criterion — отметить критерий выполненным
cod-doc story section add|list — секции (продуктовые модули)
cod-doc story set-section — привязать историю к секции
cod-doc story link       — связать историю с задачей/документом
cod-doc story coverage   — покрытие истории
cod-doc doc list         — список документов
cod-doc doc show         — детали документа
cod-doc doc create       — создать документ
cod-doc doc rename       — переименовать документ
cod-doc doc delete       — удалить документ из БД
cod-doc doc body         — показать тело документа
cod-doc doc export       — экспортировать на диск
cod-doc doc drift        — проверить дрейф проекции
cod-doc doc import       — импортировать из файла
cod-doc link list        — список ссылок документа
cod-doc link sync        — синхронизировать ссылки секции
cod-doc link verify      — проверить ссылки секции
cod-doc revision list    — история ревизий сущности
cod-doc revision show    — детали ревизии
cod-doc revision revert  — откатить ревизию
cod-doc audit            — проверка frontmatter + дрейфа (FM-*/DR-*)
cod-doc import docs      — импорт .md/.rst/.txt из репо как Documents
cod-doc import legacy-tasks — миграция .cod-doc/tasks.yaml в DB
cod-doc import all       — оба пайплайна подряд
cod-doc ingest <adapter> — ingest внешних находок (ai_review, zairgrush_*)
cod-doc ctx docs           — контекст: документы проекта
cod-doc ctx drift          — контекст: дрейф проекций проекта
cod-doc ctx search         — контекст: поиск по проекту
cod-doc finding stability — Jaccard-стабильность находок по SHA
cod-doc routine list       — список рутин с последним запуском
cod-doc routine tick       — один тик планировщика (для OS cron/launchd)
cod-doc routine run        — ручной запуск рутины по имени
cod-doc embed status       — провайдер эмбеддингов: резолв ключа и состояние индекса
cod-doc embed probe        — живой вызов эмбеддера (размерность, цена, задержка)
cod-doc embed models       — каталог моделей эмбеддингов провайдера
cod-doc embed reset        — удалить векторную коллекцию (смена модели)
"""

from __future__ import annotations

import click

from cod_doc.cli.adr import adr
from cod_doc.cli.cmd_adapter import adapter
from cod_doc.cli.cmd_agent import agent
from cod_doc.cli.cmd_audit import audit
from cod_doc.cli.cmd_ctx import ctx
from cod_doc.cli.cmd_embed import embed
from cod_doc.cli.cmd_finding import finding
from cod_doc.cli.cmd_hash import hash
from cod_doc.cli.cmd_hub import hub
from cod_doc.cli.cmd_import import import_cmd
from cod_doc.cli.cmd_ingest import ingest
from cod_doc.cli.cmd_project import project
from cod_doc.cli.cmd_reindex import reindex
from cod_doc.cli.cmd_search import search as search_cmd
from cod_doc.cli.cmd_serve import mcp_server, serve
from cod_doc.cli.cmd_tui import tui, wizard
from cod_doc.cli.doc import doc
from cod_doc.cli.link import link
from cod_doc.cli.plan import plan
from cod_doc.cli.revision import revision
from cod_doc.cli.routine import routine
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
main.add_command(hub)
main.add_command(agent)
main.add_command(hash)
main.add_command(serve)
main.add_command(mcp_server)
main.add_command(task)
main.add_command(plan)
main.add_command(story)
main.add_command(doc)
main.add_command(link)
main.add_command(revision)
main.add_command(routine)
main.add_command(audit)
main.add_command(import_cmd)
main.add_command(ingest)
main.add_command(ctx)
main.add_command(finding)
main.add_command(adapter)
main.add_command(adr)
main.add_command(embed)
main.add_command(reindex)
main.add_command(search_cmd)


if __name__ == "__main__":
    main()
