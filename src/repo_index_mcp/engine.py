from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path

from repo_index_mcp.chunking import LineChunker
from repo_index_mcp.embeddings import EmbeddingProvider, HashEmbeddingProvider
from repo_index_mcp.models import IndexResult, SearchResult
from repo_index_mcp.repo import (
    changed_paths_between,
    committed_blob_paths,
    committed_files,
    content_hash,
    current_commit,
    discover_repos,
    has_dirty_tracked_files,
    iter_committed_text_files,
    remote_url_for,
    repo_id_for,
    resolve_repo_root,
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
        repo_path_str = str(repo_root)
        remote_url = remote_url_for(repo_root)
        expected_model = self.embedding_provider.model_id
        chunker_version = self.chunker.version
        commit_sha = ""
        try:
            commit_sha = current_commit(repo_root)
            self.storage.cleanup_repo_path_aliases(repo_id=repo_id, repo_path=repo_path_str)
            stored_state = self.storage.indexed_file_state(repo_id=repo_id)
            prior_commit = self.storage.repo_commit(repo_id=repo_id)
            if not stored_state and self.storage.chunk_count(repo_id=repo_id):
                self.storage.clear_repo(repo_id=repo_id)

            model_or_chunker_changed = any(
                state[1:] != (expected_model, chunker_version) for state in stored_state.values()
            )
            needs_full_scan = not prior_commit or not stored_state or model_or_chunker_changed
            changed_paths: list[str] = []
            if needs_full_scan:
                paths = committed_files(repo_root, commit_sha)
                files = list(iter_committed_text_files(repo_root, commit_sha, paths))
                current_hashes = {
                    path: content_hash(file_content) for path, file_content in files
                }
                removed_paths = sorted(set(stored_state) - set(current_hashes))
                files_indexed = len(files)
            else:
                changed_paths, removed_paths = (
                    ([], [])
                    if prior_commit == commit_sha
                    else changed_paths_between(repo_root, prior_commit, commit_sha)
                )
                paths = committed_blob_paths(repo_root, commit_sha, changed_paths)
                files = list(iter_committed_text_files(repo_root, commit_sha, paths))
                current_hashes = {
                    path: content_hash(file_content) for path, file_content in files
                }
                ineligible_changed_paths = (
                    (set(changed_paths) & set(stored_state)) - set(current_hashes)
                )
                removed_paths = sorted(set(removed_paths) | ineligible_changed_paths)
                files_indexed = len(stored_state) - len(removed_paths) + sum(
                    1 for path, _content in files if path not in stored_state
                )
        except Exception as exc:
            last_error = f"read committed snapshot: {exc}"
            self.storage.record_repo_failure(
                repo_id=repo_id,
                repo_path=repo_path_str,
                remote_url=remote_url,
                last_error=last_error,
                error_count=1,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            return IndexResult(
                repo_id=repo_id,
                repo_path=repo_path_str,
                commit_sha=commit_sha,
                files_indexed=0,
                chunks_indexed=0,
                duration_ms=duration_ms,
                error_count=1,
                last_error=last_error,
            )

        changed_files = [
            (path, file_content)
            for path, file_content in files
            if stored_state.get(path) != (current_hashes[path], expected_model, chunker_version)
        ]

        errors: list[str] = []
        chunks_indexed = 0
        try:
            self.storage.delete_paths(repo_id=repo_id, paths=removed_paths)
        except Exception as exc:
            errors.append(f"delete removed paths: {exc}")

        for path, file_content in changed_files:
            try:
                chunks = self.chunker.chunk_file(
                    repo_id=repo_id,
                    repo_path=repo_path_str,
                    path=path,
                    content=file_content,
                )
                embeddings = [self.embedding_provider.embed(chunk.content) for chunk in chunks]
                chunks_indexed += self.storage.replace_file_chunks(
                    repo_id=repo_id,
                    path=path,
                    content_hash=current_hashes[path],
                    chunks=chunks,
                    embeddings=embeddings,
                    commit_sha=commit_sha,
                    embedding_model=expected_model,
                    chunker_version=chunker_version,
                )
            except Exception as exc:
                errors.append(f"{path}: {exc}")

        last_error = "; ".join(errors) if errors else None
        if errors:
            self.storage.record_repo_failure(
                repo_id=repo_id,
                repo_path=repo_path_str,
                remote_url=remote_url,
                last_error=last_error or "index failed",
                error_count=len(errors),
            )
        else:
            self.storage.record_repo_success(
                repo_id=repo_id,
                repo_path=repo_path_str,
                remote_url=remote_url,
                commit_sha=commit_sha,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        return IndexResult(
            repo_id=repo_id,
            repo_path=repo_path_str,
            commit_sha=commit_sha,
            files_indexed=files_indexed,
            chunks_indexed=chunks_indexed,
            duration_ms=duration_ms,
            files_changed=len(changed_files),
            files_removed=len(removed_paths),
            chunks_total=self.storage.chunk_count(repo_id=repo_id),
            error_count=len(errors),
            last_error=last_error,
        )

    def index_root(self, root: str | Path) -> list[IndexResult]:
        results: list[IndexResult] = []
        for repo_path in discover_repos(root):
            try:
                results.append(self.index_repo(repo_path))
            except Exception as exc:
                repo_path_str = str(Path(repo_path).expanduser().resolve())
                results.append(
                    IndexResult(
                        repo_id=repo_path_str,
                        repo_path=repo_path_str,
                        commit_sha="",
                        files_indexed=0,
                        chunks_indexed=0,
                        duration_ms=0,
                        error_count=1,
                        last_error=str(exc),
                    )
                )
        return results

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
        results = self.storage.search(
            query_embedding=query_embedding,
            embedding_model=self.embedding_provider.model_id,
            k=k,
            query_text=query,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
        )
        repo_ids = {result.repo for result in results}
        repo_state = {str(item["repo_id"]): item for item in self._repo_status(repo_ids)}
        return [
            replace(
                result,
                is_stale=bool(repo_state.get(result.repo, {}).get("is_stale", True)),
                has_dirty_tracked_files=bool(
                    repo_state.get(result.repo, {}).get("has_dirty_tracked_files", False)
                ),
            )
            for result in results
        ]

    def get_symbol(self, name: str, *, repo: str | None = None) -> SearchResult | None:
        result = self.storage.find_symbol(
            name=name,
            embedding_model=self.embedding_provider.model_id,
            repo=repo,
        )
        if result is None:
            results = self.query(name, repo=repo, k=1)
            return results[0] if results else None
        repo_state = {
            str(item["repo_id"]): item for item in self._repo_status({result.repo})
        }
        return replace(
            result,
            is_stale=bool(repo_state.get(result.repo, {}).get("is_stale", True)),
            has_dirty_tracked_files=bool(
                repo_state.get(result.repo, {}).get("has_dirty_tracked_files", False)
            ),
        )

    def list_repos(self) -> list[dict[str, object]]:
        return self._repo_status(None)

    def _repo_status(self, repo_ids: set[str] | None) -> list[dict[str, object]]:
        repos = self.storage.repos_by_id(repo_ids)
        for repo in repos:
            try:
                repo_root = Path(str(repo["repo_path"]))
                repo["has_dirty_tracked_files"] = has_dirty_tracked_files(repo_root)
                repo["is_stale"] = (
                    current_commit(repo_root) != repo["last_commit_sha"]
                    or int(repo["error_count"] or 0) > 0
                )
            except Exception as exc:
                repo["is_stale"] = True
                repo["last_error"] = str(exc)
                repo["error_count"] = max(1, int(repo["error_count"] or 0))
        return repos

    def reindex(self, repo_path: str | Path | None = None) -> IndexResult:
        if repo_path is not None:
            return self.index_repo(repo_path)

        repo_paths = self.storage.repo_paths()
        if len(repo_paths) != 1:
            raise ValueError("repo_path is required unless exactly one repo is indexed")
        return self.index_repo(repo_paths[0])
