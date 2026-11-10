from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from repo_index_mcp.chunking import LineChunker
from repo_index_mcp.embeddings import EmbeddingProvider, embed_batch, embedding_provider_from_env
from repo_index_mcp.hooks import inspect_hooks
from repo_index_mcp.models import Chunk, IndexResult, SearchResult
from repo_index_mcp.repo import (
    changed_paths_between,
    committed_blob_paths_with_skips,
    committed_files_with_skips,
    content_hash,
    current_commit,
    discover_repos,
    filter_ignored_paths,
    has_dirty_tracked_files,
    iter_committed_text_files,
    load_codescryignore,
    remote_url_for,
    repo_id_for,
    resolve_repo_root,
    should_ignore_path,
    should_skip,
)
from repo_index_mcp.secrets import SECRET_FILTER_VERSION, looks_like_secret
from repo_index_mcp.storage import SQLiteStorage

DEFAULT_DB_PATH = Path.home() / ".codescry" / "index.sqlite"
DEFAULT_INDEX_EMBEDDING_BATCH_CHUNKS = 256
DEFAULT_MIN_CHUNK_BYTES = 50


class RepoIndex:
    def __init__(
        self,
        *,
        db_path: str | Path = DEFAULT_DB_PATH,
        embedding_provider: EmbeddingProvider | None = None,
        chunker: LineChunker | None = None,
    ) -> None:
        self.storage = SQLiteStorage(db_path)
        self.embedding_provider = embedding_provider or embedding_provider_from_env()
        self.chunker = chunker or LineChunker()
        self.min_chunk_bytes = min_chunk_bytes_from_env()

    def index_repo(self, repo_path: str | Path) -> IndexResult:
        start = time.monotonic()
        repo_root = resolve_repo_root(repo_path)
        repo_id = repo_id_for(repo_root)
        repo_path_str = str(repo_root)
        remote_url = remote_url_for(repo_root)
        expected_model = self.embedding_provider.model_id
        chunker_version = ""
        commit_sha = ""
        try:
            commit_sha = current_commit(repo_root)
            codescry_ignore = load_codescryignore(repo_root, commit_sha)
            ignore_version = (
                f":codescryignore:v1={codescry_ignore.fingerprint}"
                if codescry_ignore.has_patterns
                else ""
            )
            chunker_version = (
                f"{self.chunker.version}:{SECRET_FILTER_VERSION}:"
                f"tiny-filter:v1:min_bytes={self.min_chunk_bytes}{ignore_version}"
            )
            self.storage.cleanup_repo_path_aliases(repo_id=repo_id, repo_path=repo_path_str)
            stored_state = self.storage.indexed_file_state(repo_id=repo_id)
            prior_commit = self.storage.repo_commit(repo_id=repo_id)
            if not stored_state and self.storage.chunk_count(repo_id=repo_id):
                self.storage.clear_repo(repo_id=repo_id)

            model_or_chunker_changed = any(
                state[1:] != (expected_model, chunker_version) for state in stored_state.values()
            )
            newly_skipped_stored_paths = {path for path in stored_state if should_skip(path)}
            newly_ignored_stored_paths = {
                path for path in stored_state if should_ignore_path(path, codescry_ignore)
            }
            files_ignored = 0
            needs_full_scan = not prior_commit or not stored_state or model_or_chunker_changed
            changed_paths: list[str] = []
            if needs_full_scan:
                paths, artifact_skipped_paths = committed_files_with_skips(repo_root, commit_sha)
                paths, ignored_paths = filter_ignored_paths(paths, codescry_ignore)
                files, secret_skipped_paths = filter_secret_files(
                    iter_committed_text_files(repo_root, commit_sha, paths)
                )
                skipped_paths = artifact_skipped_paths | secret_skipped_paths
                files_ignored = len(ignored_paths | newly_ignored_stored_paths)
                current_hashes = {path: content_hash(file_content) for path, file_content in files}
                removed_paths = sorted(
                    (set(stored_state) - set(current_hashes))
                    | skipped_paths
                    | newly_skipped_stored_paths
                    | newly_ignored_stored_paths
                )
                files_indexed = len(files)
            else:
                changed_paths, removed_paths = (
                    ([], [])
                    if prior_commit == commit_sha
                    else changed_paths_between(repo_root, prior_commit, commit_sha)
                )
                paths, artifact_skipped_paths = committed_blob_paths_with_skips(
                    repo_root,
                    commit_sha,
                    changed_paths,
                )
                paths, ignored_paths = filter_ignored_paths(paths, codescry_ignore)
                files, secret_skipped_paths = filter_secret_files(
                    iter_committed_text_files(repo_root, commit_sha, paths)
                )
                skipped_paths = artifact_skipped_paths | secret_skipped_paths
                files_ignored = len(ignored_paths | newly_ignored_stored_paths)
                current_hashes = {path: content_hash(file_content) for path, file_content in files}
                ineligible_changed_paths = (set(changed_paths) & set(stored_state)) - set(
                    current_hashes
                )
                removed_paths = sorted(
                    set(removed_paths)
                    | ineligible_changed_paths
                    | skipped_paths
                    | newly_skipped_stored_paths
                    | newly_ignored_stored_paths
                )
                files_indexed = (
                    len(stored_state)
                    - len(removed_paths)
                    + sum(1 for path, _content in files if path not in stored_state)
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
        chunks_skipped = 0
        try:
            self.storage.delete_paths(repo_id=repo_id, paths=removed_paths)
        except Exception as exc:
            errors.append(f"delete removed paths: {exc}")

        pending: list[tuple[str, str, list]] = []
        pending_chunk_count = 0
        index_batch_chunks = index_embedding_batch_chunks()

        def embed_texts_limited(texts: list[str]) -> list[list[float]]:
            embeddings: list[list[float]] = []
            for start_index in range(0, len(texts), index_batch_chunks):
                embeddings.extend(
                    embed_batch(
                        self.embedding_provider,
                        texts[start_index : start_index + index_batch_chunks],
                    )
                )
            return embeddings

        def flush_pending() -> None:
            nonlocal chunks_indexed, pending, pending_chunk_count
            if not pending:
                return
            flat_chunks = [chunk for _path, _content_hash, chunks in pending for chunk in chunks]
            try:
                flat_embeddings = embed_texts_limited([chunk.content for chunk in flat_chunks])
                offset = 0
                for path, file_hash, chunks in pending:
                    next_offset = offset + len(chunks)
                    chunks_indexed += self.storage.replace_file_chunks(
                        repo_id=repo_id,
                        path=path,
                        content_hash=file_hash,
                        chunks=chunks,
                        embeddings=flat_embeddings[offset:next_offset],
                        commit_sha=commit_sha,
                        embedding_model=expected_model,
                        chunker_version=chunker_version,
                    )
                    offset = next_offset
            except Exception:
                for path, file_hash, chunks in pending:
                    try:
                        embeddings = embed_texts_limited([chunk.content for chunk in chunks])
                        chunks_indexed += self.storage.replace_file_chunks(
                            repo_id=repo_id,
                            path=path,
                            content_hash=file_hash,
                            chunks=chunks,
                            embeddings=embeddings,
                            commit_sha=commit_sha,
                            embedding_model=expected_model,
                            chunker_version=chunker_version,
                        )
                    except Exception as exc:
                        errors.append(f"{path}: {exc}")
            finally:
                pending = []
                pending_chunk_count = 0

        for path, file_content in changed_files:
            try:
                chunks = self.chunker.chunk_file(
                    repo_id=repo_id,
                    repo_path=repo_path_str,
                    path=path,
                    content=file_content,
                )
            except Exception as exc:
                errors.append(f"{path}: {exc}")
                continue
            chunks, skipped_count = filter_indexable_chunks(
                chunks,
                min_chunk_bytes=self.min_chunk_bytes,
            )
            chunks_skipped += skipped_count
            if pending and pending_chunk_count + len(chunks) > index_batch_chunks:
                flush_pending()
            pending.append((path, current_hashes[path], chunks))
            pending_chunk_count += len(chunks)
            if pending_chunk_count >= index_batch_chunks or len(pending) >= index_batch_chunks:
                flush_pending()
        flush_pending()

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
            files_skipped=len(skipped_paths),
            files_ignored=files_ignored,
            chunks_skipped=chunks_skipped,
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
        query_embedding, _cache_hit = self._embed_query(query)
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

    def query_debug(
        self,
        query: str,
        *,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
        k: int | None = 10,
    ) -> list[dict[str, object]]:
        total_start = time.perf_counter()
        embed_start = time.perf_counter()
        query_embedding, cache_hit = self._embed_query(query)
        embed_ms = int((time.perf_counter() - embed_start) * 1000)
        storage_start = time.perf_counter()
        debug_rows = self.storage.search_debug(
            query_embedding=query_embedding,
            embedding_model=self.embedding_provider.model_id,
            k=k,
            query_text=query,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
        )
        storage_ms = int((time.perf_counter() - storage_start) * 1000)
        repo_ids = {row["result"].repo for row in debug_rows}  # type: ignore[union-attr]
        repo_status_start = time.perf_counter()
        repo_state = {str(item["repo_id"]): item for item in self._repo_status(repo_ids)}
        repo_status_ms = int((time.perf_counter() - repo_status_start) * 1000)
        output: list[dict[str, object]] = []
        for row in debug_rows:
            result = row["result"]
            enriched = replace(
                result,
                is_stale=bool(repo_state.get(result.repo, {}).get("is_stale", True)),
                has_dirty_tracked_files=bool(
                    repo_state.get(result.repo, {}).get("has_dirty_tracked_files", False)
                ),
            )
            telemetry = dict(row.get("telemetry", {}))
            telemetry.update(
                {
                    "embed_query_ms": embed_ms,
                    "query_embedding_cache_hit": cache_hit,
                    "storage_ms": storage_ms,
                    "repo_status_ms": repo_status_ms,
                    "engine_total_ms": int((time.perf_counter() - total_start) * 1000),
                }
            )
            output.append({"result": enriched, "score": row["score"], "telemetry": telemetry})
        return output

    def _embed_query(self, query: str) -> tuple[list[float], bool]:
        fingerprint = self.embedding_provider_fingerprint()
        try:
            cached = self.storage.cached_query_embedding(
                embedding_model=self.embedding_provider.model_id,
                provider_fingerprint=fingerprint,
                query_text=query,
                expected_dimensions=self.embedding_provider.dimensions,
            )
        except OSError:
            cached = None
        if cached is not None:
            return cached, True
        embedding = self.embedding_provider.embed(query)
        try:
            self.storage.cache_query_embedding(
                embedding_model=self.embedding_provider.model_id,
                provider_fingerprint=fingerprint,
                query_text=query,
                embedding=embedding,
            )
        except OSError:
            pass
        return embedding, False

    def embedding_provider_fingerprint(self) -> str:
        return (
            f"{self.embedding_provider.model_id}:dims={self.embedding_provider.dimensions}:cache-v1"
        )

    def expected_path_debug(
        self,
        query: str,
        *,
        expected_path: str,
        expected_text: str | None = None,
        repo: str | None = None,
    ) -> dict[str, object]:
        return self.storage.expected_path_debug(
            query_embedding=self.embedding_provider.embed(query),
            embedding_model=self.embedding_provider.model_id,
            query_text=query,
            expected_path=expected_path,
            expected_text=expected_text,
            repo=repo,
        )

    def get_symbol(self, name: str, *, repo: str | None = None) -> SearchResult | None:
        result = self.storage.find_symbol(
            name=name,
            embedding_model=self.embedding_provider.model_id,
            repo=repo,
        )
        if result is None:
            results = self.query(name, repo=repo, k=1)
            return results[0] if results else None
        repo_state = {str(item["repo_id"]): item for item in self._repo_status({result.repo})}
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
                repo["freshness_hooks"] = inspect_hooks(repo_root)
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


def filter_indexable_chunks(
    chunks: list[Chunk],
    *,
    min_chunk_bytes: int,
) -> tuple[list[Chunk], int]:
    kept: list[Chunk] = []
    skipped = 0
    for chunk in chunks:
        if not chunk.content.strip():
            skipped += 1
            continue
        if (
            chunk.symbol_line is not None
            and chunk.start_line <= chunk.symbol_line <= chunk.end_line
        ):
            kept.append(chunk)
            continue
        if min_chunk_bytes > 0 and len(chunk.content.encode("utf-8")) < min_chunk_bytes:
            skipped += 1
            continue
        kept.append(chunk)
    return kept, skipped


def min_chunk_bytes_from_env() -> int:
    configured = os.environ.get("CODESCRY_MIN_CHUNK_BYTES", str(DEFAULT_MIN_CHUNK_BYTES))
    try:
        value = int(configured)
    except ValueError as exc:
        raise ValueError(
            f"invalid CODESCRY_MIN_CHUNK_BYTES={configured!r}; expected non-negative integer"
        ) from exc
    if value < 0:
        raise ValueError(
            f"invalid CODESCRY_MIN_CHUNK_BYTES={configured!r}; expected non-negative integer"
        )
    return value


def index_embedding_batch_chunks() -> int:
    configured = os.environ.get(
        "CODESCRY_INDEX_EMBEDDING_BATCH_CHUNKS",
        str(DEFAULT_INDEX_EMBEDDING_BATCH_CHUNKS),
    )
    return max(int(configured), 1)


def filter_secret_files(files: Iterable[tuple[str, str]]) -> tuple[list[tuple[str, str]], set[str]]:
    safe_files: list[tuple[str, str]] = []
    skipped_paths: set[str] = set()
    for path, content in files:
        if looks_like_secret(content):
            skipped_paths.add(path)
        else:
            safe_files.append((path, content))
    return safe_files, skipped_paths
