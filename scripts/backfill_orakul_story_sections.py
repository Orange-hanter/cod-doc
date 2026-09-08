#!/usr/bin/env python
"""ADO-143: разложить истории orakul по секциям из исходного markdown.

Импортёра историй в cod-doc нет — их заводили руками, и секции взять неоткуда,
кроме документа-первоисточника. Скрипт читает `03-product/02-user-stories.md`:

* заголовки `### … Модуль N: Название` дают секции `module-N`;
* истории под заголовком (`**US-NN | Роль**`) привязываются к своему модулю;
* остальные (US-19…26, 32, 33) живут в annex-таблице ниже по документу, под
  модули не попадают и уезжают в секцию `annex`.

Идентификаторы в документе — `US-NN`, в БД — `US-NNN`, поэтому нормализуем.

    python scripts/backfill_orakul_story_sections.py --project orakul --docs <path>
    python scripts/backfill_orakul_story_sections.py --project orakul --docs <path> --apply
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MODULE_HEADER = re.compile(r"^###\s+\S*\s*Модуль\s+(\d+):\s*(.+?)\s*$")
STORY_ENTRY = re.compile(r"^\*\*(US-\d+)\s*\|")
ANNEX_KEY = "annex"
ANNEX_TITLE = "Прод-фичи вне классической матрицы"


def parse_doc(path: Path) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Вернуть (секции [(key, title)], карта story_id -> section_key)."""
    sections: list[tuple[str, str]] = []
    mapping: dict[str, str] = {}
    current: str | None = None

    for line in path.read_text(encoding="utf-8").splitlines():
        header = MODULE_HEADER.match(line)
        if header:
            number, title = header.group(1), header.group(2).strip()
            current = f"module-{number}"
            sections.append((current, title))
            continue
        # Любой другой заголовок того же уровня закрывает модуль — иначе
        # «Сводная таблица» утянула бы к себе истории последнего модуля.
        if line.startswith("### "):
            current = None
            continue
        entry = STORY_ENTRY.match(line)
        if entry and current is not None:
            mapping[normalise_story_id(entry.group(1))] = current

    sections.append((ANNEX_KEY, ANNEX_TITLE))
    return sections, mapping


def normalise_story_id(doc_id: str) -> str:
    """`US-01` в документе → `US-001` в БД."""
    number = doc_id.split("-", 1)[1]
    return f"US-{int(number):03d}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", default="orakul", help="Project slug in cod-doc")
    ap.add_argument("--docs", required=True, type=Path, help="Path to 02-user-stories.md")
    ap.add_argument("--apply", action="store_true", help="Write; otherwise dry-run")
    ap.add_argument("--author", default="cli", help="Revision author")
    args = ap.parse_args()

    if not args.docs.is_file():
        print(f"Нет файла: {args.docs}", file=sys.stderr)
        return 1

    from cod_doc.config import Config
    from cod_doc.infra.db import db_for_entry, transactional
    from cod_doc.infra.repositories import ProjectRepository
    from cod_doc.services import story_service

    sections, mapping = parse_doc(args.docs)
    print(f"Секций в документе: {len(sections)}; историй с модулем: {len(mapping)}")

    cfg = Config.load()
    entry = cfg.get_project(args.project)
    if entry is None:
        print(f"Проект '{args.project}' не найден в реестре", file=sys.stderr)
        return 1

    factory, engine = db_for_entry(entry)
    try:
        # dry-run прогоняет ровно ту же логику, но откатывает транзакцию.
        with transactional(factory, commit=args.apply) as session:
            repo = ProjectRepository(session)
            proj = repo.get_by_slug(args.project)
            if proj is None or proj.row_id is None:
                print(f"Нет записи проекта '{args.project}' в БД", file=sys.stderr)
                return 1
            project_id = proj.row_id

            existing = {s.key for s in story_service.list_sections(session, project_id)}
            for key, title in sections:
                if key in existing:
                    print(f"  = секция {key} уже есть")
                    continue
                story_service.create_section(
                    session, project_id=project_id, key=key, title=title, author=args.author
                )
                print(f"  + секция {key} «{title}»")

            all_stories = story_service.list_for_project(session, project_id)
            assigned = 0
            to_annex = 0
            for story in all_stories:
                key = mapping.get(story.story_id, ANNEX_KEY)
                story_service.assign_section(
                    session, story_id=story.story_id, key=key, author=args.author
                )
                assigned += 1
                if key == ANNEX_KEY:
                    to_annex += 1
                print(f"  → {story.story_id}: {key}")

            print(f"\nИсторий обработано: {assigned}, из них в annex: {to_annex}")
            if not args.apply:
                print("DRY-RUN: транзакция откачена. Повтори с --apply.")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
