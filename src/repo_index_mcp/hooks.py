from __future__ import annotations

import re
import shlex
import stat
import subprocess
from pathlib import Path

from repo_index_mcp.repo import resolve_repo_root

HOOK_NAMES = ("post-commit", "post-merge")
COMMAND_RE = re.compile(r"^[A-Za-z0-9_./-]+$")


def install_hooks(
    repo_path: str | Path,
    *,
    command: str = "repo-index",
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
# Auto-installed by repo-index-mcp. Keeps local code retrieval index fresh after git changes.
if command -v {command} >/dev/null 2>&1; then
  {reindex_command} >/dev/null 2>&1 || true
fi
"""


def make_executable(path: Path) -> None:
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
