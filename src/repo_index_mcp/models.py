from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Chunk:
    repo_id: str
    repo_path: str
    path: str
    language: str
    symbol_name: str | None
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


@dataclass(frozen=True)
class IndexResult:
    repo_id: str
    repo_path: str
    commit_sha: str
    files_indexed: int
    chunks_indexed: int
    duration_ms: int
