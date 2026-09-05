"""RFC 22 §3.5 (SYM-010, фаза 4a): детерминированный drift-гейт для PR.

Гейт собирает три класса **проверяемых фактов** о документах проекта:

- ``drift`` — проекционный дрейф DB ↔ markdown (`projection_service.detect_drift`);
- ``link`` — ссылки и якоря, которые не резолвятся (`link_service.resolve_section`);
- ``frontmatter`` — advisory-нарушения frontmatter (`validation.audit_frontmatter`).

Это ровно тот класс находок, который P0 #19 Orakul велит вынести из LLM: у
результата нет вероятности и нет «мнения» — он либо воспроизводится побайтово,
либо нет. Находки отдаются в форме движка ai-review (`slimFinding` + `model:
"cod-doc/drift"`, `prescan: true`), чтобы потребитель не писал парсер под
cod-doc.

Модуль **read-only по БД**: вызывать внутри ``transactional(..., commit=False)``.
``resolve_section`` трогает derived-таблицу ``link`` только в памяти, финальный
rollback её сбрасывает — так же, как это делает ``cod-doc ctx docs``.

Доставка находок (PR-комментарий) живёт в :mod:`cod_doc.services.gh_service`;
здесь — только сбор и рендер тела комментария.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cod_doc.services import projection_service, validation
from cod_doc.services.finding_service import fingerprint_drift_gate

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.orm import Session

    from cod_doc.domain.entities import Document

# Маркер идемпотентности: единственный якорь, по которому cod-doc узнаёт
# собственный комментарий среди чужих. Меняешь формат — теряешь все ранее
# оставленные комментарии (появятся дубликаты), поэтому он и вынесен в
# константу с явным namespace.
MARKER_NAMESPACE = "cod-doc:drift-gate"

#: Значение поля ``model`` в находке — как его увидит движок ai-review.
MODEL = "cod-doc/drift"

#: Источник для фингерпринта (не путать с ``model``: тот идёт наружу).
SOURCE = "cod_doc_drift"

# drift-статус → severity в терминах движка (critical/major/minor/nit).
_DRIFT_SEVERITY: dict[str, str] = {
    "missing": "major",
    "stale_export": "minor",
    "edited_in_place": "minor",
}

# severity ValidationIssue → severity движка.
_VALIDATION_SEVERITY: dict[str, str] = {
    "error": "major",
    "warning": "minor",
}

_SEVERITY_ORDER: dict[str, int] = {"critical": 0, "major": 1, "minor": 2, "info": 3, "nit": 3}


def marker(project: str) -> str:
    """HTML-комментарий-маркер для конкретного проекта.

    Проект входит в маркер, чтобы два cod-doc-проекта, смотрящих в один
    репозиторий, обновляли каждый свой комментарий, а не перетирали друг друга.
    """
    return f"<!-- {MARKER_NAMESPACE}:{project} -->"


@dataclass(slots=True, frozen=True)
class GateFinding:
    """Одна находка гейта в форме, близкой к ``slimFinding`` движка."""

    path: str
    doc_key: str
    rule: str  # drift | link | frontmatter
    code: str  # DRIFT-EDITED_IN_PLACE | LINK-BROKEN | FM-002 | …
    severity: str  # critical | major | minor | nit
    title: str
    body: str
    fp: str
    anchor: str | None = None

    def as_engine_dict(self) -> dict[str, Any]:
        """Находка в форме движка ai-review (`prescan`, `model`, `fp`)."""
        return {
            "title": self.title,
            "severity": self.severity,
            "file": self.path,
            "line": None,
            "body": self.body,
            "model": MODEL,
            "prescan": True,
            "confidence": 1.0,
            "fp": self.fp,
            "rule": self.rule,
            "code": self.code,
            "anchor": self.anchor,
            "doc_key": self.doc_key,
        }


@dataclass(slots=True)
class GateReport:
    """Результат прогона гейта по выборке файлов."""

    project: str
    changed_files: list[str] | None
    scanned_docs: int
    findings: list[GateFinding] = field(default_factory=list)
    drift_counts: dict[str, int] = field(default_factory=dict)

    @property
    def finding_count(self) -> int:
        return len(self.findings)

    @property
    def counts_by_rule(self) -> dict[str, int]:
        counts = {"drift": 0, "link": 0, "frontmatter": 0}
        for finding in self.findings:
            counts[finding.rule] = counts.get(finding.rule, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        """Payload в форме движка: находки плюс сводка прогона."""
        return {
            "model": MODEL,
            "prescan": True,
            "project": self.project,
            "changed_files": self.changed_files,
            "scanned_docs": self.scanned_docs,
            "drift_counts": self.drift_counts,
            "counts_by_rule": self.counts_by_rule,
            "finding_count": self.finding_count,
            "findings": [f.as_engine_dict() for f in self.findings],
        }


def _select_docs(
    session: Session,
    project_id: int,
    changed_files: Sequence[str] | None,
) -> list[Document]:
    """Документы проекта, суженные до ``changed_files`` (если задан)."""
    from cod_doc.services import doc_service

    docs = doc_service.list_for_project(session, project_id)
    if changed_files is None:
        return docs
    wanted = {projection_service.normalize_repo_path(p) for p in changed_files}
    return [d for d in docs if projection_service.normalize_repo_path(d.path) in wanted]


def _drift_findings(
    session: Session,
    docs: Sequence[Document],
    root_path: Path,
) -> tuple[list[GateFinding], dict[str, int]]:
    """Находки класса ``drift`` плюс распределение статусов по выборке."""
    counts = {status.value: 0 for status in projection_service.DriftStatus}
    findings: list[GateFinding] = []

    for doc in docs:
        if doc.row_id is None:
            continue
        report = projection_service.detect_drift(session, doc.row_id, root_path=root_path)
        counts[report.status.value] += 1
        if report.status is projection_service.DriftStatus.IN_SYNC:
            continue
        code = f"DRIFT-{report.status.value.upper()}"
        fp, _basis = fingerprint_drift_gate(
            source=SOURCE, path=doc.path, rule="drift", code=code, subject=doc.doc_key
        )
        findings.append(
            GateFinding(
                path=doc.path,
                doc_key=doc.doc_key,
                rule="drift",
                code=code,
                severity=_DRIFT_SEVERITY.get(report.status.value, "minor"),
                title=f"Дрейф проекции: {report.status.value}",
                body=_drift_body(report.status.value, doc.doc_key),
                fp=fp,
            )
        )
    return findings, counts


def _drift_body(status: str, doc_key: str) -> str:
    explanation = {
        "edited_in_place": (
            "файл на диске изменён после последнего экспорта — правка не доехала "
            "до БД (`cod-doc doc import`)"
        ),
        "stale_export": (
            "в БД есть изменения, которых нет в файле — проекция устарела (`cod-doc doc export`)"
        ),
        "missing": "документ есть в БД, но файла по его пути нет на диске",
    }.get(status, status)
    return f"`{doc_key}`: {explanation}."


def _link_findings(session: Session, docs: Sequence[Document]) -> list[GateFinding]:
    """Находки класса ``link``: ссылки и якоря, которые не резолвятся."""
    from cod_doc.services import doc_service, link_service

    findings: list[GateFinding] = []
    for doc in docs:
        if doc.row_id is None:
            continue
        for section in doc_service.get_sections(session, doc.row_id):
            if section.row_id is None:
                continue
            # resolve_section синхронизирует ссылки секции в памяти; вызывающий
            # обязан держать commit=False-транзакцию.
            link_service.resolve_section(session, section.row_id)
            for link in link_service.list_for_section(session, section.row_id):
                if link.resolved:
                    continue
                subject = f"{section.anchor}|{link.raw}"
                fp, _basis = fingerprint_drift_gate(
                    source=SOURCE,
                    path=doc.path,
                    rule="link",
                    code="LINK-BROKEN",
                    subject=subject,
                )
                findings.append(
                    GateFinding(
                        path=doc.path,
                        doc_key=doc.doc_key,
                        rule="link",
                        code="LINK-BROKEN",
                        severity="major",
                        title=f"Ссылка не резолвится: {link.raw}",
                        body=(
                            f"`{doc.doc_key}#{section.anchor}` → `{link.raw}` "
                            f"({link.kind.value}): {link.broken_reason or 'не резолвится'}."
                        ),
                        fp=fp,
                        anchor=section.anchor,
                    )
                )
    return findings


def _frontmatter_findings(docs: Sequence[Document]) -> list[GateFinding]:
    """Находки класса ``frontmatter``: advisory-аудит без записи в БД."""
    findings: list[GateFinding] = []
    for doc in docs:
        issues = validation.audit_frontmatter(
            type=doc.type,
            status=doc.status,
            owner=doc.owner,
            source_of_truth=doc.source_of_truth,
            frontmatter=doc.frontmatter,
            last_updated=doc.last_updated,
        )
        for issue in issues:
            fp, _basis = fingerprint_drift_gate(
                source=SOURCE,
                path=doc.path,
                rule="frontmatter",
                code=issue.code,
                subject=doc.doc_key,
            )
            findings.append(
                GateFinding(
                    path=doc.path,
                    doc_key=doc.doc_key,
                    rule="frontmatter",
                    code=issue.code,
                    severity=_VALIDATION_SEVERITY.get(issue.severity, "minor"),
                    title=f"Frontmatter {issue.code}: {issue.message}",
                    body=f"`{doc.doc_key}`: {issue.message}.",
                    fp=fp,
                )
            )
    return findings


def collect(
    session: Session,
    *,
    project: str,
    project_id: int,
    root_path: Path,
    changed_files: Sequence[str] | None = None,
) -> GateReport:
    """Собрать находки гейта по проекту, сузив выборку до ``changed_files``.

    ``changed_files`` — repo-относительные пути (то, что отдаёт ``gh pr view
    --json files``). ``None`` — весь проект; пустой список — осознанно пустой
    прогон (в PR не тронуто ни одного документа).
    """
    docs = _select_docs(session, project_id, changed_files)
    drift, drift_counts = _drift_findings(session, docs, root_path)
    findings = [*drift, *_link_findings(session, docs), *_frontmatter_findings(docs)]
    findings.sort(key=lambda f: (f.path, _SEVERITY_ORDER.get(f.severity, 9), f.code, f.fp))
    return GateReport(
        project=project,
        changed_files=list(changed_files) if changed_files is not None else None,
        scanned_docs=len(docs),
        findings=findings,
        drift_counts=drift_counts,
    )


_RULE_TITLES: dict[str, str] = {
    "drift": "Дрейф проекции (DB ↔ markdown)",
    "link": "Ссылки и якоря",
    "frontmatter": "Frontmatter",
}


def render_comment(report: GateReport, *, pr: int | None = None) -> str:
    """Тело PR-комментария: маркер + сводка + находки, сгруппированные по правилу.

    Первая строка — маркер: по нему же комментарий и находится при повторном
    прогоне, поэтому он обязан быть в теле, а не только в метаданных.
    """
    lines = [
        marker(report.project),
        f"### cod-doc drift gate — `{report.project}`",
        "",
        (
            "Детерминированный гейт целостности документации: дрейф проекции, "
            "нерезолвящиеся ссылки/якоря, frontmatter. Не LLM — каждая строка "
            "воспроизводима. Статус **advisory**: гейт ничего не блокирует."
        ),
        "",
    ]

    changed = report.changed_files
    scope = "весь проект" if changed is None else f"{len(changed)} файл(ов) из PR"
    lines.append(f"Область: {scope} → документов под проверкой: **{report.scanned_docs}**.")

    if not report.findings:
        lines.append("")
        lines.append("Находок нет.")
        lines.append("")
        lines.append(_footer(pr))
        return "\n".join(lines)

    counts = report.counts_by_rule
    summary = ", ".join(
        f"{_RULE_TITLES[rule].split(' (')[0].lower()}: {counts[rule]}"
        for rule in ("drift", "link", "frontmatter")
        if counts.get(rule)
    )
    lines.append(f"Находок: **{report.finding_count}** ({summary}).")
    lines.append("")

    for rule in ("drift", "link", "frontmatter"):
        subset = [f for f in report.findings if f.rule == rule]
        if not subset:
            continue
        lines.append(f"#### {_RULE_TITLES[rule]}")
        lines.append("")
        lines.append("| Файл | Код | Severity | Находка |")
        lines.append("|---|---|---|---|")
        lines.extend(
            f"| `{f.path}` | `{f.code}` | {f.severity} | {_escape_cell(f.body)} |" for f in subset
        )
        lines.append("")

    lines.append(_footer(pr))
    return "\n".join(lines)


def _footer(pr: int | None) -> str:
    suffix = f" (PR #{pr})" if pr is not None else ""
    return (
        f"<sub>cod-doc `ctx drift --changed-files`{suffix} · "
        f"`model: {MODEL}` · комментарий обновляется на месте.</sub>"
    )


def _escape_cell(text: str) -> str:
    """Свернуть переводы строк и экранировать pipe — иначе поедет таблица."""
    return text.replace("|", "\\|").replace("\n", " ").strip()
