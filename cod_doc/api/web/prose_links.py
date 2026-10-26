"""Живые ссылки в прозе задач и историй — ADO-142.

В описаниях, критериях приёмки и итогах закрытия люди и агенты пишут
указатели, которые раньше оставались текстом: ключи сущностей (`ADO-012`,
`US-004`, `ADR-007`), номера PR (`#257`), пути к файлам
(`server/dataQuality.ts`) и голые адреса (`https://…`). Здесь они становятся
ссылками — но только проверенные: битая ссылка хуже текста.

- ключ сущности — если запись есть в проекте (задача открывается шторкой);
- `#N` — если у проекта задан `repo_url` (`cod-doc project repo-url`);
- путь — если файл есть на диске проекта или в индексе репозитория, и задан
  `repo_url`;
- голый адрес — только http(s).

Работа в два шага. `collect` выбирает кандидатов из сырого текста, и одним
запросом на вид проверяется, что из них существует (`ProseLinks.build`).
`apply` переписывает уже отрисованный HTML: трогает только текст между
тегами и не заходит внутрь `<a>`, `<code>`, `<pre>` — ни атрибуты, ни код,
ни готовые ссылки не меняются.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cod_doc.api.web.repo_links import file_url, issue_url
from cod_doc.domain.entities import EntityKind
from cod_doc.services import entity_ref_service, repo_index_service

if TYPE_CHECKING:
    from collections.abc import Iterable

    from sqlalchemy.orm import Session

# Расширения, по которым токен со слэшем или без считается путём к файлу.
_PATH_EXTS = (
    "py|pyi|ts|tsx|js|jsx|mjs|cjs|go|rs|java|kt|rb|php|cs|c|h|cc|cpp|hpp|swift|"
    "sql|sh|toml|yaml|yml|json|md|html|css|scss|vue|svelte|txt|ini|cfg"
)
_ENTITY = r"(?P<entity>(?<![\w-])[A-Z][A-Z0-9]{1,9}-\d{1,5}(?![\w-]))"
# `#N` не после `&` (HTML-сущность `&#39;`), слова, `/` или другого `#`.
_ISSUE = r"(?P<issue>(?<![\w&#/])#(?P<number>\d{1,7})(?!\w))"
# Текст уже экранирован: кавычки и угловые скобки вокруг адреса — сущности
# (`&quot;`, `&#x27;`, `&lt;`), и адрес на них обрывается, а не глотает их.
_URL = r"(?P<url>https?://(?:(?!&(?:quot|lt|gt|#x27|#39);)[^\s<>\"'])+)"
_PATH = r"(?P<path>(?<![\w./-])(?:[\w.-]+/)*[\w-][\w.-]*\.(?:" + _PATH_EXTS + r")(?![\w/-]))"
# Порядок альтернатив важен: адрес раньше пути (путь внутри адреса — часть
# адреса), путь раньше ключа (`docs/ADO-001.md` — файл, а не задача).
_TOKEN = re.compile("|".join((_URL, _PATH, _ENTITY, _ISSUE)))
_ENTITY_ONLY = re.compile(_ENTITY)
_PATH_ONLY = re.compile(_PATH)
_TAG = re.compile(r"(<[^>]*>)")
_SKIP_OPEN = re.compile(r"^<(a|code|pre)\b", re.IGNORECASE)
_SKIP_CLOSE = re.compile(r"^</(a|code|pre)\s*>", re.IGNORECASE)
# Хвостовая пунктуация предложения к адресу не относится.
_URL_TRAIL = ".,;:!?)"

_ENTITY_PATHS = {
    EntityKind.TASK: "tasks",
    EntityKind.STORY: "stories",
    EntityKind.ADR: "adr",
}


def collect(texts: Iterable[str | None]) -> tuple[set[str], set[str]]:
    """Кандидаты из сырого текста: ключи сущностей и пути к файлам."""
    ids: set[str] = set()
    paths: set[str] = set()
    for text in texts:
        if not text:
            continue
        ids.update(m.group("entity") for m in _ENTITY_ONLY.finditer(text))
        paths.update(m.group("path") for m in _PATH_ONLY.finditer(text))
    return ids, paths


def _on_disk(root: Path | None, paths: set[str]) -> set[str]:
    """Пути, которые есть файлами под корнем проекта (без выхода за корень)."""
    if root is None:
        return set()
    try:
        base = root.resolve()
    except OSError:
        return set()
    found = set()
    for rel in paths:
        try:
            target = (base / rel.removeprefix("./")).resolve()
            if target.is_relative_to(base) and target.is_file():
                found.add(rel)
        except OSError:
            continue
    return found


@dataclass(frozen=True)
class ProseLinks:
    """Что в прозе этой карточки можно сделать ссылкой и куда она ведёт."""

    slug: str
    repo_url: str | None = None
    entities: dict[str, EntityKind] = field(default_factory=dict)
    paths: frozenset[str] = frozenset()

    @classmethod
    def build(
        cls,
        session: Session,
        project_id: int,
        *,
        slug: str,
        repo_url: str | None,
        root: Path | None,
        texts: Iterable[str | None],
    ) -> ProseLinks:
        """Проверить кандидатов из `texts`: один запрос на вид сущности."""
        ids, paths = collect(texts)
        entities = entity_ref_service.existing_public_ids(session, project_id, ids)
        known = repo_index_service.indexed_paths(session, project_id, paths) if paths else set()
        known |= _on_disk(root, paths - known)
        return cls(slug=slug, repo_url=repo_url, entities=entities, paths=frozenset(known))

    def apply(self, html: str) -> str:
        """Переписать отрисованный HTML: ссылки в тексте, вне `<a>/<code>/<pre>`."""
        if not html:
            return html
        out: list[str] = []
        skip = 0
        for piece in _TAG.split(html):
            if piece.startswith("<"):
                if _SKIP_OPEN.match(piece) and not piece.endswith("/>"):
                    skip += 1
                elif _SKIP_CLOSE.match(piece) and skip:
                    skip -= 1
                out.append(piece)
            elif skip or not piece:
                out.append(piece)
            else:
                out.append(_TOKEN.sub(self._replace, piece))
        return "".join(out)

    def _replace(self, m: re.Match[str]) -> str:
        if m.group("url"):
            return self._url(m.group("url"))
        if m.group("path"):
            return self._path(m.group("path"))
        if m.group("entity"):
            return self._entity(m.group("entity"))
        return self._issue(m.group("issue"), int(m.group("number")))

    def _url(self, raw: str) -> str:
        url = raw.rstrip(_URL_TRAIL)
        tail = raw[len(url) :]
        return f'<a href="{url}" target="_blank" rel="noopener">{url}</a>{tail}'

    def _path(self, path: str) -> str:
        href = file_url(self.repo_url, None, path) if path in self.paths else None
        if href is None:
            return path
        return (
            f'<a class="ref-link ref-file" href="{href}" target="_blank" rel="noopener">{path}</a>'
        )

    def _entity(self, key: str) -> str:
        kind = self.entities.get(key)
        if kind is None:
            return key
        href = f"/p/{self.slug}/{_ENTITY_PATHS[kind]}/{key}"
        # Задача открывается шторкой (WUI-001); история и ADR — страницей.
        peek = " data-peek" if kind is EntityKind.TASK else ""
        return f'<a class="ref-link ref-{kind.value}" href="{href}"{peek}>{key}</a>'

    def _issue(self, text: str, number: int) -> str:
        href = issue_url(self.repo_url, number)
        if href is None:
            return text
        return f'<a class="ref-link ref-pr" href="{href}" target="_blank" rel="noopener">{text}</a>'
