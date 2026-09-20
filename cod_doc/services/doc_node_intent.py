"""Вердикт LLM: покрывают ли документы раздела его ``intent``.

Единственный вопрос, который детерминированными правилами не взять. Всё, что
считается по числам — пусто, тонко, без намерения, вырожденная типизация —
живёт в :mod:`cod_doc.services.doc_node_health` и работает без сети.

Находки пишутся в свою партицию ``source_ref="doc_node_health_ai"``. Отдельная
партиция здесь не косметика: автозакрытие обязано идти внутри своей, иначе
пропущенный проход закроет чужие находки. Тот же довод, что у
``structure_drift.reconcile_findings``.

**Ошибка LLM обязана прервать проход, а не закрыть его партицию.** Если
проглотить исключение и продолжить со списком находок, который остался пустым,
сверка решит, что все пробелы вылечены, и закроет их. Это худший исход всей
фичи, и стоит он одной строки — поэтому ``analyze`` не ловит
``AIBackendError`` вовсе, а вызывающий обязан не сверять партицию при ошибке.
Ровно так делать было нельзя: прежний ``nav_service`` ловил ``Exception`` и всё равно
писал пустой результат в кэш, из-за чего «ключ не настроен» выглядел как
«пробелов нет».
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from cod_doc.services import activity_service, finding_service
from cod_doc.services.activity_service import _uuid7
from cod_doc.services.ai_text import AIBackendError, _call_lite_raw
from cod_doc.services.doc_node_health import (
    FINDING_SOURCE,
    SCOPE_NODE,
    HealthIssue,
    assess_nodes,
)
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.config import Config

FINDING_SOURCE_REF = "doc_node_health_ai"
DEFAULT_AUTHOR = "agent:doc_node_intent"

#: Закрывать вердикт модели только после двух промахов подряд. Вердикт
#: субъективен и мигает: замер на живом корпусе (три прогона подряд, один и тот
#: же промпт) дал наборы {architecture, data-model, scenarios},
#: {architecture, data-model}, {architecture, data-model} — ядро стабильно,
#: хвост плавает. С отсрочкой хвост перестаёт производить пару событий
#: `resolved`/`reopened` на каждый прогон. Столько же у прецедента в
#: `structure_drift`, где промежуточный статус даёт ровно N=2.
CLOSE_AFTER_MISSES = 2

#: Сколько документов раздела показывать модели. Раздел на 42 отчёта аудита
#: целиком в промпт не влезет, а решение «покрывает ли intent» принимается по
#: составу, а не по каждой строке.
_DOCS_PER_NODE = 12

#: Столько символов преамбулы — чтобы отличить документ-заглушку от живого.
_PREAMBLE_CHARS = 200

#: Бюджет ответа считается от числа разделов, а не константой. Фиксированные
#: 1500 токенов обрывали ответ на середине уже на 11 разделах: вердикт с
#: тремя пунктами `missing` и пояснением по-русски стоит ~200 токенов, и
#: обрыв приходит не ошибкой модели, а невалидным JSON.
_TOKENS_BASE = 400
_TOKENS_PER_SECTION = 260
#: Потолок — чтобы дерево из сотни разделов не выписало счёт на ровном месте.
_TOKENS_CAP = 8000


def _token_budget(section_count: int) -> int:
    return min(_TOKENS_BASE + _TOKENS_PER_SECTION * section_count, _TOKENS_CAP)


_PROMPT = """\
Ты ревьюишь структуру проектной документации.

Для каждого раздела ниже дано его НАЗНАЧЕНИЕ (intent) и документы, которые в
нём лежат. Реши, покрывают ли документы это назначение.

{sections}

Верни ТОЛЬКО валидный JSON, без markdown-ограждений и пояснений:
{{
  "verdicts": [
    {{
      "node_key": "ключ раздела",
      "covers_intent": true,
      "missing": ["чего конкретно не хватает"],
      "note": "одно предложение — почему"
    }}
  ]
}}

