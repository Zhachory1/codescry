from __future__ import annotations

import time
from pathlib import Path

from repo_index_mcp.chunking import LineChunker
from repo_index_mcp.embeddings import EmbeddingProvider, HashEmbeddingProvider
from repo_index_mcp.models import IndexResult, SearchResult
from repo_index_mcp.repo import (
    current_commit,
    iter_text_files,
    repo_id_for,
    resolve_repo_root,
    tracked_files,
)
from repo_index_mcp.storage import SQLiteStorage

DEFAULT_DB_PATH = Path.home() / ".repo-index-mcp" / "index.sqlite"


class RepoIndex:
    def __init__(
        self,
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        embedding_provider: EmbeddingProvider | None = None,
        chunker: LineChunker | None = None,
    ) -> None:
        self.storage = SQLiteStorage(db_path)
        self.embedding_provider = embedding_provider or HashEmbeddingProvider()
        self.chunker = chunker or LineChunker()

    def index_repo(self, repo_path: str | Path) -> IndexResult:
        start = time.monotonic()
        repo_root = resolve_repo_root(repo_path)
        repo_id = repo_id_for(repo_root)
        commit_sha = current_commit(repo_root)
        paths = tracked_files(repo_root)
        files = list(iter_text_files(repo_root, paths))
        chunks = self.chunker.chunk_files(repo_id=repo_id, repo_path=str(repo_root), files=files)
        embeddings = [self.embedding_provider.embed(chunk.content) for chunk in chunks]

        self.storage.upsert_repo(repo_id=repo_id, repo_path=str(repo_root), commit_sha=commit_sha)
        chunks_indexed = self.storage.replace_chunks(
            repo_id=repo_id,
            chunks=chunks,
            embeddings=embeddings,
            commit_sha=commit_sha,
            embedding_model=self.embedding_provider.model_id,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return IndexResult(
            repo_id=repo_id,
            repo_path=str(repo_root),
            commit_sha=commit_sha,
            files_indexed=len(files),
            chunks_indexed=chunks_indexed,
            duration_ms=duration_ms,
        )

    def query(
        self,
        query: str,
        *,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
        k: int = 10,
    ) -> list[SearchResult]:
        query_embedding = self.embedding_provider.embed(query)
        return self.storage.search(
            query_embedding=query_embedding,
            embedding_model=self.embedding_provider.model_id,
            k=k,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
        )

    def list_repos(self) -> list[dict[str, object]]:
        repos = self.storage.list_repos()
        for repo in repos:
            try:
                repo["is_stale"] = (
                    current_commit(Path(str(repo["repo_path"]))) != repo["last_commit_sha"]
                )
            except Exception:
                repo["is_stale"] = True
        return repos

    def reindex(self, repo_path: str | Path | None = None) -> IndexResult:
        if repo_path is not None:
            return self.index_repo(repo_path)

        repo_paths = self.storage.repo_paths()
        if len(repo_paths) != 1:
            raise ValueError("repo_path is required unless exactly one repo is indexed")
        return self.index_repo(repo_paths[0])
