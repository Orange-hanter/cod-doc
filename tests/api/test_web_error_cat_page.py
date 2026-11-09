"""Страница 403/404/415 с котом у двери — только для навигации браузера."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

if TYPE_CHECKING:
    from collections.abc import Iterator

HTML = {"accept": "text/html,application/xhtml+xml"}
_FORBIDDEN_PATH = "/__test_forbidden"
_MEDIA_PATH = "/__test_unsupported"


@pytest.fixture
def client() -> Iterator[TestClient]:
    from cod_doc.api.server import app

    @app.get(_FORBIDDEN_PATH)
    def _forbidden() -> None:
        raise HTTPException(403, "Только для владельцев")

    @app.get(_MEDIA_PATH)
    def _unsupported() -> None:
        raise HTTPException(415)

    try:
        with TestClient(app) as c:
            yield c
    finally:
        app.router.routes[:] = [
            r
            for r in app.router.routes
            if getattr(r, "path", "") not in {_FORBIDDEN_PATH, _MEDIA_PATH}
        ]


def test_unknown_page_renders_cat_404(client: TestClient) -> None:
    r = client.get("/no-such-page", headers=HTML)
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("text/html")
    assert "за дверью никого" in r.text
    assert 'class="leg leg-a"' in r.text
    # Дефолтная фраза «Not Found» не дублирует заголовок.
    assert "Not Found" not in r.text


def test_forbidden_renders_locked_door_with_detail(client: TestClient) -> None:
    r = client.get(_FORBIDDEN_PATH, headers=HTML)
    assert r.status_code == 403
    assert "сюда не пускают" in r.text
    assert "Только для владельцев" in r.text
    assert 'class="gap-light"' in r.text  # за запертой дверью горит свет


def test_unsupported_media_renders_radio_meme(client: TestClient) -> None:
    r = client.get(_MEDIA_PATH, headers=HTML)
    assert r.status_code == 415
    assert "база, ответьте" in r.text
    assert 'class="radio-led"' in r.text
    assert "Unsupported Media Type" not in r.text


@pytest.mark.parametrize(
    ("path", "headers"),
    [
        ("/api/no-such", HTML),  # API остаётся JSON даже из браузера
        ("/no-such-page", {"accept": "application/json"}),
        ("/no-such-page", {**HTML, "HX-Request": "true"}),
    ],
)
def test_machine_clients_keep_json(client: TestClient, path: str, headers: dict[str, str]) -> None:
    r = client.get(path, headers=headers)
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}
