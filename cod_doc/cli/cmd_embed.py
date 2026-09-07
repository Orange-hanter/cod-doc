"""ADO-071: ``cod-doc embed`` — диагностика провайдера эмбеддингов.

Зачем отдельная команда, если раньше её не было: все три потребителя
семантики (ContextService L3, link-suggest, агентские тулы) глушат ошибку по
дизайну — поиск просто отдаёт пустоту. Fail-open без громкого пробника
неотличим от fail-silent, и именно поэтому сломанный эмбеддер жил незамеченным.
``embed status`` отвечает без сети, ``embed probe`` делает один живой вызов.
"""

from __future__ import annotations

import json as json_lib
import sys
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console
from rich.table import Table

from cod_doc.core.embeddings import (
    EmbeddingError,
    settings_from_config,
    supports_catalog,
)
from cod_doc.core.embeddings.catalog import RECOMMENDED_DIMENSIONS, RECOMMENDED_MODEL
from cod_doc.core.embeddings.registry import get_adapter_from_settings

if TYPE_CHECKING:
    from cod_doc.config import Config
    from cod_doc.core.embeddings.settings import EmbeddingSettings

console = Console()


@click.group()
def embed() -> None:
    """Провайдер эмбеддингов: статус, живая проверка, каталог, сброс индекса."""


def _mask(key: str) -> str:
    from cod_doc.api.web.pages._helpers import _masked_api_key

    return _masked_api_key(key)


def _collection_state(cfg: Config, settings: EmbeddingSettings) -> dict[str, Any]:
    """Что лежит в chroma — без создания коллекции и без сетевых вызовов."""
    state: dict[str, Any] = {
        "chroma_path": cfg.chroma_path,
        "exists": False,
        "count": None,
        "stored_signature": None,
    }
    try:
        import chromadb

        from cod_doc.core.reindex import COLLECTION_NAME, SIGNATURE_KEY

        client = chromadb.PersistentClient(path=cfg.chroma_path)
        names = [getattr(c, "name", c) for c in client.list_collections()]
        if COLLECTION_NAME not in names:
            return state
        collection = client.get_collection(COLLECTION_NAME)
        state["exists"] = True
        state["count"] = collection.count()
        state["stored_signature"] = (collection.metadata or {}).get(SIGNATURE_KEY)
    except Exception as exc:  # хранилище недоступно — это тоже диагностика
        state["error"] = str(exc)
    return state


# Провайдеры, у которых чат есть, а эмбеддингов нет вовсе (замерено: их
# /v1/embeddings отдаёт 404 на любой модели). Наследовать у них base_url —
# гарантированно мёртвый поиск.
_NO_EMBEDDINGS_HOSTS = ("ollama.com",)


def _inherit_warning(cfg: Config, settings: EmbeddingSettings) -> str | None:
    """Предупредить, что эмбеддер молча живёт на настройках LLM."""
    if settings.backend != "openai":
        return None
    inherited = not (cfg.embedding_base_url or cfg.embedding_api_key)
    if not inherited:
        return None
    host = settings.base_url or ""
    if any(marker in host for marker in _NO_EMBEDDINGS_HOSTS):
        return (
            f"base_url={host} — у этого провайдера нет маршрута /embeddings, "
            "поиск будет молча пустым. Поставьте embedding_backend=openrouter "
            "и embedding_api_key."
        )
    return (
        "Эмбеддер наследует ключ и endpoint у LLM. Если чат-провайдер не отдаёт "
        "/embeddings — проверьте 'cod-doc embed probe'."
    )


def _verdict(settings: EmbeddingSettings, state: dict[str, Any]) -> tuple[bool, str]:
    if not settings.is_usable:
        return False, (
            f"backend={settings.backend} требует ключ, а embedding_api_key пуст. "
            "Ключ эмбеддера не наследуется от ключа LLM."
        )
    stored = state.get("stored_signature")
    if stored and stored != settings.collection_signature and state.get("count"):
        return False, (
            f"Коллекция построена эмбеддером {stored}, конфиг требует "
            f"{settings.collection_signature}: нужен 'cod-doc embed reset --yes'."
        )
    if not state.get("count"):
        return True, "Настройки валидны; индекс пуст — запустите переиндексацию."
    return True, "Настройки валидны, индекс совпадает с конфигом."


@embed.command("status")
@click.option("--json", "as_json", is_flag=True, help="Машинный вывод")
@click.pass_context
def embed_status(ctx: click.Context, as_json: bool) -> None:
    """Показать разрешённые настройки эмбеддера и состояние индекса (без сети)."""
    cfg: Config = ctx.obj["config"]
    settings = settings_from_config(cfg)
    state = _collection_state(cfg, settings)
    ok, verdict = _verdict(settings, state)

    if as_json:
        payload = {
            "settings": settings.redacted(),
            "signature": settings.collection_signature,
            "collection": state,
            "ok": ok,
            "verdict": verdict,
            "warning": _inherit_warning(cfg, settings),
        }
        console.print_json(json_lib.dumps(payload, ensure_ascii=False))
        sys.exit(0 if ok else 1)

    table = Table(title="Embeddings", show_header=False)
    table.add_row("backend", settings.backend)
    table.add_row("model", settings.model)
    table.add_row("dimensions", str(settings.dimensions or "native"))
    table.add_row("base_url", settings.base_url or "—")
    table.add_row("api key", _mask(settings.api_key) if settings.api_key else "[red]не задан[/red]")
    table.add_row("signature", settings.collection_signature)
    table.add_row("chroma_path", str(state["chroma_path"]))
    table.add_row("collection", "есть" if state["exists"] else "нет")
    table.add_row("vectors", str(state["count"]) if state["count"] is not None else "—")
    table.add_row("stored signature", str(state["stored_signature"] or "—"))
    console.print(table)
    console.print(f"[green]OK[/green] {verdict}" if ok else f"[red]FAIL[/red] {verdict}")
    warning = _inherit_warning(cfg, settings)
    if warning:
        console.print(f"[yellow]Внимание[/yellow]: {warning}")
    if settings.backend != "openrouter" and not settings.is_usable:
        console.print(
            f"[dim]Совет: embedding_backend=openrouter, model={RECOMMENDED_MODEL}, "
            f"dimensions={RECOMMENDED_DIMENSIONS}[/dim]"
        )
    sys.exit(0 if ok else 1)


