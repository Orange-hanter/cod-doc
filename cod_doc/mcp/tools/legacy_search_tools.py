"""Semantic search + reindex tools (ChromaDB-backed)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from cod_doc.config import Config

from ._legacy import open_project

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def register(mcp: FastMCP) -> None:
    """Register semantic search tools."""

    @mcp.tool()
    def search_docs(
        project_name: str,
        query: str,
        n_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Semantic search across indexed documentation files."""
        from cod_doc.core.reindex import search_documents

        proj = open_project(project_name)
        cfg = Config.load()
        chroma_path = str(proj.entry.cod_doc_dir / "chroma")
        return search_documents(
            query,
            chroma_path,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            embedding_model=cfg.embedding_model,
            embedding_backend=cfg.embedding_backend,
            project_root=str(proj.entry.root),
            n_results=n_results,
        )

    @mcp.tool()
    def reindex(project_name: str) -> dict[str, Any]:
        """Rebuild the ChromaDB vector index for a project's documentation files."""
        from cod_doc.core.reindex import reindex_project

        proj = open_project(project_name)
        cfg = Config.load()
        chroma_path = str(proj.entry.cod_doc_dir / "chroma")
        return reindex_project(
            proj.entry.root,
            chroma_path,
            api_key=cfg.api_key,
            base_url=cfg.base_url,
            embedding_model=cfg.embedding_model,
            embedding_backend=cfg.embedding_backend,
        )
