"""Какие click-параметры дополняются какими значениями — и какими запросами.

Ключ — ``param.name`` (python-dest), а не строка опции. Так сделано потому, что
кодовая база уже развела смыслы именно дестами: ``--section`` в ``link *`` это
``anchor`` (якорь секции документа), а в ``task create`` — ``section_letter``
(буква секции плана). Ключ по строке опции склеил бы их в одно.

``COMPLETION_QUERIES`` держит SQL отдельно от ``prelude.zsh``, чтобы
``tests/cli/test_zsh_completion_queries.py`` мог прогнать каждый запрос по
свежей схеме (``alembic upgrade head``) и поймать переименование колонки.
Генератор подставляет их в prelude по плейсхолдеру ``@@SQL:<ключ>@@``.
"""

from __future__ import annotations

from typing import Final

_P: Final = "_cod_doc"

#: Плейсхолдер фильтра по проекту внутри SQL.
#: В zsh разворачивается в ``$(_cod_doc_where_project)``, в тестах — в пустую
#: строку либо в ``and p.slug = '…'``.
PROJECT_FILTER: Final = "{project_filter}"

#: Плейсхолдер дополнительного фильтра (план для секций, doc_key для якорей).
EXTRA_FILTER: Final = "{extra_filter}"

#: Потолок числа кандидатов в одном запросе: страховка от hub-БД на сотни
#: тысяч строк. 2000 кандидатов zsh рисует без заметной паузы.
QUERY_LIMIT: Final = 2000

#: SQL для динамических источников. Формат вывода — ``значение:описание``,
#: двухколоночная форма ``_describe``. Двоеточие внутри описания ломает
#: разбор (``_describe`` режет по ПЕРВОМУ двоеточию), поэтому вычищаем его в
#: SQL: там это дешевле и точнее, чем в zsh. char(10)/char(9) — перевод строки
#: и таб, которые встречаются в title и разорвали бы список кандидатов.
COMPLETION_QUERIES: Final[dict[str, str]] = {
    "tasks": f"""
        select t.task_id || ':' || t.status || ' · ' ||
               replace(replace(replace(t.title, ':', ' -'), char(10), ' '), char(9), ' ')
          from task t
          join project p on p.row_id = t.project_id
         where 1=1 {PROJECT_FILTER}
         order by t.task_id
         limit {QUERY_LIMIT};
    """,
    "docs": f"""
        select d.doc_key || ':' || d.type || ' · ' ||
               replace(replace(replace(d.title, ':', ' -'), char(10), ' '), char(9), ' ')
          from document d
          join project p on p.row_id = d.project_id
         where 1=1 {PROJECT_FILTER}
         order by d.doc_key
         limit {QUERY_LIMIT};
    """,
    "plans": f"""
        select pl.scope || ':' ||
               replace(replace(coalesce(pl.principle, 'plan'), ':', ' -'), char(10), ' ')
          from plan pl
          join project p on p.row_id = pl.project_id
         where 1=1 {PROJECT_FILTER}
         order by pl.scope
         limit {QUERY_LIMIT};
    """,
    "plan_sections": f"""
        select ps.letter || ':' || pl.scope || ' · ' ||
               replace(replace(ps.title, ':', ' -'), char(10), ' ')
          from plan_section ps
          join plan pl on pl.row_id = ps.plan_id
          join project p on p.row_id = pl.project_id
         where 1=1 {PROJECT_FILTER} {EXTRA_FILTER}
         order by pl.scope, ps.position
         limit {QUERY_LIMIT};
    """,
    "doc_sections": f"""
        select distinct s.anchor || ':' ||
               replace(replace(s.heading, ':', ' -'), char(10), ' ')
          from section s
          join document d on d.row_id = s.document_id
          join project p on p.row_id = d.project_id
         where 1=1 {PROJECT_FILTER} {EXTRA_FILTER}
         order by s.anchor
         limit {QUERY_LIMIT};
    """,
    "adrs": f"""
        select a.adr_id || ':' || a.status || ' · ' ||
               replace(replace(a.title, ':', ' -'), char(10), ' ')
          from adr a
          join project p on p.row_id = a.project_id
         where 1=1 {PROJECT_FILTER}
         order by a.adr_id
         limit {QUERY_LIMIT};
    """,
    # У user_story НЕТ колонки title — человекочитаемое поле зовётся narrative.
    "stories": f"""
        select s.story_id || ':' || s.status || ' · ' ||
               substr(replace(replace(s.narrative, ':', ' -'), char(10), ' '), 1, 60)
          from user_story s
          join project p on p.row_id = s.project_id
         where 1=1 {PROJECT_FILTER}
         order by s.story_id
         limit {QUERY_LIMIT};
    """,
    "scenarios": f"""
        select sc.scenario_id || ':' || sc.status || ' · ' ||
               replace(replace(sc.title, ':', ' -'), char(10), ' ')
          from scenario sc
          join project p on p.row_id = sc.project_id
         where 1=1 {PROJECT_FILTER}
         order by sc.scenario_id
         limit {QUERY_LIMIT};
    """,
    "scenario_groups": f"""
        select distinct sc.group_key || ':' || count(*) || ' сценариев'
          from scenario sc
          join project p on p.row_id = sc.project_id
         where sc.group_key is not null {PROJECT_FILTER}
         group by sc.group_key
         order by sc.group_key
         limit {QUERY_LIMIT};
    """,
    # `task remove-dep TASK_ID BLOCKER_ID` снимает СУЩЕСТВУЮЩЕЕ ребро, поэтому
    # второй аргумент — не «любая задача», а только блокеры первой. Направление
    # ребра: from = задача, to = блокер, kind='blocks'
    # (services/task_service.py::remove_dependency).
    "task_blockers": f"""
        select b.task_id || ':' || b.status || ' · ' ||
               replace(replace(b.title, ':', ' -'), char(10), ' ')
          from dependency d
          join task t on t.row_id = d.from_task_id
          join task b on b.row_id = d.to_task_id
          join project p on p.row_id = t.project_id
         where d.kind = 'blocks' {PROJECT_FILTER} {EXTRA_FILTER}
         order by b.task_id
         limit {QUERY_LIMIT};
    """,
    "revisions": f"""
        select r.revision_id || ':' || r.entity_kind || ' · ' ||
               replace(replace(coalesce(r.reason, ''), ':', ' -'), char(10), ' ')
          from revision r
          join project p on p.row_id = r.project_id
         where 1=1 {PROJECT_FILTER}
         order by r.row_id desc
         limit 200;
    """,
    # У routine НЕТ колонки status — есть enabled (boolean).
    "routines": f"""
        select rt.name || ':' || (case rt.enabled when 1 then 'enabled' else 'disabled' end)
               || ' · ' || coalesce(rt.trigger, '')
          from routine rt
          join project p on p.row_id = rt.project_id
         where 1=1 {PROJECT_FILTER}
         order by rt.name
         limit {QUERY_LIMIT};
    """,
}