@embed.command("probe")
@click.option("--text", default="cod-doc embedding probe", show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Машинный вывод")
@click.pass_context
def embed_probe(ctx: click.Context, text: str, as_json: bool) -> None:
    """Один живой вызов эмбеддера: размерность, задержка, стоимость."""
    cfg: Config = ctx.obj["config"]
    settings = settings_from_config(cfg)
    try:
        adapter = get_adapter_from_settings(settings)
        batch = adapter.embed([text])
        result = {
            "ok": True,
            "backend": settings.backend,
            "model": settings.model,
            "dimensions": batch.dimensions,
            "requested_dimensions": settings.dimensions,
            "cost_usd": str(batch.cost_usd),
            "provider": batch.provider,
            "prompt_tokens": batch.prompt_tokens,
        }
    except EmbeddingError as exc:
        result = {"ok": False, "backend": settings.backend, "error": str(exc)}
    except Exception as exc:
        result = {"ok": False, "backend": settings.backend, "error": f"{type(exc).__name__}: {exc}"}

    if as_json:
        console.print_json(json_lib.dumps(result, ensure_ascii=False))
    elif result["ok"]:
        console.print(
            f"[green]OK[/green] {result['model']} → dim={result['dimensions']}"
            f" · cost=${result['cost_usd']}"
            + (f" · provider={result['provider']}" if result["provider"] else "")
        )
        if settings.dimensions and batch.dimensions != settings.dimensions:
            console.print(
                f"[yellow]Внимание[/yellow]: запрошено {settings.dimensions}, "
                f"получено {batch.dimensions} — параметр dimensions не доехал."
            )
    else:
        console.print(f"[red]FAIL[/red] {result['error']}")
    sys.exit(0 if result["ok"] else 1)


@embed.command("models")
@click.option("--json", "as_json", is_flag=True, help="Машинный вывод")
@click.pass_context
def embed_models(ctx: click.Context, as_json: bool) -> None:
    """Каталог моделей эмбеддингов провайдера (у OpenRouter он отдельный)."""
    cfg: Config = ctx.obj["config"]
    settings = settings_from_config(cfg)
    try:
        adapter = get_adapter_from_settings(settings)
    except EmbeddingError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if not supports_catalog(adapter):
        console.print(f"[yellow]backend={settings.backend} не умеет перечислять модели[/yellow]")
        sys.exit(1)

    try:
        entries = adapter.list_models()  # type: ignore[attr-defined]
    except EmbeddingError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    if as_json:
        console.print_json(
            json_lib.dumps(
                [
                    {
                        "model_id": e.model_id,
                        "label": e.label,
                        "context_length": e.context_length,
                        "dimensions": e.dimensions,
                        "usd_per_million": e.prompt_per_million,
                    }
                    for e in entries
                ],
                ensure_ascii=False,
            )
        )
        return

    table = Table(title=f"{settings.backend}: {len(entries)} embedding models")
    table.add_column("model")
    table.add_column("ctx", justify="right")
    table.add_column("dim", justify="right")
    table.add_column("$/1M", justify="right")
    for entry in entries:
        table.add_row(
            entry.model_id,
            str(entry.context_length or "—"),
            str(entry.dimensions or "?"),
            f"{entry.prompt_per_million:.3f}" if entry.prompt_per_million is not None else "—",
        )
    console.print(table)


@embed.command("reset")
@click.option("--yes", is_flag=True, help="Подтвердить удаление коллекции")
@click.option("--force", is_flag=True, help="Удалить даже непустую коллекцию")
@click.pass_context
def embed_reset(ctx: click.Context, yes: bool, force: bool) -> None:
    """Удалить векторную коллекцию — единственный корректный ответ на смену модели.

    Векторы разных моделей несравнимы, автоматически переэмбеддить их нельзя:
    это молча потраченные деньги и время. Поэтому сброс — явный.
    """
    cfg: Config = ctx.obj["config"]
    if not yes:
        console.print("[yellow]Нужен --yes: команда удаляет векторный индекс[/yellow]")
        sys.exit(1)

    import chromadb

    from cod_doc.core.reindex import COLLECTION_NAME, _client_cache

    client = chromadb.PersistentClient(path=cfg.chroma_path)
    names = [getattr(c, "name", c) for c in client.list_collections()]
    if COLLECTION_NAME not in names:
        console.print("[dim]Коллекции нет — нечего сбрасывать[/dim]")
        return

    count = client.get_collection(COLLECTION_NAME).count()
    if count and not force:
        console.print(
            f"[red]В коллекции {count} векторов[/red]. Повторите с --force, "
            "если действительно хотите их потерять."
        )
        sys.exit(1)

    client.delete_collection(COLLECTION_NAME)
    _client_cache.pop(cfg.chroma_path, None)
    console.print(f"[green]Коллекция удалена[/green] ({count} векторов). Переиндексируйте проект.")
