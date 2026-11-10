from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from repo_index_mcp import __version__
from repo_index_mcp.embeddings import HashEmbeddingProvider, embedding_provider_from_env
from repo_index_mcp.hooks import inspect_hooks, summarize_hook_status
from repo_index_mcp.storage import SQLiteStorage


def run_doctor(db_path: str | Path) -> tuple[dict[str, Any], int]:
    db = Path(db_path).expanduser()
    checks = {
        "git": check_git(),
        "db_writable": check_db_writable(db),
        "mcp_dependency": check_importable("mcp"),
    }
    repos: list[dict[str, object]] = []
    repo_count = None
    if checks["db_writable"]["ok"]:
        try:
            repos = SQLiteStorage(db).list_repos()
            repo_count = len(repos)
            for repo in repos:
                try:
                    repo["freshness_hooks"] = inspect_hooks(str(repo["repo_path"]))
                except Exception as exc:
                    repo["freshness_hooks"] = {
                        "installed": False,
                        "missing": [],
                        "non_codescry": [],
                        "non_executable": [],
                        "error": str(exc),
                    }
        except Exception as exc:
            checks["db_writable"] = {"ok": False, "detail": str(exc)}

    provider = embedding_provider_from_env()
    result: dict[str, Any] = {
        "ok": all(check["ok"] for check in checks.values()),
        "version": __version__,
        "python": sys.version.split()[0],
        "db_path": str(db),
        "repo_count": repo_count,
        "freshness_hooks": summarize_hook_status(repos),
        "embedding_provider": {
            "selected": provider.model_id,
            "semantic": not isinstance(provider, HashEmbeddingProvider),
            "recommendation": None
            if not isinstance(provider, HashEmbeddingProvider)
            else "ollama pull mxbai-embed-large",
        },
        "checks": checks,
    }
    return result, 0 if result["ok"] else 1


def check_git() -> dict[str, Any]:
    path = shutil.which("git")
    return {"ok": path is not None, "detail": path or "git not found on PATH"}


def check_importable(module: str) -> dict[str, Any]:
    importable = importlib.util.find_spec(module) is not None
    return {"ok": importable, "detail": "importable" if importable else f"{module} not importable"}


def check_db_writable(db_path: Path) -> dict[str, Any]:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with db_path.open("ab"):
            pass
        if not os.access(db_path, os.W_OK):
            return {"ok": False, "detail": "database path is not writable"}
        return {"ok": True, "detail": "writable"}
    except Exception as exc:
        return {"ok": False, "detail": str(exc)}
