"""Каталог кита Ledger — `/ui/kit`.

Живой образец каждого макроса из `templates/web/_ui/ledger.html` на
выдуманных данных масштаба Restate/Orakul (доска «Ledger · Масштаб» в
pencil-файле редизайна). Страница не читает БД: она нужна, чтобы верстающий
видел компонент целиком и в обеих темах, а не собирал его по месту.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from cod_doc.api.web.templates_env import templates

router = APIRouter()

# Образцы для строк: длины взяты по максимумам живых БД (narrative до 245
# знаков, заголовок задачи до 170), чтобы line-clamp было видно сразу.
_STORIES: list[dict[str, Any]] = [
    {
        "id": "US-201",
        "narrative": (
            "Как сторона партнёрской заявки (агентство или независимый агент-инициатор), "
            "я хочу переписываться с партнёром в треде заявки, чтобы согласовать показы, "
            "документы и сроки внутри платформы и сохранить переписку как доказательство "
            "для арбитража"
        ),
        "status": "accepted",
        "priority": "critical",
        "tasks_done": 3,
        "tasks_total": 12,
    },
    {
        "id": "US-204",
        "narrative": (
            "Как сторона партнёрской заявки, я хочу прикладывать к сообщению файлы "
            "(подписанный оффер, договор о сотрудничестве, документы объекта), чтобы они "
            "хранились вместе с перепиской и были доступны арбитру при споре"
        ),
        "status": "draft",
        "priority": "high",
        "tasks_done": 0,
        "tasks_total": 4,
    },
    *(
        {
            "id": f"US-{190 + i}",
            "narrative": "Как агентство, я хочу видеть статус своей заявки и срок ответа партнёра",
            "status": "accepted",
            "priority": "medium",
            "tasks_done": i,
            "tasks_total": 6,
        }
        for i in range(6)
    ),
]

_TASKS: list[dict[str, Any]] = [
    {
        "id": "KIT-010",
        "title": (
            "ui/Segment: replace ~12 segment families (.segment/.seg/.fin-seg/.staff-seg/"
            ".tasks-seg/.sched-*seg/.mode-switch/.triage-tabs/.ofs-tabs/.prep-period/"
            ".dur-btn) in ~25 files"
        ),
        "status": "in_progress",
        "who": "agent:claude",
    },
    {
        "id": "PBR-086",
        "title": "Retry отправки email при отказе SMTP",
        "status": "todo",
        "who": "—",
    },
    {
        "id": "PBR-079",
        "title": "Модель notification_outbox и миграция",
        "status": "done",
        "who": "human:dakh",
    },
]

_SWATCHES: tuple[tuple[str, str], ...] = (
    ("--bg-inset", "бумага, фон экрана"),
    ("--bg", "поверхность"),
    ("--border", "разделитель"),
    ("--ink", "чернила, primary"),
    ("--muted", "вторичный текст"),
    ("--signal", "проблема, главное действие"),
    ("--signal-soft", "фон метки signal"),
    ("--success", "в синхроне, done"),
    ("--warning", "неточно, замок"),
    ("--info", "ссылки, на разбор"),
    ("--bar", "верхняя панель, код"),
)


@router.get("/ui/kit", response_class=HTMLResponse)
def ui_kit(request: Request) -> HTMLResponse:
    """Каталог компонентов кита Ledger на образцовых данных."""
    return templates.TemplateResponse(
        request,
        "ui_kit.html",
        {"stories": _STORIES, "tasks": _TASKS, "swatches": _SWATCHES},
    )
