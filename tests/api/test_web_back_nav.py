"""ADO-149: полоса «назад» — куда ведёт и что показывает соседями."""

from __future__ import annotations

from urllib.parse import quote

import pytest
from starlette.requests import Request

from cod_doc.api.web.back_nav import back_nav, natural_key, neighbors

_PAGE = "/p/demo/tasks/T-2"


def _request(*, referer: str | None = None, from_: str | None = None, path: str = _PAGE) -> Request:
    headers = [(b"host", b"testserver")]
    if referer is not None:
        headers.append((b"referer", referer.encode("latin-1")))
    query = f"from={quote(from_, safe='')}" if from_ is not None else ""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "http",
            "server": ("testserver", 80),
            "path": path,
            "query_string": query.encode(),
            "headers": headers,
        }
    )


def _href(key: str) -> str:
    return f"/p/demo/tasks/{key}"


def test_without_source_back_goes_to_the_tab_index() -> None:
    nav = back_nav(_request(), "demo", tab="tasks")
    assert (nav.back.href, nav.back.label, nav.has_context) == ("/p/demo/tasks", "Tasks", False)


def test_referer_list_with_filters_is_kept() -> None:
    nav = back_nav(
        _request(referer="http://testserver/p/demo/tasks?status=todo&plan=web-ui"),
        "demo",
        tab="tasks",
    )
    assert nav.back.href == "/p/demo/tasks?status=todo&plan=web-ui"
    assert nav.back.label == "Tasks (filtered)"
    assert nav.has_context


def test_referer_entity_page_is_the_parent() -> None:
    nav = back_nav(_request(referer="http://testserver/p/demo/stories/US-4"), "demo", tab="tasks")
    assert (nav.back.href, nav.back.label) == ("/p/demo/stories/US-4", "US-4")
    doc = back_nav(_request(referer="http://testserver/p/demo/docs/a/b"), "demo", tab="tasks")
    assert doc.back.label == "a/b"


@pytest.mark.parametrize(
    "referer",
    [
        "https://evil.example/p/demo/tasks",  # чужой хост
        "http://testserver/p/other/tasks",  # другой проект
        "http://testserver/settings",  # вне проекта
        f"http://testserver{_PAGE}",  # сама себя (перезагрузка)
        "http://testserver/p/demo/tasks\\x",  # обратный слэш
    ],
)
def test_foreign_or_self_referer_is_ignored(referer: str) -> None:
    assert back_nav(_request(referer=referer), "demo", tab="tasks").back.href == "/p/demo/tasks"


def test_from_wins_over_referer() -> None:
    nav = back_nav(
        _request(from_="/p/demo/tasks?q=x", referer="http://testserver/p/demo/plans"),
        "demo",
        tab="tasks",
    )
    assert nav.back.href == "/p/demo/tasks?q=x"


@pytest.mark.parametrize(
    "from_",
    [
        "//evil.example/p/demo",
        "https://evil.example/p/demo/tasks",
        "/p/other",
        "x",
        # Префикс проходит, но браузер свернёт `..` в чужой проект.
        "/p/demo/../other/tasks",
        "/p/demo/%2e%2e/other/tasks",
        "/p/demo/./tasks",
    ],
)
def test_from_must_be_a_path_of_this_project(from_: str) -> None:
    nav = back_nav(
        _request(from_=from_, referer="http://testserver/p/demo/plans"), "demo", tab="tasks"
    )
    assert nav.back.href == "/p/demo/tasks"


def test_back_href_is_reencoded() -> None:
    nav = back_nav(
        _request(referer='http://testserver/p/demo/tasks?q="><script>'), "demo", tab="tasks"
    )
    assert "<" not in nav.back.href
    assert '"' not in nav.back.href


def test_neighbors_in_order_and_at_the_edges() -> None:
    keys = ["T-1", "T-2", "T-3"]
    prev, nxt = neighbors(keys, "T-2", _href)
    assert prev is not None and prev.href == "/p/demo/tasks/T-1"
    assert nxt is not None and nxt.label == "T-3"
    assert neighbors(keys, "T-1", _href)[0] is None
    assert neighbors(keys, "T-3", _href)[1] is None
    assert neighbors(keys, "T-9", _href) == (None, None)


def test_steps_carry_the_parent_along() -> None:
    nav = back_nav(
        _request(referer="http://testserver/p/demo/tasks?status=todo"),
        "demo",
        tab="tasks",
        order=["T-1", "T-2", "T-3"],
        current="T-2",
        href=_href,
    )
    assert nav.next is not None
    assert nav.next.href == "/p/demo/tasks/T-3?from=%2Fp%2Fdemo%2Ftasks%3Fstatus%3Dtodo"
    # Пришли с индекса — шагам нечего нести.
    plain = back_nav(
        _request(), "demo", tab="tasks", order=["T-2", "T-3"], current="T-2", href=_href
    )
    assert plain.next is not None and plain.next.href == "/p/demo/tasks/T-3"


def test_natural_key_orders_numbers_as_numbers() -> None:
    assert sorted(["US-10", "US-2", "US-1"], key=natural_key) == ["US-1", "US-2", "US-10"]


def test_neighbour_label_can_differ_from_key() -> None:
    names = {"1": "payments", "2": "web-ui"}
    prev, _ = neighbors(["1", "2"], "2", lambda k: f"/p/demo/plans/{k}", names.__getitem__)
    assert prev is not None
    assert (prev.href, prev.label) == ("/p/demo/plans/1", "payments")


def test_back_bar_renders_nothing_without_nav() -> None:
    from cod_doc.api.web.templates_env import templates

    tpl = templates.get_template("_frag/back_bar.html")
    assert tpl.render(nav=None).strip() == ""
    assert 'class="back-bar"' in tpl.render(nav=back_nav(_request(), "demo", tab="tasks"))


def test_entity_label_names_a_parent_with_an_opaque_key() -> None:
    def labels(tab: str, key: str) -> str | None:
        return "payments" if (tab, key) == ("plans", "7") else None

    nav = back_nav(
        _request(referer="http://testserver/p/demo/plans/7"),
        "demo",
        tab="tasks",
        entity_label=labels,
    )
    assert (nav.back.href, nav.back.label) == ("/p/demo/plans/7", "payments")
    # Неизвестное резолверу — по-прежнему ключ из адреса.
    other = back_nav(
        _request(referer="http://testserver/p/demo/plans/8"),
        "demo",
        tab="tasks",
        entity_label=labels,
    )
    assert other.back.label == "8"


def test_self_referer_with_literal_percent_in_key_is_ignored() -> None:
    # Ключ документа `a%41`: путь запроса уже декодирован, повторный unquote
    # превратил бы его в `aA`, и страница стала бы родителем сама себе.
    nav = back_nav(
        _request(referer="http://testserver/p/demo/docs/a%2541", path="/p/demo/docs/a%41"),
        "demo",
        tab="docs",
    )
    assert nav.back.href == "/p/demo/docs"
