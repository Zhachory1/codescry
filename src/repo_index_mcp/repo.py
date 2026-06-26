from __future__ import annotations

import fnmatch
import hashlib
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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
    "node_modules/**",
    "**/node_modules/**",
    ".venv/**",
    "**/.venv/**",
    "venv/**",
    "**/venv/**",
    ".tox/**",
    "**/.tox/**",
    "dist/**",
    "**/dist/**",
    "build/**",
    "**/build/**",
    ".next/**",
    "**/.next/**",
    "target/**",
    "**/target/**",
    "__pycache__/**",
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
    return str(repo_root.resolve())


def remote_url_for(repo_root: Path) -> str:
    remote = _git(repo_root, "config", "--get", "remote.origin.url", check=False).strip()
    return sanitize_remote_url(remote)


def sanitize_remote_url(remote_url: str) -> str:
    parts = urlsplit(remote_url)
    if not parts.scheme:
        if "@" not in remote_url or ":" not in remote_url.rsplit("@", maxsplit=1)[-1]:
            return remote_url
        user, host_path = remote_url.split("@", maxsplit=1)
        return remote_url if user == "git" else host_path
    host = parts.netloc.rsplit("@", maxsplit=1)[-1]
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def current_commit(repo_root: Path) -> str:
    return _git(repo_root, "rev-parse", "HEAD").strip()


def discover_repos(root: str | Path) -> list[Path]:
    root_path = Path(root).expanduser().resolve()
    repos: list[Path] = []
    for current, dirnames, filenames in os.walk(root_path):
        if ".git" in dirnames or ".git" in filenames:
            repos.append(Path(current).resolve())
            dirnames[:] = []
            continue
        dirnames[:] = [name for name in dirnames if not should_prune_dir(name)]
    return sorted(repos)


def tracked_files(repo_root: Path) -> list[str]:
    output = _git(repo_root, "ls-files", "-z")
    return [path for path in output.split("\0") if path and not should_skip(path)]


def committed_files(repo_root: Path, commit_sha: str, max_bytes: int = 1_000_000) -> list[str]:
    output = _git(repo_root, "ls-tree", "-r", "-z", "-l", commit_sha)
    return [
        path
        for path, size in parse_ls_tree(output)
        if size <= max_bytes and not should_skip(path)
    ]


def committed_blob_paths(
    repo_root: Path,
    commit_sha: str,
    paths: Iterable[str],
    max_bytes: int = 1_000_000,
) -> list[str]:
    result: list[str] = []
    for path in paths:
        if should_skip(path):
            continue
        object_ref = f"{commit_sha}:{path}"
        if _git(repo_root, "cat-file", "-t", object_ref, check=False).strip() != "blob":
            continue
        size_text = _git(repo_root, "cat-file", "-s", object_ref, check=False).strip()
        if size_text and int(size_text) <= max_bytes:
            result.append(path)
    return result


def changed_paths_between(
    repo_root: Path,
    old_commit: str,
    new_commit: str,
) -> tuple[list[str], list[str]]:
    output = _git(repo_root, "diff", "--name-status", "-z", old_commit, new_commit)
    parts = [part for part in output.split("\0") if part]
    changed: list[str] = []
    removed: list[str] = []
    index = 0
    while index < len(parts):
        status = parts[index]
        index += 1
        if status.startswith("R"):
            old_path = parts[index]
            new_path = parts[index + 1]
            index += 2
            removed.append(old_path)
            changed.append(new_path)
        else:
            path = parts[index]
            index += 1
            if status.startswith("D"):
                removed.append(path)
            else:
                changed.append(path)
    return changed, removed


def parse_ls_tree(output: str) -> list[tuple[str, int]]:
    blobs: list[tuple[str, int]] = []
    for entry in output.split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", maxsplit=1)
        parts = metadata.split()
        if len(parts) >= 4 and parts[1] == "blob" and parts[3] != "-":
            blobs.append((path, int(parts[3])))
    return blobs


def has_dirty_tracked_files(repo_root: Path) -> bool:
    return bool(_git(repo_root, "status", "--porcelain", "--untracked-files=no").strip())


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


def iter_committed_text_files(
    repo_root: Path,
    commit_sha: str,
    paths: Iterable[str],
) -> Iterable[tuple[str, str]]:
    for path in paths:
        data = _git_bytes(repo_root, "show", f"{commit_sha}:{path}")
        if is_binary_or_too_large_bytes(data):
            continue
        try:
            yield path, data.decode("utf-8")
        except UnicodeDecodeError:
            continue


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def should_prune_dir(name: str) -> bool:
    return name in {
        ".git",
        "node_modules",
        ".venv",
        "venv",
        ".tox",
        "dist",
        "build",
        ".next",
        "target",
        "__pycache__",
    }


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
        size = path.stat().st_size
        if size > max_bytes:
            return True
        sample = path.read_bytes()[:4096]
    except OSError:
        return True
    return is_binary_or_too_large_bytes(sample, max_bytes=max_bytes, full_size=size)


def is_binary_or_too_large_bytes(
    data: bytes,
    *,
    max_bytes: int = 1_000_000,
    full_size: int | None = None,
) -> bool:
    size = len(data) if full_size is None else full_size
    return size > max_bytes or b"\0" in data[:4096]


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


def _git_bytes(repo_root: Path, *args: str, check: bool = True) -> bytes:
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
    )
    if check and result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git {' '.join(args)} failed in {repo_root}: {detail}")
    return result.stdout if result.returncode == 0 else b""
