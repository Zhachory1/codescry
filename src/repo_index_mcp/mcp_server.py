from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from repo_index_mcp.engine import DEFAULT_DB_PATH, RepoIndex


def run_server(*, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError("Install repo-index-mcp with the mcp dependency to run serve") from exc

    engine = RepoIndex(db_path=db_path)
    mcp = FastMCP("repo-index-mcp")

    @mcp.tool()
    def search_code(
        query: str,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
        k: int = 10,
    ) -> list[dict[str, Any]]:
        """Search indexed code and return ranked snippets with file locations."""
        results = engine.query(
            query,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
            k=k,
        )
        return [asdict(result) for result in results]

    @mcp.tool()
    def get_symbol(name: str, repo: str | None = None) -> dict[str, Any] | None:
        """Best-effort symbol lookup backed by search until AST symbols land."""
        results = engine.query(name, repo=repo, k=1)
        if not results:
            return None
        result = results[0]
        return {
            "repo": result.repo,
            "path": result.path,
            "start_line": result.start_line,
            "end_line": result.end_line,
            "definition": result.snippet,
            "score": result.score,
        }

    @mcp.tool()
    def list_repos() -> list[dict[str, Any]]:
        """List indexed repos and freshness state."""
        return engine.list_repos()

    @mcp.tool()
    def reindex(repo_path: str | None = None) -> dict[str, Any]:
        """Reindex a repo. Path optional only when exactly one repo is indexed."""
        return asdict(engine.reindex(repo_path))

    mcp.run()
