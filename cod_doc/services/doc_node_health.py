"""Здоровье разделов дерева документации: чего в корпусе не написано.

Разделение труда между двумя экранами: дерево (ADO-116) отвечает «где лежит»,
этот модуль — «чего не написано». Оба читают один ``doc_node``.

Спецификация не выдумана здесь: это «Критерии живого дерева» из
``cod_doc/skills/doc-structure/SKILL.md``. Скилл их сформулировал, модуль
делает исполняемыми — поэтому правится он вместе со скиллом, а не вместо него.

Модуль чистый в той части, которая решает: :func:`assess_nodes` и
:func:`assess_corpus` берут уже прочитанные данные и не знают про ``Session``.
Сборка данных — в :func:`assess`, запись находок — в вызывающем.

**Инбокс здесь не считается.** «Документ не разложен» уже занимает ранг 6 в
``curator_service`` и отдельную проверку ``doc_unplaced``. Третьего
представления одного факта не заводим — это ровно то, от чего уходили в
ADO-116.
"""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from cod_doc.services import activity_service, finding_service
from cod_doc.services.activity_service import _uuid7
from cod_doc.services.finding_service import FindingSeed

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from cod_doc.services.doc_tree_service import NodeStat

#: Доля самого частого типа, выше которой корпус считается нетипизированным.
#: Скилл формулирует критерий как «весь корпус в `module-spec` — не
#: таксономия», то есть мерится разнообразие по корпусу, а не соответствие
#: документа ожиданиям раздела. Порог выбран по замеру на корпусе cod-doc:
#: 46% `module-spec` при 12 различных типах — типизация посредственная, но
#: живая, и правило обязано на ней молчать. Срабатывает оно на свежем
#: хаотичном импорте, где импортёр проставил один тип всему подряд.
MAX_DOMINANT_TYPE_SHARE = 0.6

#: Сколько одноимённых индексов в одном разделе — уже «пачка безымянных
#: README без роли». На корпусе cod-doc максимум два (`README` и
#: `plugins/cod-doc/README`), и это два разных законных индекса, поэтому порог
#: начинается с трёх.
MAX_INDEX_SIBLINGS = 2

#: Сколько ключей перечислять в теле находки: список нужен, чтобы понять
#: масштаб, а не чтобы читать его целиком.
_KEYS_IN_BODY = 5

#: Имена файлов, которые притворяются индексом каталога.
_INDEX_BASENAMES = frozenset({"readme", "index"})

#: Корпус меньше этого размера ничего не говорит о типизации: три документа
#: одного типа — не вырожденная таксономия, а маленький проект.
_MIN_CORPUS_FOR_TYPE_CHECK = 10

SCOPE_NODE = "doc_node"
SCOPE_PROJECT = "project"


@dataclass(frozen=True, slots=True)
class HealthIssue:
    """Один пробел. ``scope_id`` — ключ раздела либо слаг проекта.

    ``code`` идёт в fingerprint находки, то есть это контракт: переименуешь —
    все открытые находки осиротеют, а вылеченные не закроются.
    """

    code: str
    scope_kind: str
    scope_id: str
    title: str
    body: str
    severity: str = "minor"


def assess_nodes(stats: list[NodeStat]) -> list[HealthIssue]:
    """Пробелы уровня раздела: пусто, тонко, нет намерения.

    Одна находка на раздел, а не на условие: действие у них общее —
    «наполнить раздел», — и по документу на строку очередь куратора уже
    однажды раздували (см. Инбокс, ``curator_service``).
    """
    issues: list[HealthIssue] = []
    for stat in stats:
        node = stat.node
        reasons: list[str] = []
        severity = "minor"

        if node.min_docs > 0 and stat.doc_count == 0:
            reasons.append(f"раздел объявлен обязательным (min_docs={node.min_docs}) и пуст")
            severity = "major"
        elif stat.under_filled:
            reasons.append(f"документов {stat.doc_count} при min_docs={node.min_docs}")
        if not node.intent.strip():
            reasons.append(
                "не заполнен intent — раздел превращается в ярлык, "
                "и следующий разбор Инбокса упрётся в «а куда это»"
            )

        if reasons:
            issues.append(
                HealthIssue(
                    code="NODE-THIN",
                    scope_kind=SCOPE_NODE,
                    scope_id=node.node_key,
                    title=f"Раздел «{node.title}» не наполнен",
                    body="; ".join(reasons),
                    severity=severity,
                )
            )
    return issues