Правила:
- один вердикт на раздел, ключи бери дословно из списка;
- "covers_intent": false ставь только когда назначение раздела заметно шире
  того, что в нём лежит, а не когда просто хочется большего;
- "missing" — до трёх конкретных тем, не общих слов;
- раздел, который покрыт, тоже верни, с пустым "missing".
"""


@dataclass(frozen=True, slots=True)
class Verdict:
    """Вердикт по одному разделу."""

    node_key: str
    covers_intent: bool
    missing: list[str]
    note: str


def build_prompt(sections: list[dict[str, Any]]) -> str:
    """Собрать промпт. Отдельно от вызова — чтобы его можно было проверить."""
    blocks: list[str] = []
    for section in sections:
        docs = section["docs"] or ["(пусто)"]
        listing = "\n".join(f"  - {line}" for line in docs)
        blocks.append(
            f"## {section['node_key']} — {section['title']}\n"
            f"Назначение: {section['intent']}\n"
            f"Документы ({section['total']}):\n{listing}"
        )
    return _PROMPT.format(sections="\n\n".join(blocks))


def collect_sections(session: Session, project_id: int) -> list[dict[str, Any]]:
    """Состав разделов для промпта: назначение плюс что в них лежит.

    Модели показывают только наполненный раздел, на который нет
    детерминированной находки. Её вопрос — «покрывают ли ЭТИ документы
    назначение»; там, где документов нет, ответ арифметический, и его уже
    дало правило. Действие у обеих находок одно и то же: наполнить раздел.
    На свежем проекте пусты все разделы разом — без фильтра первый же прогон
    удвоил бы очередь целиком и оплатил вопрос с заранее известным ответом.
    """
    from cod_doc.infra.models import DocumentModel
    from cod_doc.services import doc_tree_service

    stats = doc_tree_service.node_stats(session, project_id)
    already_flagged = {issue.scope_id for issue in assess_nodes(stats)}
    docs_by_node: dict[int, list[str]] = {}
    for doc in session.query(DocumentModel).filter(DocumentModel.project_id == project_id):
        if doc.node_id is None:
            continue
        preamble = (doc.preamble or "")[:_PREAMBLE_CHARS].replace("\n", " ").strip()
        entry = f"[{doc.type}] {doc.title}" + (f" — {preamble}" if preamble else "")
        docs_by_node.setdefault(doc.node_id, []).append(entry)

    sections: list[dict[str, Any]] = []
    for stat in stats:
        if stat.node.is_inbox or stat.node.row_id is None:
            # Инбокс — очередь разбора, а не раздел с назначением.
            continue
        if stat.doc_count == 0 or stat.node.node_key in already_flagged:
            continue
        entries = docs_by_node.get(stat.node.row_id, [])
        sections.append(
            {
                "node_key": stat.node.node_key,
                "title": stat.node.title,
                "intent": stat.node.intent,
                "total": stat.doc_count,
                "docs": entries[:_DOCS_PER_NODE],
            }
        )
    return sections


def parse_verdicts(raw: str) -> list[Verdict]:
    """Разобрать ответ модели. Ограждение снимается, мусор — это ошибка."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    data = json.loads(text)
    out: list[Verdict] = []
    for item in data.get("verdicts", []):
        node_key = str(item.get("node_key", "")).strip()
        if not node_key:
            continue
        out.append(
            Verdict(
                node_key=node_key,
                covers_intent=bool(item.get("covers_intent", True)),
                missing=[str(m) for m in item.get("missing", [])][:3],
                note=str(item.get("note", "")),
            )
        )
    return out