#: Дест параметра → zsh-функция-источник. Только однозначные по всему дереву.
PARAM_SOURCES: Final[dict[str, str]] = {
    "project": f"{_P}_projects",  # --project/-p (88 мест) + `link suggest PROJECT`
    "project_name": f"{_P}_projects",  # agent run, import all|docs|legacy-tasks
    "project_opt": f"{_P}_projects",  # import * — тот же -p, но другой дест
    "task_id": f"{_P}_tasks",
    "blocker_id": f"{_P}_tasks",  # task remove-dep TASK_ID BLOCKER_ID
    "doc_key": f"{_P}_docs",
    "plan_scope": f"{_P}_plans",
    "section_letter": f"{_P}_plan_sections",  # task create --section
    "anchor": f"{_P}_doc_sections",  # link list|sync|verify --section
    "section_anchor": f"{_P}_doc_sections",  # scenario new|update --section-anchor
    "adr_id": f"{_P}_adrs",
    "superseding_adr_id": f"{_P}_adrs",
    "superseded_adr_id": f"{_P}_adrs",
    "story_id": f"{_P}_stories",
    "scenario_id": f"{_P}_scenarios",
    "group_key": f"{_P}_scenario_groups",
    "revision_id": f"{_P}_revisions",
}

#: Дест многозначен — уточняем полным путём команды. ``name`` это слаг проекта
#: в ``project *``, имя рутины в ``routine run`` и имя адаптера в ``adapter *``.
PATH_SOURCES: Final[dict[tuple[str, str], str]] = {
    ("project init", "name"): f"{_P}_projects",
    ("project migrate", "name"): f"{_P}_projects",
    ("project remove", "name"): f"{_P}_projects",
    ("project status", "name"): f"{_P}_projects",
    ("routine run", "name"): f"{_P}_routines",
    ("adapter remove", "name"): f"{_P}_adapters",
    ("adapter show", "name"): f"{_P}_adapters",
    # Парные позиционные: второй аргумент сужаем по первому, иначе он
    # предлагает тот же список вместе с уже набранным значением, а такой
    # вызов CLI гарантированно отвергнет.
    ("task remove-dep", "blocker_id"): f"{_P}_task_blockers",
    ("adr supersede", "superseded_adr_id"): f"{_P}_adrs_other",
    # Пути, объявленные как обычный str (click.Path разбирается сам).
    ("project add", "path"): "_files -/",
    ("hash calc", "file_path"): "_files",
    ("hash update", "master_path"): "_files",
    ("doc create", "path"): "_files",
    ("doc rename", "new_path"): "_files",
    ("tui", "debug_log_file"): "_files",
    ("wizard", "debug_log_file"): "_files",
    # Корневая группа: help перечисляет значения, но это не click.Choice.
    ("", "log_level"): "(DEBUG INFO WARNING ERROR)",
    ("", "log_format"): "(text json)",
}

#: Параметры, ВВОДЯЩИЕ новый идентификатор. Подсказывать существующие значения
#: здесь вредно: `task create --id ADO-500` — такого id ещё нет, а список
#: занятых только мешает.
NO_COMPLETE: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        ("task create", "task_id"),  # --id
        ("adr new", "adr_id"),  # --adr-id
        ("doc create", "doc_key"),  # --key
        ("doc rename", "new_key"),  # позиционный NEW_KEY
        ("story create", "story_id"),  # --id
        ("scenario new", "scenario_id"),  # --id
        ("project add", "name"),  # --name/-n
        ("adapter add", "name"),  # позиционный NAME
    }
)

__all__ = [
    "COMPLETION_QUERIES",
    "EXTRA_FILTER",
    "NO_COMPLETE",
    "PARAM_SOURCES",
    "PATH_SOURCES",
    "PROJECT_FILTER",
    "QUERY_LIMIT",
]
