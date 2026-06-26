from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path

DEFAULT_EXCLUDES = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_dsa",
    "id_ed25519",
    "**/node_modules/**",
    "**/.venv/**",
    "**/venv/**",
    "**/.tox/**",
    "**/dist/**",
    "**/build/**",
    "**/.next/**",
    "**/target/**",
    "**/__pycache__/**",
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "poetry.lock",
    "uv.lock",
)

TEXT_EXTENSIONS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".cs",
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".h",
    ".hpp",
    ".rb",
    ".php",
    ".swift",
    ".scala",
    ".sh",
    ".bash",
    ".zsh",
    ".md",
    ".txt",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".sql",
}


def resolve_repo_root(path: str | Path) -> Path:
    candidate = Path(path).expanduser().resolve()
    result = _git(candidate, "rev-parse", "--show-toplevel")
    return Path(result.strip()).resolve()


def repo_id_for(repo_root: Path) -> str:
    remote = _git(repo_root, "config", "--get", "remote.origin.url", check=False).strip()
    return remote or str(repo_root)


def current_commit(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "HEAD").strip()


def tracked_files(repo_root: Path) -> list[str]:
    output = _git(repo_root, "ls-files", "-z")
    return [path for path in output.split("\0") if path and not should_skip(path)]


def iter_text_files(repo_root: Path, paths: Iterable[str]) -> Iterable[tuple[str, str]]:
    for path in paths:
        full_path = repo_root / path
        if not full_path.is_file() or is_binary_or_too_large(full_path):
            continue
        try:
            yield path, full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError:
            continue


def should_skip(path: str) -> bool:
    normalized = path.replace(os.sep, "/")
    name = Path(normalized).name
    if Path(normalized).suffix and Path(normalized).suffix not in TEXT_EXTENSIONS:
        return True
    return any(
        fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in DEFAULT_EXCLUDES
    )


def is_binary_or_too_large(path: Path, max_bytes: int = 1_000_000) -> bool:
    try:
        if path.stat().st_size > max_bytes:
            return True
        sample = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in sample


def _git(repo_root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_root}: {detail}")
    return result.stdout if result.returncode == 0 else ""