def verdicts_to_issues(verdicts: list[Verdict], known_nodes: set[str]) -> list[HealthIssue]:
    """Отбросить вердикты о несуществующих разделах и о покрытых.

    Модель иногда придумывает ключ; находка о разделе, которого нет, никогда
    не закроется и будет висеть вечно.
    """
    issues: list[HealthIssue] = []
    for verdict in verdicts:
        if verdict.covers_intent or verdict.node_key not in known_nodes:
            continue
        missing = "; ".join(verdict.missing) if verdict.missing else verdict.note
        issues.append(
            HealthIssue(
                code="NODE-INTENT-AI",
                scope_kind=SCOPE_NODE,
                scope_id=verdict.node_key,
                title=f"Раздел «{verdict.node_key}» не покрывает своё назначение",
                body=missing or "вердикт без пояснения",
                severity="minor",
            )
        )
    return issues


def _seed_for(issue: HealthIssue) -> FindingSeed:
    fingerprint, _basis = finding_service.fingerprint_routine(
        source=FINDING_SOURCE,
        check_name=issue.code,
        scope_kind=issue.scope_kind,
        scope_id=issue.scope_id,
    )
    return FindingSeed(
        fingerprint=fingerprint,
        source=FINDING_SOURCE,
        source_ref=FINDING_SOURCE_REF,
        kind=issue.code,
        title=issue.title,
        body=issue.body,
        severity=issue.severity,
        payload={"scope_kind": issue.scope_kind, "scope_id": issue.scope_id},
    )


def analyze(
    session: Session,
    *,
    project_id: int,
    cfg: Config,
    author: str = DEFAULT_AUTHOR,
) -> dict[str, int]:
    """Спросить модель и записать вердикты в свою партицию находок.

    Бросает :class:`AIBackendError`, если модель недоступна, и
    :class:`json.JSONDecodeError`, если ответ не разобрать. Оба исключения
    обязаны дойти до вызывающего: проглотить их — значит закрыть партицию
    как «вылеченную» по пустому списку.

    Закрытие идёт с гистерезисом (:data:`CLOSE_AFTER_MISSES`): вердикт,
    пропавший на одном прогоне, остаётся ``open`` и виден куратору, а
    закрывается только вторым промахом подряд. В ответе это видно ключом
    ``missed`` — сколько находок промахнулись, но закрытие отложено.
    """
    sections = collect_sections(session, project_id)
    if not sections:
        # Партицию не сверяем: спросить было не о чем, а пустой набор
        # отпечатков закрыл бы все находки модели как «вылеченные».
        return {
            "sections": 0,
            "issues": 0,
            "created": 0,
            "updated": 0,
            "resolved": 0,
            "reopened": 0,
            "missed": 0,
        }

    raw = _call_lite_raw(build_prompt(sections), cfg, max_tokens=_token_budget(len(sections)))
    verdicts = parse_verdicts(raw)
    issues = verdicts_to_issues(verdicts, {s["node_key"] for s in sections})
    seeds = [_seed_for(issue) for issue in issues]

    ingested = finding_service.ingest_findings(
        session,
        project_id=project_id,
        source_run_id=f"{FINDING_SOURCE_REF}:{_uuid7()}",
        seeds=seeds,
    )
    reconciled = finding_service.reconcile_partition(
        session,
        project_id=project_id,
        source=FINDING_SOURCE,
        source_ref=FINDING_SOURCE_REF,
        seen_fingerprints={seed.fingerprint for seed in seeds},
        author=author,
        close_after_misses=CLOSE_AFTER_MISSES,
    )
    activity_service.emit_for_write(
        session,
        project_id,
        "doc.intent_analyzed",
        author,
        scope_kind="project",
        scope_id=str(project_id),
        payload={"sections": len(sections), "issues": len(issues), **reconciled},
        summary=f"Intent coverage: {len(issues)} section(s) below intent",
    )
    return {
        "sections": len(sections),
        "issues": len(issues),
        "created": ingested.created,
        "updated": ingested.updated,
        **reconciled,
    }


__all__ = [
    "FINDING_SOURCE_REF",
    "AIBackendError",
    "Verdict",
    "analyze",
    "build_prompt",
    "collect_sections",
    "parse_verdicts",
    "verdicts_to_issues",
]
