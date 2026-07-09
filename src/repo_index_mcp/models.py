from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    repo_id: str
    repo_path: str
    path: str
    language: str
    symbol_name: str | None
    symbol_kind: str | None
    symbol_line: int | None
    symbol_confidence: str | None
    start_line: int
    end_line: int
    content: str


@dataclass(frozen=True)
class SearchResult:
    repo: str
    path: str
    start_line: int
    end_line: int
    snippet: str
    score: float
    language: str
    symbol_name: str | None = None
    symbol_kind: str | None = None
    symbol_line: int | None = None
    symbol_confidence: str | None = None
    is_stale: bool = False
    has_dirty_tracked_files: bool = False


@dataclass(frozen=True)
class IndexResult:
    repo_id: str
    repo_path: str
    commit_sha: str
    files_indexed: int
    chunks_indexed: int
    duration_ms: int
    files_changed: int = 0
    files_removed: int = 0
    files_skipped: int = 0
    files_ignored: int = 0
    chunks_skipped: int = 0
    chunks_total: int = 0
    error_count: int = 0
    last_error: str | None = None
