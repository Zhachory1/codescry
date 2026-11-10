from __future__ import annotations

import re
import shlex
import stat
import subprocess
from pathlib import Path

from repo_index_mcp.repo import resolve_repo_root

HOOK_NAMES = ("post-commit", "post-merge")
COMMAND_RE = re.compile(r"^[A-Za-z0-9_./-]+$")
CODESCRY_HOOK_MARKER = "Auto-installed by CodeScry"


def install_hooks(
    repo_path: str | Path,
    *,
    command: str = "codescry",
    db_path: str | Path | None = None,
    force: bool = False,
) -> list[Path]:
    if not COMMAND_RE.fullmatch(command):
        raise ValueError("command must be an executable name or path without shell metacharacters")
    repo_root = resolve_repo_root(repo_path)
    installed: list[Path] = []
    for hook_name in HOOK_NAMES:
        hook_path = git_hook_path(repo_root, hook_name)
        if hook_path.exists() and not force:
            continue
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(hook_script(command, db_path=db_path), encoding="utf-8")
        make_executable(hook_path)
        installed.append(hook_path)
    return installed


def git_hook_path(repo_root: Path, hook_name: str) -> Path:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--git-path", f"hooks/{hook_name}"],
        check=True,
        capture_output=True,
        text=True,
    )
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else repo_root / path


def hook_script(command: str, *, db_path: str | Path | None = None) -> str:
    db_args = ""
    if db_path is not None:
        resolved_db_path = Path(db_path).expanduser().resolve()
        db_args = f" --db {shlex.quote(str(resolved_db_path))}"
    reindex_command = f"{command}{db_args} reindex \"$PWD\""
    return f"""#!/bin/sh
# {CODESCRY_HOOK_MARKER}. Keeps local code retrieval index fresh after git changes.
if command -v {command} >/dev/null 2>&1; then
  {reindex_command} >/dev/null 2>&1 || true
fi
"""


def inspect_hooks(repo_path: str | Path) -> dict[str, object]:
    repo_root = resolve_repo_root(repo_path)
    present: list[str] = []
    missing: list[str] = []
    non_codescry: list[str] = []
    non_executable: list[str] = []
    paths: dict[str, str] = {}

    for hook_name in HOOK_NAMES:
        hook_path = git_hook_path(repo_root, hook_name)
        paths[hook_name] = str(hook_path)
        if not hook_path.exists():
            missing.append(hook_name)
            continue
        present.append(hook_name)
        text = hook_path.read_text(encoding="utf-8", errors="ignore")
        if not hook_has_codescry_reindex(text):
            non_codescry.append(hook_name)
        if not (hook_path.stat().st_mode & stat.S_IXUSR):
            non_executable.append(hook_name)

    return {
        "installed": not missing and not non_codescry and not non_executable,
        "present": present,
        "missing": missing,
        "non_codescry": non_codescry,
        "non_executable": non_executable,
        "paths": paths,
    }


def summarize_hook_status(repos: list[dict[str, object]]) -> dict[str, object]:
    missing_repos: list[dict[str, object]] = []
    installed = 0
    for repo in repos:
        hooks = repo.get("freshness_hooks", {})
        if isinstance(hooks, dict) and hooks.get("installed") is True:
            installed += 1
            continue
        missing_repos.append(
            {
                "repo_path": repo.get("repo_path"),
                "missing": hooks.get("missing", []) if isinstance(hooks, dict) else [],
                "non_codescry": hooks.get("non_codescry", []) if isinstance(hooks, dict) else [],
                "non_executable": (
                    hooks.get("non_executable", []) if isinstance(hooks, dict) else []
                ),
                "error": (
                    hooks.get("error") if isinstance(hooks, dict) else "hook status unavailable"
                ),
            }
        )
    return {
        "repos_checked": len(repos),
        "installed": installed,
        "missing": len(missing_repos),
        "missing_repos": missing_repos,
    }


def hook_has_codescry_reindex(text: str) -> bool:
    lowered = text.lower()
    return (
        CODESCRY_HOOK_MARKER.lower() in lowered or "codescry" in lowered
    ) and "reindex" in lowered


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