def assess_corpus(
    doc_types: list[str],
    index_docs_by_node: dict[str, list[str]],
    *,
    project_slug: str,
) -> list[HealthIssue]:
    """Пробелы уровня корпуса: типизация и пачки безымянных индексов.

    ``doc_types`` — типы всех документов проекта; ``index_docs_by_node`` —
    ключи документов с именем ``README``/``index``, сгруппированные по разделу.
    """
    issues: list[HealthIssue] = []

    if len(doc_types) >= _MIN_CORPUS_FOR_TYPE_CHECK:
        by_type = collections.Counter(doc_types)
        top_type, top_n = by_type.most_common(1)[0]
        share = top_n / len(doc_types)
        if share > MAX_DOMINANT_TYPE_SHARE:
            issues.append(
                HealthIssue(
                    code="TREE-TYPES",
                    scope_kind=SCOPE_PROJECT,
                    scope_id=project_slug,
                    title="Типы документов не различают корпус",
                    body=(
                        f"{top_n} из {len(doc_types)} документов имеют тип "
                        f"«{top_type}» ({share:.0%}), различных типов всего "
                        f"{len(by_type)}. Обычно это дефолт импортёра, а не "
                        f"решение автора: тип правится через doc import с "
                        f"честным frontmatter."
                    ),
                )
            )

    crowded = {
        node: keys for node, keys in index_docs_by_node.items() if len(keys) > MAX_INDEX_SIBLINGS
    }
    for node, keys in sorted(crowded.items()):
        issues.append(
            HealthIssue(
                code="TREE-README",
                scope_kind=SCOPE_NODE,
                scope_id=node,
                title=f"В разделе «{node}» пачка безымянных индексов",
                body=(
                    f"{len(keys)} документов с именем README/index: "
                    f"{', '.join(sorted(keys)[:_KEYS_IN_BODY])}"
                    + ("…" if len(keys) > _KEYS_IN_BODY else "")
                    + ". Один индекс на каталог, у остальных должна быть роль."
                ),
            )
        )
    return issues


def assess(session: Session, project_id: int, *, project_slug: str) -> list[HealthIssue]:
    """Собрать данные и посчитать все детерминированные пробелы.

    Пустой список означает две разные вещи — «всё хорошо» и «дерева нет»; их
    различает вызывающий через :func:`tree_is_seeded`, как это делает проверка
    ``doc_unplaced``: раскладывать не по чему — это не находка, а состояние
    «фича не заведена».
    """
    from cod_doc.infra.models import DocNodeModel, DocumentModel
    from cod_doc.services import doc_tree_service

    stats = doc_tree_service.node_stats(session, project_id)
    if not stats:
        return []

    node_key_by_id = {
        node.row_id: node.node_key
        for node in session.query(DocNodeModel).filter(DocNodeModel.project_id == project_id)
    }
    docs = session.query(DocumentModel).filter(DocumentModel.project_id == project_id).all()

    index_docs_by_node: dict[str, list[str]] = collections.defaultdict(list)
    for doc in docs:
        if doc.doc_key.rsplit("/", 1)[-1].lower() in _INDEX_BASENAMES:
            # Неразложенный документ уже виден Инбоксом; приписывать его
            # разделу здесь нечестно.
            node_key = node_key_by_id.get(doc.node_id) if doc.node_id is not None else None
            if node_key is not None:
                index_docs_by_node[node_key].append(doc.doc_key)

    return assess_nodes(stats) + assess_corpus(
        [doc.type for doc in docs],
        dict(index_docs_by_node),
        project_slug=project_slug,
    )


def tree_is_seeded(session: Session, project_id: int) -> bool:
    """Заведены ли разделы вообще. Без них любой вердикт бессмыслен."""
    from cod_doc.services import doc_tree_service

    return bool(doc_tree_service.list_nodes(session, project_id))


# --------------------------------------------------------------------------- #
# Запись находок                                                               #
# --------------------------------------------------------------------------- #

#: Словарь ``source`` — контракт RFC 22 §3.2 (``ai_review | zairgrush |
#: routine``), поэтому внутренний производитель приходит как ``routine``, а
#: отличает его ``source_ref``. Он же служит партицией автозакрытия: под одним
#: ``source`` живут разные проверки, и упавшая не вправе закрывать чужое.
FINDING_SOURCE = "routine"
FINDING_SOURCE_REF = "doc_node_health"

DEFAULT_AUTHOR = "routine:doc_node_health"


class SyncResult(NamedTuple):
    """Итог прогона. ``seeded=False`` — дерева нет, сверка не выполнялась."""

    seeded: bool
    issues: int = 0
    created: int = 0
    updated: int = 0
    resolved: int = 0
    reopened: int = 0

    def as_dict(self) -> dict[str, int | bool]:
        return dict(self._asdict())


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


def sync(
    session: Session,
    *,
    project_id: int,
    project_slug: str,
    author: str = DEFAULT_AUTHOR,
) -> SyncResult:
    """Посчитать пробелы и свести их с находками в БД.

    Идемпотентно: повторный прогон поднимает ``times_seen``, а не плодит
    строки (``UNIQUE (project_id, source, fingerprint)``). Вылеченное
    закрывается, вернувшееся переоткрывается — см.
    ``finding_service.reconcile_partition``.

    Дерева нет — ничего не делаем и **не сверяем**: раскладывать не по чему,
    и пустой набор отпечатков закрыл бы все находки как «вылеченные», хотя их
    просто не рассматривали.
    """
    if not tree_is_seeded(session, project_id):
        return SyncResult(seeded=False)

    issues = assess(session, project_id, project_slug=project_slug)
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
    )

    activity_service.emit_for_write(
        session,
        project_id,
        "doc.health_synced",
        author,
        scope_kind="project",
        scope_id=project_slug,
        payload={"issues": len(issues), **ingested.as_dict(), **reconciled},
        summary=f"Doc node health: {len(issues)} issue(s) for {project_slug}",
    )

    return SyncResult(
        seeded=True,
        issues=len(issues),
        created=ingested.created,
        updated=ingested.updated,
        resolved=reconciled["resolved"],
        reopened=reconciled["reopened"],
    )
