"""Настоящий HTTP-сервер умного remote для контрактных тестов (CLO-035).

Один uvicorn в фоновом потоке на процесс: маршруты ``/api/sync/v1`` за тем же
Bearer-гейтом, что у ``cod-doc serve``, без остального веб-приложения (его
lifespan поднимает демона агента). Хранилище доступа и объекты сервер берёт
из ``COD_DOC_HOME`` запроса — его на каждый тест подменяет autouse-фикстура,
поэтому тесты не делят ни токены, ни объекты.
"""

from __future__ import annotations

import socket
import threading
import time
from typing import Final

#: Совпадает с ``tests/services/sync/_replicas.AUTHOR`` (``human:dakh``): push реплики
#: из контракта клона уходит под этим токеном и проходит проверку author (CLO-037).
ACTOR: Final = "dakh"
_STARTED: dict[str, str] = {}
_LOCK = threading.Lock()


def _app() -> object:
    from fastapi import FastAPI

    from cod_doc.api.auth import BearerGate, RateLimiter, is_web_protected_path
    from cod_doc.api.sync import router

    app = FastAPI()
    app.include_router(router)
    # Гонка CAS контракта — ~1000 запросов одного токена за минуту; лимит гейта
    # (600) проверяется в tests/api/test_bearer_gate.py, здесь он мешал бы.
    app.add_middleware(BearerGate, protected=is_web_protected_path, requests=RateLimiter(1_000_000))
    return app


def base_url() -> str:
    """URL сервера; первый вызов поднимает его."""
    import uvicorn

    with _LOCK:
        if "url" in _STARTED:
            return _STARTED["url"]
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = uvicorn.Config(_app(), host="127.0.0.1", port=port, log_level="warning")  # type: ignore[arg-type]
        server = uvicorn.Server(config)
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                msg = "сервер умного remote не поднялся за 10 с"
                raise RuntimeError(msg)
            time.sleep(0.02)
        _STARTED["url"] = f"http://127.0.0.1:{port}"
        return _STARTED["url"]


def grant(project_uid: str) -> None:
    """Дать владельцу токена admin в ещё одном проекте (uid реплики известен поздно)."""
    from cod_doc.infra.db import transactional
    from cod_doc.services import access_service

    factory = access_service.open_store()
    assert factory is not None
    with transactional(factory) as s:
        access_service.add_member(
            s, handle=ACTOR, project_uid=project_uid, role="admin", author="human:root"
        )


def provision(project_uids: tuple[str, ...]) -> str:
    """Хранилище доступа в текущем ``COD_DOC_HOME``, ``alice`` — admin проектов, её токен
    в файле кредов. Вернуть URL remote."""
    import yaml

    from cod_doc.config import config_dir
    from cod_doc.infra.db import transactional
    from cod_doc.services import access_service

    url = base_url()
    access_service.init_store()
    factory = access_service.open_store()
    assert factory is not None
    with transactional(factory) as s:
        for uid in project_uids:
            access_service.add_member(
                s, handle=ACTOR, project_uid=uid, role="admin", author="human:root"
            )
        token = access_service.create_token(s, handle=ACTOR, label="contract", author="human:root")
    creds = config_dir() / "credentials"
    creds.write_text(yaml.safe_dump({url: {"token": token.token}}))
    creds.chmod(0o600)
    return url
