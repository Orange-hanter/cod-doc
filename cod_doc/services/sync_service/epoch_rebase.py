"""Отставшая реплика стартует со снапшота эпохи (RFC 29 §3.6 п. 3, Q-012; CLO-050).

Changeset'ы прошлой эпохи несут операции в форме уезжающих колонок своей
схемы; на схеме новой эпохи они не воспроизводятся. Поэтому реплика, чья
голова осталась в прошлой эпохе (``cod-doc update`` догнал схему, а pull ещё
не было), при pull **не применяет** changeset'ы между своей головой и
снапшотом эпохи, а кладёт этот снапшот поверх своих строк:

1. **Когда** (``needed``): эпоха changeset'а головы реплики (``changeset.epoch``
   в индексе) старше ``meta.epoch`` remote. Эпоха схемы реплики к этому
   моменту уже не старше ``meta.epoch`` — иначе ``check_pull`` ответил
   ``SYNC-105`` «cod-doc update» раньше.
2. **Хвост** (``refuse_pending``). Неупакованные операции записаны поверх
   состояния прошлой эпохи; снапшот их заменил бы. Молча их не выбрасываем:
   ``SYNC-105`` с выходом ``export-pending`` и новым ``clone`` — тот же, что у
   переписанной истории (§3.6 п. 4).
3. **Снапшот** (``target``): указатель ``meta.json["snapshot"]`` обязан быть в
   эпохе ``meta.epoch`` (его поднимает тот же CAS, что открывает эпоху),
   заголовок — совпасть с указателем. Иначе ``SYNC-102``.
4. **Проверка — как у clone.** Changeset'ы от головы снапшота до уже
   применённых читаются по адресу (``pull._download``: ``decode`` сверяет
   sha, потолки), штампы — ``apply.check_clock``; голова реплики обязана быть
   их предком. В индекс и журнал они ложатся историей (``changeset = sha``),
   как у clone (``snapshot._record_history``): применённым становится только
   достижимое от проверенной головы. Строки снапшота идут через тот же
   ``snapshot.load`` — тот же гейт строк, та же сверка дайджеста загруженного
   с дайджестом changeset'а головы до коммита.
5. **Строки** (``Rebaser``) — поверх своих, в одной транзакции под
   ``suspended``: уезжающая строка, которой в снапшоте нет, удаляется
   (дочерние раньше родителей); есть, но с другими полями — правится на
   месте (``row_id`` сохраняется: на него ссылаются local-строки и ревизии);
   нет у реплики — создаётся, как у clone. Local-строки (restricted-документы
   и всё под ними), local-колонки (``project.root_path``, ``config_json``…) и
   local-таблицы (конфликты, аренды, алиасы ``pinned``) не трогаются.
6. **Changeset прошлой эпохи** в обычном pull (``check_changesets``) —
   ``SYNC-105``, до первой записи: такой changeset поверх истории новой эпохи
   мог записать только клиент без CLO-032, а на реплике без головы он значит
   «заведите реплику через clone». Changeset'ы эпохи, которую remote ещё не
   сменил, проходят: реплика на новой схеме догоняет remote перед тем, как
   открыть эпоху своим push'ем.

Событие ``sync.epoch_rebased`` пишет pull в транзакции замены.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from cod_doc.infra import sync_capture as cap
from cod_doc.infra import sync_registry as reg
from cod_doc.infra.hlc import Hlc, clock_for, self_replica
from cod_doc.infra.models import ChangesetModel, EntityAliasModel
from cod_doc.services.sync_service import _local, apply, epochs, snapshot
from cod_doc.services.sync_service._values import to_wire
from cod_doc.services.sync_service.errors import EpochPassedError, ObjectMissingError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from cod_doc.infra.remote.base import RemoteStore
    from cod_doc.services.sync_service.changeset import Changeset

__all__ = [
    "Rebaser",
    "Target",
    "check_changesets",
    "needed",
    "refuse_pending",
    "replace",
    "target",
]

_PROJECT: Final = "project"
_ROW: Final = "row"
_ROW_LEN: Final = 4
_PAIR_LEN: Final = 2

Key = tuple[str, str]


def needed(session: Session, project_id: int, meta: epochs.RemoteMeta | None) -> str | None:
    """Эпоха головы реплики, если она старше ``meta.epoch``; иначе ``None``.

    Без головы снапшот класть не на что: такую реплику заводит ``clone``, а
    changeset'ы прошлой эпохи ей отказывает ``check_changesets``.
    """
    if meta is None:
        return None
    head = _local.local_head(session, project_id)
    row = session.get(ChangesetModel, head) if head else None
    if row is None:
        return None
    return row.epoch if epochs._newer(meta.epoch, row.epoch) else None


def refuse_pending(project: str, pending: int, head_epoch: str, remote_epoch: str) -> None:
    """Хвост поверх прошлой эпохи снапшот не примет — ``SYNC-105``, ничего не тронуто."""
    if not pending:
        return
    msg = (
        f"{epochs.pending_phrase(pending)} записаны поверх эпохи {head_epoch}, а remote "
        f"перешёл в эпоху {remote_epoch}: снапшот эпохи их не примет — сначала "
        f"cod-doc sync export-pending -p {project}, затем проект заново через cod-doc clone "
        "(операции переносятся руками)"
    )
    raise EpochPassedError(msg)


def check_changesets(
    changesets: Sequence[tuple[str, Changeset]], meta: epochs.RemoteMeta | None
) -> None:
    """Changeset эпохи старше ``meta.epoch`` не применяется (capability §5) — ``SYNC-105``."""
    if meta is None:
        return
    for sha, cs in changesets:
        if epochs._newer(meta.epoch, cs.epoch):
            msg = (
                f"changeset {sha[:12]} эпохи {cs.epoch}, а remote в эпохе {meta.epoch}: "
                "changeset'ы прошлых эпох не применяются (§3.6 п. 3) — реплику без головы "
                "в эпохе remote заводят через cod-doc clone"
            )
            raise EpochPassedError(msg)


@dataclass(frozen=True, slots=True)
class Target:
    """Снапшот эпохи, на который встаёт реплика: голова, тело, заголовок."""

    sha: str
    data: bytes
    header: snapshot.Header


def target(store: RemoteStore, project_uid: str, meta: epochs.RemoteMeta) -> Target:
    """Снапшот эпохи ``meta.epoch`` по указателю ``meta.json``; не тот — ``SYNC-102``."""
    found = snapshot.pointer(store, project_uid)
    if found is None or found[0].epoch != meta.epoch:
        where = "нет" if found is None else f"в эпохе {found[0].epoch}"
        msg = (
            f"снапшот эпохи {meta.epoch}: указатель meta.json {where} — "
            "отставшей реплике не с чего стартовать"
        )
        raise ObjectMissingError(msg)
    ref = found[0]
    data = store.get_snapshot(project_uid, ref)
    header, _project = snapshot.peek(data)
    if (header.project, header.head, header.epoch) != (project_uid, ref.sha, meta.epoch):
        msg = f"снапшот {ref.key}: заголовок {header.project}/{header.head}/{header.epoch}"
        raise ObjectMissingError(msg)
    return Target(sha=ref.sha, data=data, header=header)


@dataclass
class Rebaser(snapshot._Loader):
    """Загрузчик снапшота поверх строк реплики (а не в пустой проект, как у clone)."""

    #: Уезжающие строки реплики после удаления лишних: (таблица, uid) → поля §10.4.
    local: dict[Key, dict[str, object]] = field(default_factory=dict)
    #: Совпали со снапшотом — не трогаются и вторым проходом.
    same: set[Key] = field(default_factory=set)
    #: Правлены на месте: второй проход ставит и ``None`` (у новой строки он уже стоит).
    updated: set[Key] = field(default_factory=set)

    def _shipped(self) -> dict[Key, dict[str, object]]:
        rows = cap.shipped_rows(self.session, self.project.row_id, snapshot.load_order())
        return {
            (table, uid): dict(to_wire(table, json.loads(fields))) for table, uid, fields in rows
        }

    def _find(self, table: str, uid: str, fields: dict[str, object]) -> object | None:
        mapper, info = apply._info(table)
        return apply._find(self.session, self.project, mapper, info, uid, fields)

    def prepare(self, data: bytes) -> None:
        """Удалить уезжающие строки, которых в снапшоте нет: дочерние раньше родителей.

        Журнал (ревизии) не удаляется — он только дописывается; ревизия
        удалённой сущности сама становится local (решение 19 реестра).
        """
        incoming: set[Key] = set()
        for item in snapshot._lines(data):
            if isinstance(item, list) and len(item) == _ROW_LEN and item[0] == _ROW:
                incoming.add((str(item[1]), str(item[2])))
        order = {table: i for i, table in enumerate(snapshot.load_order())}
        before = self._shipped()
        extra = sorted(
            (
                key
                for key in before
                if key not in incoming and key[0] != _PROJECT and key[0] not in reg.JOURNAL_IDS
            ),
            key=lambda key: (-order[key[0]], key[1]),
        )
        for table, uid in extra:
            obj = self._find(table, uid, before[(table, uid)])
            if obj is None:  # уже снесена каскадом родителя
                continue
            apply.note_deleted(self.touched, table, obj)
            self.session.delete(obj)
            self.session.flush()
        # Каскад мог снести и строки, которые снапшот оставляет: сверяемся с тем, что есть.
        self.local = self._shipped()

    def put(self, table: str, uid: str, payload: dict[str, object]) -> None:
        key = (table, uid)
        current = self.local.get(key)
        if current == payload:
            self.same.add(key)
            return
        obj = None if current is None else self._find(table, uid, payload)
        if obj is None:
            super().put(table, uid, payload)
            return
        _mapper, info = apply._info(table)
        apply._require_known_columns(info, payload)
        later = snapshot.deferred_columns()[table]
        first = {c: v for c, v in payload.items() if c not in later}
        for column, value in apply.orm_values(self.session, self.project, info, first, obj).items():
            setattr(obj, info.attr(column), value)
        apply.finish_row(self.session, self.project, info, obj, uid, first)
        apply.note_written(self.touched, table, obj)
        self.updated.add(key)

    def deferred(self, table: str, uid: str, fields: dict[str, object]) -> dict[str, object]:
        key = (table, uid)
        if key in self.same:
            return {}
        if key in self.updated:
            later = snapshot.deferred_columns().get(table, frozenset())
            return {c: v for c, v in fields.items() if c in later}
        return super().deferred(table, uid, fields)

    def alias(self, item: list[object]) -> None:
        """Алиас, который реплика уже знает, принимается, только если он тот же."""
        body = item[1] if len(item) == _PAIR_LEN else None
        if isinstance(body, dict):
            existing = self.session.scalars(
                select(EntityAliasModel).where(
                    EntityAliasModel.project_id == self.project.row_id,
                    EntityAliasModel.entity_kind == body.get("entity_kind"),
                    EntityAliasModel.alias == body.get("alias"),
                )
            ).one_or_none()
            if existing is not None:
                if (existing.uid, existing.reason) != (body.get("uid"), body.get("reason")):
                    msg = f"алиас {existing.entity_kind} {existing.alias}: на реплике → {existing.uid}"
                    raise snapshot._bad(msg)
                return
        super().alias(item)


def replace(
    session: Session,
    project: _local.ProjectRef,
    tgt: Target,
    history: Sequence[tuple[str, Changeset]],
) -> snapshot.LoadResult:
    """История до головы снапшота — в журнал и индекс, строки — со снапшота (транзакция вызывающего).

    ``history`` — проверенные changeset'ы от головы снапшота до применённых, родитель
    раньше ребёнка. Расхождение дайджеста — ``SYNC-102`` до коммита.
    """
    head_cs = dict(history).get(tgt.sha)
    if head_cs is None:
        msg = f"снапшот {tgt.sha[:12]}: changeset головы не прочитан"
        raise ObjectMissingError(msg)
    top: Hlc | None = None
    try:
        for sha, cs in history:
            snapshot._record_history(session, project, sha, cs)
            if cs.ops:
                stamp = Hlc.parse(cs.hlc_max)
                top = stamp if top is None or stamp > top else top
    except IntegrityError as exc:
        msg = f"история эпохи: операция уже в журнале реплики: {exc.orig}"
        raise ObjectMissingError(msg) from exc
    if top is not None:
        clock_for(session, self_replica(session, project.row_id)).observe(top)
    return snapshot.load(
        session, project, tgt.data, head=tgt.sha, head_cs=head_cs, loader_cls=Rebaser
    )
