#!/usr/bin/env python3
"""Разбить graphify-граф на партиции по префиксу пути.

Зачем. Продюсер ai-reviewer режет факты по лимитам консьюмера
(``MAX_ENTITIES``/``MAX_EDGES``) и ставит ``provenance.truncated``. Усечённый
снапшот неполон даже в собственных границах, поэтому закрывать находки не
вправе — на репозитории размера cod-doc находки не закрывались бы никогда.
Разбиение снимает причину: каждая партиция целиком влезает в лимиты и потому
полна внутри себя.

Почему не просто ``graphify update <подкаталог>``: graphify пишет
``source_file`` относительно корня сканирования, и продюсер отбрасывает такие
пути как выходящие за ``--repo-root``. Резать нужно уже готовый граф корня, где
пути репозиторно-относительные.

Правила разбиения:

* узел принадлежит первой партиции, чей префикс совпал с ``source_file``;
* ребро принадлежит партиции своего ``source`` — так каждое ребро попадает
  ровно в одну партицию, ничего не теряется и не дублируется;
* узлы, не подошедшие ни под один префикс, уезжают в партицию ``rest``, чтобы
  покрытие оставалось полным.

Использование::

    python scripts/split_graph.py graph.json out/ --prefix cod_doc/services \\
        --prefix cod_doc/cli

Для каждой партиции пишется ``<out>/<name>/graph.json``; имя партиции —
префикс со слэшами, заменёнными на ``-``. Дальше по партиции на вызов::

    node pr-review-structure.mjs --repo-root <repo> \\
        --graph <out>/<name>/graph.json --scope <prefix> --facts-out ...
    cod-doc ingest structure -p <project> --facts <facts-1> --facts <facts-2>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REST = "rest"


def partition_name(prefix: str) -> str:
    return prefix.strip("/").replace("/", "-") or REST


def split(graph: dict[str, object], prefixes: list[str]) -> dict[str, dict[str, object]]:
    nodes = list(graph.get("nodes") or [])
    links = list(graph.get("links") or [])

    owner: dict[str, str] = {}
    buckets: dict[str, list[dict[str, object]]] = {partition_name(p): [] for p in prefixes}
    buckets[REST] = []

    for node in nodes:
        if not isinstance(node, dict):
            continue
        source = str(node.get("source_file") or "")
        name = REST
        for prefix in prefixes:
            if source.startswith(prefix):
                name = partition_name(prefix)
                break
        owner[str(node.get("id"))] = name
        buckets[name].append(node)

    link_buckets: dict[str, list[dict[str, object]]] = {name: [] for name in buckets}
    for link in links:
        if not isinstance(link, dict):
            continue
        # Ребро живёт в партиции своего источника: ровно одна партиция на ребро.
        link_buckets[owner.get(str(link.get("source")), REST)].append(link)

    out: dict[str, dict[str, object]] = {}
    for name, node_rows in buckets.items():
        # Пустую партицию пропускаем, но только если в ней нет и рёбер: у ребра
        # с висячим ``source`` (узла нет в графе) владельцем становится ``rest``,
        # и молчаливый пропуск такого бакета терял бы рёбра — вопреки
        # заявленной неразрушающей нарезке.
        if not node_rows and not link_buckets[name]:
            continue
        out[name] = {
            **{k: v for k, v in graph.items() if k not in {"nodes", "links"}},
            "nodes": node_rows,
            "links": link_buckets[name],
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("graph", type=Path, help="graph.json корня репозитория")
    parser.add_argument("out_dir", type=Path, help="куда писать <name>/graph.json")
    parser.add_argument(
        "--prefix",
        action="append",
        default=[],
        help="префикс пути партиции; повторяемый. Порядок задаёт приоритет.",
    )
    args = parser.parse_args(argv)

    if not args.prefix:
        parser.error("нужен хотя бы один --prefix")
    blank = [value for value in args.prefix if not value.strip("/").strip()]
    if blank:
        parser.error(
            "пустой --prefix совпадает с любым путём и схлопнул бы граф "
            f"в одну партицию {REST!r}"
        )

    graph = json.loads(args.graph.read_text(encoding="utf-8"))
    parts = split(graph, list(args.prefix))
    for name, payload in sorted(parts.items()):
        target = args.out_dir / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "graph.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{name}: nodes={len(payload['nodes'])} links={len(payload['links'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
