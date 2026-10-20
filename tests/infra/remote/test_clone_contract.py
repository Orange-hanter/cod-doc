"""SCN-082 на каждой реализации ``RemoteStore`` (CLO-029): снапшот + хвост → verify.

Строки — те же, что у контракта транспорта (``FACTORIES``): ``file`` всегда,
``s3`` — в джобе ``S3 remote (MinIO)`` (``TEST_S3_BUCKET``), иначе пропуск.
Снапшот, указатель в ``meta.json`` и чтение тела идут через настоящий
транспорт, URL клона — ``<URL remote>/<project_uid>``, как печатает ``sync
status``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from cod_doc.config import Config
from cod_doc.infra.db import db_for_entry, transactional
from cod_doc.infra.models import ProjectModel, TaskModel
from cod_doc.infra.remote.base import open_remote
from cod_doc.services.sync_service import clone, digest, push
from tests.infra.remote.test_remote_contract import remote_url  # noqa: F401 — фикстура
from tests.services.sync import _replicas as r

if TYPE_CHECKING:
    from pathlib import Path


def test_scn_082_clone_through_the_transport(remote_url: str, tmp_path: Path) -> None:  # noqa: F811
    store = open_remote(remote_url, credentials=None)
    a = r.replica_a(tmp_path)
    if remote_url.startswith("http"):
        # Умный remote: членство по uid проекта, а он есть только у созданной реплики.
        from tests.infra.remote._https_server import grant

        with transactional(a.sf) as s:
            grant(s.get_one(ProjectModel, a.project_id).uid)
    r.make_task(a, "DEM-001")
    first = push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    assert first.snapshot == first.head
    r.make_task(a, "DEM-002")
    second = push.push(a.sf, r.SLUG, author=r.AUTHOR, store=store)
    with transactional(a.sf) as s:
        uid = s.get_one(ProjectModel, a.project_id).uid

    result = clone.clone(
        f"{remote_url}/{uid}", root=tmp_path / "demo", author=r.AUTHOR, store=store
    )

    assert (result.snapshot, result.head, result.applied) == (
        first.head,
        second.head,
        [second.head],
    )
    entry = Config.load().get_project(r.SLUG)
    assert entry is not None
    sf = db_for_entry(entry)[0]
    assert digest.verify(sf, r.SLUG, store=store).equal
    with transactional(sf) as s:
        assert sorted(s.scalars(select(TaskModel.task_id))) == ["DEM-001", "DEM-002"]
