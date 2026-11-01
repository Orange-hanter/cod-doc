"""Сортировка списков веб-UI (WUI-006): один механизм на все страницы.

Порядок задаётся в URL — ``?sort=<key>&dir=asc|desc``, — поэтому ссылкой на
отсортированный список можно поделиться, а htmx-перезагрузка фрагмента его не
теряет, если несёт :attr:`SortState.query`. Страница описывает свои ключи один
раз (:class:`SortSpec`), хендлер разбирает запрос (:func:`sort_state`) и
сортирует каждую группу (:meth:`SortState.apply`); шаблон рисует заголовки и
переключатели макросами из ``_frag/sort.html``.

Правила, общие для всех страниц:

- неизвестный ``sort`` или ``dir`` — дефолт страницы, а не 422: протухшая
  закладка должна открывать список, а не ошибку;
- пустое значение ключа (``None``/``""``) всегда в конце, при любом ``dir``:
  «без даты» не становится «самым свежим» от смены направления;
- равные значения упорядочены тай-брейком страницы (обычно ID) по возрастанию
  при любом ``dir``;
- дефолтные ``sort``/``dir`` в ссылки не пишутся — URL без параметров и URL
  с дефолтом дают одну страницу.

Слой presentation: только сортировка уже прочитанных строк, без SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlencode

from cod_doc.api.web.back_nav import natural_key

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from starlette.requests import Request

ASC: Final = "asc"
DESC: Final = "desc"

__all__ = [
    "ASC",
    "DESC",
    "PRIORITY_RANK",
    "SortKey",
    "SortSpec",
    "SortState",
    "natural_key",
    "parse",
    "rank_of",
    "sort_state",
]

#: Ранг приоритета: critical сверху. Общий для задач, историй и вопросов.
PRIORITY_RANK: Final[dict[str, int]] = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def rank_of(table: Mapping[str, int], value: object) -> int | None:
    """Ранг значения по таблице; неизвестное значение — пустое (в конец)."""
    if value is None:
        return None
    return table.get(str(value))


@dataclass(frozen=True)
class SortKey:
    """Один ключ сортировки страницы.

    ``value`` достаёт из строки сравнимое значение; ``None``/``""`` — пусто.
    ``desc_first`` — направление по первому клику: даты и счётчики
    естественно читать «сначала больше/свежее».
    """

    label: str
    value: Callable[[Any], Any]
    desc_first: bool = False


@dataclass(frozen=True)
class SortSpec:
    """Ключи страницы, ключ по умолчанию и тай-брейк для равных значений."""

    keys: Mapping[str, SortKey]
    default: str
    tiebreak: Callable[[Any], Any]

    def __post_init__(self) -> None:
        if self.default not in self.keys:
            raise ValueError(f"default sort {self.default!r} not among {sorted(self.keys)}")

    def default_dir(self, key: str) -> str:
        return DESC if self.keys[key].desc_first else ASC


def _is_empty(value: object) -> bool:
    return value is None or value == ""


@dataclass(frozen=True)
class SortState:
    """Разобранный запрос сортировки плюс остальные query-параметры страницы."""

    spec: SortSpec
    key: str
    direction: str
    path: str = ""
    params: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_default(self) -> bool:
        return self.key == self.spec.default and self.direction == self.spec.default_dir(self.key)

    def apply(self, rows: Iterable[Any]) -> list[Any]:
        """Отсортировать строки: пустые в конец, равные — по тай-брейку."""
        value = self.spec.keys[self.key].value
        present: list[Any] = []
        empty: list[Any] = []
        for row in rows:
            (empty if _is_empty(value(row)) else present).append(row)
        present.sort(key=self.spec.tiebreak)
        # reverse=True у sorted стабилен: равные остаются в порядке тай-брейка.
        present.sort(key=value, reverse=self.direction == DESC)
        empty.sort(key=self.spec.tiebreak)
        return present + empty

    def _params_for(self, key: str, direction: str) -> dict[str, str]:
        out = {k: v for k, v in self.params.items() if k not in ("sort", "dir")}
        if key != self.spec.default:
            out["sort"] = key
        if direction != self.spec.default_dir(key):
            out["dir"] = direction
        return out

    def href(self, key: str) -> str:
        """Ссылка заголовка/кнопки: свой ключ — сменить направление, чужой —
        перейти на него в естественном направлении."""
        if key == self.key:
            direction = ASC if self.direction == DESC else DESC
        else:
            direction = self.spec.default_dir(key)
        query = urlencode(self._params_for(key, direction))
        return f"{self.path}?{query}" if query else self.path

    @property
    def query(self) -> str:
        """``sort=…&dir=…`` текущего состояния (пусто для дефолта) — для
        URL, которые шаблон собирает руками (htmx-перезагрузка фрагмента)."""
        own = {
            k: v
            for k, v in self._params_for(self.key, self.direction).items()
            if k in ("sort", "dir")
        }
        return urlencode(own)

    def arrow(self, key: str) -> str:
        if key != self.key:
            return ""
        return "▼" if self.direction == DESC else "▲"

    def aria(self, key: str) -> str:
        if key != self.key:
            return "none"
        return "descending" if self.direction == DESC else "ascending"


def parse(spec: SortSpec, sort: str | None, direction: str | None) -> tuple[str, str]:
    """Ключ и направление из сырых параметров; мусор — дефолт страницы."""
    key = sort if sort is not None and sort in spec.keys else spec.default
    if direction is None or direction not in (ASC, DESC):
        return key, spec.default_dir(key)
    return key, direction


def sort_state(request: Request, spec: SortSpec, *, path: str | None = None) -> SortState:
    """Состояние сортировки из ``request.query_params``.

    Остальные параметры запроса (фильтры, ``group_by``, ``view``…) уходят в
    ссылки заголовков как есть: смена сортировки не сбрасывает фильтр.
    ``path`` — адрес страницы, если запрос пришёл на htmx-фрагмент: ссылки
    заголовков в нём должны вести на страницу, а не на фрагмент.
    """
    qp = request.query_params
    key, direction = parse(spec, qp.get("sort"), qp.get("dir"))
    return SortState(
        spec=spec,
        key=key,
        direction=direction,
        path=path or request.url.path,
        params={k: v for k, v in qp.items() if v != ""},
    )
