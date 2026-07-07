from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from repo_index_mcp.embeddings import cosine_similarity, tokenize_code
from repo_index_mcp.models import Chunk, SearchResult

BUSY_TIMEOUT_MS = 5000
FTS_INDEX_VERSION = "fts-v1"
DEFAULT_CANDIDATE_THRESHOLD = 100_000
DEFAULT_VECTOR_CANDIDATE_LIMIT = 2000
DEFAULT_FTS_CANDIDATE_LIMIT = 200
DEFAULT_UNION_FTS_CANDIDATE_LIMIT = 2000
RRF_K = 60
RRF_FETCH_MULTIPLIER = 4
RRF_MIN_FETCH = 50
RRF_MAX_FETCH = 500
RRF_FILTERED_VECTOR_OVERFETCH_LIMIT = 4096
RRF_EXACT_SCAN_LIMIT = 5000
RRF_TOP_RANK_BONUS = 0.05
RRF_NEAR_TOP_RANK_BONUS = 0.02
QUERY_EXPANSIONS = {
    "retry": {"backoff", "attempt", "attempts"},
    "backoff": {"retry", "attempt", "attempts"},
    "auth": {"token", "credential", "credentials"},
    "token": {"auth", "credential", "credentials"},
    "credential": {"auth", "token", "credentials"},
    "credentials": {"auth", "token", "credential"},
    "route": {"endpoint", "handler"},
    "endpoint": {"route", "handler"},
    "handler": {"route", "endpoint"},
    "config": {"setting", "settings", "option", "options"},
    "setting": {"config", "option"},
    "settings": {"config", "options"},
    "option": {"config", "setting"},
    "options": {"config", "settings"},
}

FTS_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "where",
    "what",
    "how",
    "does",
    "this",
    "that",
    "from",
    "into",
    "code",
    "find",
    "show",
    "implemented",
    "implementation",
}


@dataclass(frozen=True)
class RankedCandidate:
    rowid: int
    backend_score: float
    path: str
    start_line: int


class SQLiteStorage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def cached_query_embedding(
        self,
        *,
        embedding_model: str,
        provider_fingerprint: str,
        query_text: str,
        expected_dimensions: int,
    ) -> list[float] | None:
        with self._connect() as conn:
            query_hash = query_embedding_hash(conn, query_text)
            row = conn.execute(
                """
                SELECT embedding
                FROM query_embedding_cache
                WHERE embedding_model = ? AND provider_fingerprint = ? AND query_hash = ?
                """,
                (embedding_model, provider_fingerprint, query_hash),
            ).fetchone()
            if row is None:
                return None
            try:
                embedding = [float(value) for value in json.loads(row[0])]
            except (TypeError, ValueError, json.JSONDecodeError):
                delete_cached_query_embedding(
                    conn,
                    embedding_model=embedding_model,
                    provider_fingerprint=provider_fingerprint,
                    query_hash=query_hash,
                )
                return None
            if len(embedding) != expected_dimensions:
                delete_cached_query_embedding(
                    conn,
                    embedding_model=embedding_model,
                    provider_fingerprint=provider_fingerprint,
                    query_hash=query_hash,
                )
                return None
            return embedding

    def cache_query_embedding(
        self,
        *,
        embedding_model: str,
        provider_fingerprint: str,
        query_text: str,
        embedding: list[float],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO query_embedding_cache(
                    embedding_model, provider_fingerprint, query_hash, embedding
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    embedding_model,
                    provider_fingerprint,
                    query_embedding_hash(conn, query_text),
                    json.dumps(embedding),
                ),
            )

    def cleanup_repo_path_aliases(self, *, repo_id: str, repo_path: str) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT repo_id FROM repos WHERE repo_path = ? AND repo_id != ?",
                (repo_path, repo_id),
            ).fetchall()
            old_ids = [row[0] for row in rows]
            for old_id in old_ids:
                delete_fts_for_repo(conn, old_id)
                delete_vectors_for_repo(conn, old_id)
                conn.execute("DELETE FROM chunks WHERE repo_id = ?", (old_id,))
                conn.execute("DELETE FROM indexed_files WHERE repo_id = ?", (old_id,))
                conn.execute("DELETE FROM repos WHERE repo_id = ?", (old_id,))
        return len(old_ids)

    def record_repo_success(
        self,
        *,
        repo_id: str,
        repo_path: str,
        commit_sha: str,
        remote_url: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repos(
                    repo_id,
                    repo_path,
                    remote_url,
                    last_commit_sha,
                    indexed_at,
                    last_error,
                    error_count
                )
                VALUES (?, ?, ?, ?, ?, NULL, 0)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_path = excluded.repo_path,
                    remote_url = excluded.remote_url,
                    last_commit_sha = excluded.last_commit_sha,
                    indexed_at = excluded.indexed_at,
                    last_error = NULL,
                    error_count = 0
                """,
                (repo_id, repo_path, remote_url, commit_sha, now_iso()),
            )

    def record_repo_failure(
        self,
        *,
        repo_id: str,
        repo_path: str,
        remote_url: str,
        last_error: str,
        error_count: int,
    ) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT last_commit_sha FROM repos WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
            last_commit_sha = existing[0] if existing else ""
            conn.execute(
                """
                INSERT INTO repos(
                    repo_id,
                    repo_path,
                    remote_url,
                    last_commit_sha,
                    indexed_at,
                    last_error,
                    error_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_path = excluded.repo_path,
                    remote_url = excluded.remote_url,
                    indexed_at = excluded.indexed_at,
                    last_error = excluded.last_error,
                    error_count = excluded.error_count
                """,
                (
                    repo_id,
                    repo_path,
                    remote_url,
                    last_commit_sha,
                    now_iso(),
                    last_error,
                    error_count,
                ),
            )

    def replace_chunks(
        self,
        *,
        repo_id: str,
        chunks: Iterable[Chunk],
        embeddings: Iterable[list[float]],
        commit_sha: str,
        embedding_model: str,
        chunker_version: str,
    ) -> int:
        rows = chunk_rows(chunks, embeddings, commit_sha, embedding_model, chunker_version)
        with self._connect() as conn:
            delete_fts_for_repo(conn, repo_id)
            delete_vectors_for_repo(conn, repo_id)
            conn.execute("DELETE FROM chunks WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM indexed_files WHERE repo_id = ?", (repo_id,))
            insert_chunks(conn, rows)
        return len(rows)

    def indexed_file_state(self, *, repo_id: str) -> dict[str, tuple[str, str, str]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT path, content_hash, embedding_model, chunker_version
                FROM indexed_files
                WHERE repo_id = ?
                """,
                (repo_id,),
            ).fetchall()
        return {row[0]: (row[1], row[2], row[3]) for row in rows}

    def replace_file_chunks(
        self,
        *,
        repo_id: str,
        path: str,
        content_hash: str,
        chunks: Iterable[Chunk],
        embeddings: Iterable[list[float]],
        commit_sha: str,
        embedding_model: str,
        chunker_version: str,
    ) -> int:
        rows = chunk_rows(chunks, embeddings, commit_sha, embedding_model, chunker_version)
        indexed_at = now_iso()
        with self._connect() as conn:
            delete_fts_for_path(conn, repo_id, path)
            delete_vectors_for_path(conn, repo_id, path)
            conn.execute("DELETE FROM chunks WHERE repo_id = ? AND path = ?", (repo_id, path))
            insert_chunks(conn, rows)
            conn.execute(
                """
                INSERT INTO indexed_files(
                    repo_id,
                    path,
                    content_hash,
                    commit_sha,
                    embedding_model,
                    chunker_version,
                    indexed_at,
                    chunk_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(repo_id, path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    commit_sha = excluded.commit_sha,
                    embedding_model = excluded.embedding_model,
                    chunker_version = excluded.chunker_version,
                    indexed_at = excluded.indexed_at,
                    chunk_count = excluded.chunk_count
                """,
                (
                    repo_id,
                    path,
                    content_hash,
                    commit_sha,
                    embedding_model,
                    chunker_version,
                    indexed_at,
                    len(rows),
                ),
            )
        return len(rows)

    def clear_repo(self, *, repo_id: str) -> None:
        with self._connect() as conn:
            delete_fts_for_repo(conn, repo_id)
            delete_vectors_for_repo(conn, repo_id)
            conn.execute("DELETE FROM chunks WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM indexed_files WHERE repo_id = ?", (repo_id,))

    def remove_repo(self, repo: str) -> dict[str, object] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT r.repo_id, r.repo_path, COUNT(c.chunk_id)
                FROM repos r
                LEFT JOIN chunks c ON c.repo_id = r.repo_id
                WHERE r.repo_id = ? OR r.repo_path = ?
                GROUP BY r.repo_id, r.repo_path
                """,
                (repo, repo),
            ).fetchone()
            if row is None:
                return None
            repo_id = str(row[0])
            repo_path = str(row[1])
            chunk_count = int(row[2])
            delete_fts_for_repo(conn, repo_id)
            delete_vectors_for_repo(conn, repo_id)
            conn.execute("DELETE FROM chunks WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM indexed_files WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM repos WHERE repo_id = ?", (repo_id,))
            return {
                "repo_id": repo_id,
                "repo_path": repo_path,
                "chunks_removed": chunk_count,
            }

    def prune_missing_repos(self) -> list[dict[str, object]]:
        removed: list[dict[str, object]] = []
        for repo in self.list_repos():
            repo_path = str(repo["repo_path"])
            if Path(repo_path).exists():
                continue
            result = self.remove_repo(str(repo["repo_id"]))
            if result is not None:
                removed.append(result)
        return removed

    def delete_paths(self, *, repo_id: str, paths: Sequence[str]) -> int:
        if not paths:
            return 0
        with self._connect() as conn:
            deleted_chunks = 0
            for path in paths:
                delete_fts_for_path(conn, repo_id, path)
                delete_vectors_for_path(conn, repo_id, path)
                cursor = conn.execute(
                    "DELETE FROM chunks WHERE repo_id = ? AND path = ?",
                    (repo_id, path),
                )
                deleted_chunks += cursor.rowcount
                conn.execute(
                    "DELETE FROM indexed_files WHERE repo_id = ? AND path = ?",
                    (repo_id, path),
                )
        return deleted_chunks

    def chunk_count(self, *, repo_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM chunks WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
        return int(row[0])

    def backfill_vectors(self) -> int:
        with self._connect() as conn:
            return backfill_vectors(conn)

    def search(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        k: int,
        query_text: str,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
        return [
            item["result"]
            for item in self.search_debug(
                query_embedding=query_embedding,
                embedding_model=embedding_model,
                k=k,
                query_text=query_text,
                repo=repo,
                path_prefix=path_prefix,
                language=language,
            )
        ]

    def search_debug(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        k: int | None,
        query_text: str,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
    ) -> list[dict[str, object]]:
        where = ["c.embedding_model = ?"]
        params: list[str] = [embedding_model]
        if repo:
            where.append("(c.repo_id = ? OR c.repo_path = ?)")
            params.extend([repo, repo])
        if path_prefix:
            where.append("c.path LIKE ?")
            params.append(f"{path_prefix}%")
        if language:
            where.append("c.language = ?")
            params.append(language)

        total_start = time.perf_counter()
        rows: list[dict[str, object]] = []
        rrf_disabled_reason: str | None = None
        telemetry: dict[str, object] = {
            "ranking_mode": "current",
            "candidate_path": "full_scan",
            "fts_ms": 0,
            "vector_ms": 0,
            "row_score_ms": 0,
            "diversify_ms": 0,
            "fts_candidates": 0,
            "vector_candidates": 0,
            "candidate_union_size": 0,
            "rows_scored": 0,
            "rows_returned": 0,
        }
        with self._connect() as conn:
            if rrf_ranking_enabled() and k is not None:
                rrf_rows, rrf_disabled_reason = self._search_debug_rrf(
                    conn=conn,
                    query_embedding=query_embedding,
                    embedding_model=embedding_model,
                    k=k,
                    query_text=query_text,
                    repo=repo,
                    path_prefix=path_prefix,
                    language=language,
                )
                if rrf_rows:
                    return rrf_rows
            elif rrf_ranking_enabled():
                rrf_disabled_reason = "k_none"

            union_preflight = should_try_candidate_union(
                conn,
                query_text=query_text,
                embedding_model=embedding_model,
                repo=repo,
                path_prefix=path_prefix,
                language=language,
                k=k,
            )
            fts_limit = (
                DEFAULT_UNION_FTS_CANDIDATE_LIMIT
                if union_preflight and not (repo or path_prefix or language)
                else DEFAULT_FTS_CANDIDATE_LIMIT
            )
            fts_start = time.perf_counter()
            if union_preflight and (repo or path_prefix or language):
                bm25_scores = {}
            else:
                bm25_scores = fts_scores(
                    conn,
                    query_text=query_text,
                    embedding_model=embedding_model,
                    repo=repo,
                    path_prefix=path_prefix,
                    language=language,
                    limit=fts_limit,
                )
            telemetry["fts_ms"] = elapsed_ms(fts_start)
            telemetry["fts_candidates"] = len(bm25_scores)
            vector_start = time.perf_counter()
            vector_candidate_scores = (
                vector_scores(
                    conn,
                    query_embedding=query_embedding,
                    embedding_model=embedding_model,
                    repo=repo,
                    path_prefix=path_prefix,
                    language=language,
                )
                if union_preflight
                else {}
            )
            telemetry["vector_ms"] = elapsed_ms(vector_start)
            telemetry["vector_candidates"] = len(vector_candidate_scores)
            from_clause = "chunks c"
            if should_use_candidate_union(
                vector_scores=vector_candidate_scores,
                bm25_scores=bm25_scores,
                k=k,
            ):
                candidate_rowids = set(vector_candidate_scores) | set(bm25_scores)
                telemetry["candidate_path"] = "candidate_union"
                telemetry["candidate_union_size"] = len(candidate_rowids)
                conn.execute(
                    "CREATE TEMP TABLE IF NOT EXISTS candidate_rowids(rowid INTEGER PRIMARY KEY)"
                )
                conn.execute("DELETE FROM candidate_rowids")
                conn.executemany(
                    "INSERT OR IGNORE INTO candidate_rowids(rowid) VALUES (?)",
                    [(rowid,) for rowid in candidate_rowids],
                )
                from_clause = "candidate_rowids ci CROSS JOIN chunks c ON c.rowid = ci.rowid"
            sql = f"""
                SELECT
                    c.rowid,
                    c.chunk_id,
                    c.repo_id,
                    c.path,
                    c.start_line,
                    c.end_line,
                    c.content,
                    c.embedding,
                    c.language,
                    c.symbol_name,
                    c.symbol_kind,
                    c.symbol_line,
                    c.symbol_confidence
                FROM {from_clause}
                WHERE {' AND '.join(where)}
            """
            score_start = time.perf_counter()
            for row in conn.execute(sql, params):
                embedding = json.loads(row[7])
                vector_score = cosine_similarity(query_embedding, embedding)
                parts = score_breakdown(
                    query_text=query_text,
                    vector_score=vector_score,
                    path=row[3],
                    content=row[6],
                    symbol_name=row[9],
                    bm25_score=bm25_scores.get(row[0], 0.0),
                )
                if rrf_disabled_reason is not None:
                    parts = {
                        **parts,
                        "ranking_mode": "current",
                        "rrf_requested": True,
                        "rrf_disabled_reason": rrf_disabled_reason,
                    }
                result = SearchResult(
                    repo=row[2],
                    path=row[3],
                    start_line=row[4],
                    end_line=row[5],
                    snippet=row[6],
                    score=float(parts["score"]),
                    language=row[8],
                    symbol_name=row[9],
                    symbol_kind=row[10],
                    symbol_line=row[11],
                    symbol_confidence=row[12],
                )
                rows.append({"result": result, "score": parts})
            telemetry["row_score_ms"] = elapsed_ms(score_start)
            telemetry["rows_scored"] = len(rows)

        rows.sort(key=lambda item: search_sort_key(item["result"]), reverse=True)
        if k is None:
            telemetry["rows_returned"] = len(rows)
            telemetry["total_ms"] = elapsed_ms(total_start)
            return attach_telemetry(rows, telemetry)
        if wants_docs_query(query_text):
            output = rows[:k]
            telemetry["rows_returned"] = len(output)
            telemetry["total_ms"] = elapsed_ms(total_start)
            return attach_telemetry(output, telemetry)
        diversify_start = time.perf_counter()
        selected = diversify_results([item["result"] for item in rows], k)
        telemetry["diversify_ms"] = elapsed_ms(diversify_start)
        debug_by_result_id = {id(item["result"]): item for item in rows}
        output = [debug_by_result_id[id(result)] for result in selected]
        telemetry["rows_returned"] = len(output)
        telemetry["total_ms"] = elapsed_ms(total_start)
        return attach_telemetry(output, telemetry)

    def _search_debug_rrf(
        self,
        *,
        conn: sqlite3.Connection,
        query_embedding: list[float],
        embedding_model: str,
        k: int,
        query_text: str,
        repo: str | None,
        path_prefix: str | None,
        language: str | None,
    ) -> tuple[list[dict[str, object]], str | None]:
        total_start = time.perf_counter()
        fts_start = time.perf_counter()
        fts_candidates = ranked_fts_candidates(
            conn,
            query_text=query_text,
            embedding_model=embedding_model,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
            limit=DEFAULT_UNION_FTS_CANDIDATE_LIMIT,
        )
        fts_ms = elapsed_ms(fts_start)
        if not fts_candidates:
            return [], "no_fts_candidates"
        if not vector_table_exists(conn):
            return [], "vector_table_missing"
        if not vector_coverage_complete(conn, embedding_model=embedding_model):
            return [], "vector_coverage_incomplete"
        vector_start = time.perf_counter()
        vector_candidates = ranked_vector_candidates(
            conn,
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
            limit=DEFAULT_VECTOR_CANDIDATE_LIMIT,
        )
        vector_ms = elapsed_ms(vector_start)
        if not vector_candidates:
            return [], "no_vector_candidates"

        extra_sources: list[tuple[str, Sequence[RankedCandidate]]] = []
        if rrf_exact_lists_enabled():
            extra_sources = [
                (
                    "path",
                    ranked_path_candidates(
                        conn,
                        query_text=query_text,
                        embedding_model=embedding_model,
                        repo=repo,
                        path_prefix=path_prefix,
                        language=language,
                    ),
                ),
                (
                    "symbol",
                    ranked_symbol_candidates(
                        conn,
                        query_text=query_text,
                        embedding_model=embedding_model,
                        repo=repo,
                        path_prefix=path_prefix,
                        language=language,
                    ),
                ),
                (
                    "lexical",
                    ranked_lexical_candidates(
                        conn,
                        query_text=query_text,
                        embedding_model=embedding_model,
                        repo=repo,
                        path_prefix=path_prefix,
                        language=language,
                    ),
                ),
            ]

        fuse_start = time.perf_counter()
        ordered_rowids, traces = rrf_fuse(
            fts_candidates=fts_candidates,
            vector_candidates=vector_candidates,
            extra_sources=extra_sources,
            top_rank_bonus=rrf_top_rank_bonus_enabled(),
        )
        fuse_ms = elapsed_ms(fuse_start)
        fetch_limit = min(RRF_MAX_FETCH, max(RRF_MIN_FETCH, k * RRF_FETCH_MULTIPLIER))
        fetch_rowids = ordered_rowids[:fetch_limit]
        if not fetch_rowids:
            return [], "rrf_error"

        conn.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS rrf_candidate_rowids(
                rowid INTEGER PRIMARY KEY,
                rank_order INTEGER NOT NULL,
                rrf_score REAL NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM rrf_candidate_rowids")
        conn.executemany(
            """
            INSERT OR REPLACE INTO rrf_candidate_rowids(rowid, rank_order, rrf_score)
            VALUES (?, ?, ?)
            """,
            [
                (rowid, index, float(traces[rowid]["rrf_score"]))
                for index, rowid in enumerate(fetch_rowids)
            ],
        )
        rows: list[dict[str, object]] = []
        fetch_start = time.perf_counter()
        sql = """
            SELECT
                c.rowid,
                c.repo_id,
                c.path,
                c.start_line,
                c.end_line,
                c.content,
                c.language,
                c.symbol_name,
                c.symbol_kind,
                c.symbol_line,
                c.symbol_confidence,
                rc.rrf_score
            FROM rrf_candidate_rowids rc
            JOIN chunks c ON c.rowid = rc.rowid
            ORDER BY rc.rank_order
        """
        for row in conn.execute(sql):
            trace = traces[int(row[0])]
            result = SearchResult(
                repo=row[1],
                path=row[2],
                start_line=row[3],
                end_line=row[4],
                snippet=row[5],
                score=float(row[11]),
                language=row[6],
                symbol_name=row[7],
                symbol_kind=row[8],
                symbol_line=row[9],
                symbol_confidence=row[10],
            )
            rows.append(
                {
                    "result": result,
                    "score": {
                        "ranking_mode": "rrf_v1",
                        "score": result.score,
                        "rrf_score": result.score,
                        "source_ranks": trace["source_ranks"],
                        "source_contributions": trace["source_contributions"],
                        "top_rank_bonus": trace.get("top_rank_bonus", 0.0),
                    },
                }
            )
        row_fetch_ms = elapsed_ms(fetch_start)
        telemetry: dict[str, object] = {
            "ranking_mode": "rrf_v1",
            "candidate_path": "rrf",
            "fts_ms": fts_ms,
            "vector_ms": vector_ms,
            "fuse_ms": fuse_ms,
            "row_fetch_ms": row_fetch_ms,
            "diversify_ms": 0,
            "fts_candidates": len(fts_candidates),
            "vector_candidates": len(vector_candidates),
            "candidate_union_size": len(ordered_rowids),
            "rows_scored": len(rows),
            "rows_returned": 0,
        }
        if wants_docs_query(query_text):
            output = rows[:k]
            telemetry["rows_returned"] = len(output)
            telemetry["total_ms"] = elapsed_ms(total_start)
            return attach_telemetry(output, telemetry), None
        diversify_start = time.perf_counter()
        selected = diversify_results([item["result"] for item in rows], k)
        telemetry["diversify_ms"] = elapsed_ms(diversify_start)
        debug_by_result_id = {id(item["result"]): item for item in rows}
        output = [debug_by_result_id[id(result)] for result in selected]
        telemetry["rows_returned"] = len(output)
        telemetry["total_ms"] = elapsed_ms(total_start)
        return attach_telemetry(output, telemetry), None

    def expected_path_debug(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        query_text: str,
        expected_path: str,
        expected_text: str | None = None,
        repo: str | None = None,
    ) -> dict[str, object]:
        best_score = None
        best_parts = None
        best_text_match = False
        path_found = False
        expected_text_lower = expected_text.lower() if expected_text else None
        for row in self._iter_score_rows(
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            query_text=query_text,
            repo=repo,
        ):
            if row["path"] != expected_path:
                continue
            path_found = True
            text_match = (
                True
                if expected_text_lower is None
                else expected_text_lower in str(row["content"]).lower()
            )
            if best_score is None or (text_match, row["score"]["score"]) > (
                best_text_match,
                best_score,
            ):
                best_score = row["score"]["score"]
                best_parts = row["score"]
                best_text_match = text_match
        if best_score is None:
            return {
                "expected_found_in_index": False,
                "expected_path_found_in_index": path_found,
                "expected_best_rank": None,
                "expected_best_score_parts": None,
                "expected_text_match": None,
            }
        better_count = 0
        for row in self._iter_score_rows(
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            query_text=query_text,
            repo=repo,
        ):
            if row["score"]["score"] > best_score:
                better_count += 1
        return {
            "expected_found_in_index": best_text_match if expected_text_lower else path_found,
            "expected_path_found_in_index": path_found,
            "expected_best_rank": better_count + 1,
            "expected_best_score_parts": best_parts,
            "expected_text_match": best_text_match if expected_text_lower else None,
        }

    def _iter_score_rows(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        query_text: str,
        repo: str | None = None,
    ):
        where = ["embedding_model = ?"]
        params = [embedding_model]
        if repo:
            where.append("(repo_id = ? OR repo_path = ?)")
            params.extend([repo, repo])
        sql = f"""
            SELECT rowid, path, content, embedding, symbol_name
            FROM chunks
            WHERE {' AND '.join(where)}
        """
        with self._connect() as conn:
            bm25_scores = fts_scores(
                conn,
                query_text=query_text,
                embedding_model=embedding_model,
                repo=repo,
            )
            for rowid, path, content, embedding_json, symbol_name in conn.execute(sql, params):
                embedding = json.loads(embedding_json)
                vector_score = cosine_similarity(query_embedding, embedding)
                yield {
                    "path": path,
                    "content": content,
                    "score": score_breakdown(
                        query_text=query_text,
                        vector_score=vector_score,
                        path=path,
                        content=content,
                        symbol_name=symbol_name,
                        bm25_score=bm25_scores.get(rowid, 0.0),
                    ),
                }

    def find_symbol(
        self,
        *,
        name: str,
        embedding_model: str,
        repo: str | None = None,
    ) -> SearchResult | None:
        where = ["embedding_model = ?", "symbol_name IS NOT NULL"]
        params: list[str] = [embedding_model]
        if repo:
            where.append("(repo_id = ? OR repo_path = ?)")
            params.extend([repo, repo])
        rows = []
        with self._connect() as conn:
            for row in conn.execute(
                f"""
                SELECT
                    repo_id,
                    path,
                    start_line,
                    end_line,
                    content,
                    language,
                    symbol_name,
                    symbol_kind,
                    symbol_line,
                    symbol_confidence
                FROM chunks
                WHERE {' AND '.join(where)}
                """,
                params,
            ):
                rank = symbol_rank(name, row[6], row[9])
                if rank is not None:
                    rows.append((rank, row))
        if not rows:
            return None
        rows.sort(key=lambda item: item[0])
        row = rows[0][1]
        return SearchResult(
            repo=row[0],
            path=row[1],
            start_line=row[2],
            end_line=row[3],
            snippet=row[4],
            score=1.0,
            language=row[5],
            symbol_name=row[6],
            symbol_kind=row[7],
            symbol_line=row[8],
            symbol_confidence=row[9],
        )

    def list_repos(self) -> list[dict[str, object]]:
        return self.repos_by_id(None)

    def repos_by_id(self, repo_ids: set[str] | None) -> list[dict[str, object]]:
        where = ""
        params: list[str] = []
        if repo_ids is not None:
            if not repo_ids:
                return []
            placeholders = ", ".join("?" for _repo_id in repo_ids)
            where = f"WHERE r.repo_id IN ({placeholders})"
            params = sorted(repo_ids)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT
                    r.repo_id,
                    r.repo_path,
                    r.remote_url,
                    r.last_commit_sha,
                    r.indexed_at,
                    r.last_error,
                    r.error_count,
                    COUNT(c.chunk_id)
                FROM repos r
                LEFT JOIN chunks c ON c.repo_id = r.repo_id
                {where}
                GROUP BY
                    r.repo_id,
                    r.repo_path,
                    r.remote_url,
                    r.last_commit_sha,
                    r.indexed_at,
                    r.last_error,
                    r.error_count
                ORDER BY r.repo_id
                """,
                params,
            ).fetchall()
        return [
            {
                "repo_id": row[0],
                "repo_path": row[1],
                "remote_url": row[2],
                "last_commit_sha": row[3],
                "indexed_at": row[4],
                "last_error": row[5],
                "error_count": row[6],
                "chunk_count": row[7],
                "is_stale": False,
                "has_dirty_tracked_files": False,
            }
            for row in rows
        ]

    def repo_commit(self, *, repo_id: str) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_commit_sha FROM repos WHERE repo_id = ?",
                (repo_id,),
            ).fetchone()
        return None if row is None else str(row[0])

    def repo_paths(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT repo_path FROM repos ORDER BY repo_id").fetchall()
        return [row[0] for row in rows]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    repo_id TEXT PRIMARY KEY,
                    repo_path TEXT NOT NULL,
                    remote_url TEXT NOT NULL DEFAULT '',
                    last_commit_sha TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    last_error TEXT,
                    error_count INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    symbol_name TEXT,
                    symbol_kind TEXT,
                    symbol_line INTEGER,
                    symbol_confidence TEXT,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL DEFAULT '',
                    embedding TEXT NOT NULL,
                    embedding_model TEXT NOT NULL DEFAULT '',
                    chunker_version TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS indexed_files (
                    repo_id TEXT NOT NULL,
                    path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    commit_sha TEXT NOT NULL DEFAULT '',
                    embedding_model TEXT NOT NULL DEFAULT '',
                    chunker_version TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL,
                    chunk_count INTEGER NOT NULL,
                    PRIMARY KEY(repo_id, path)
                );

                CREATE TABLE IF NOT EXISTS storage_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS query_embedding_cache (
                    embedding_model TEXT NOT NULL,
                    provider_fingerprint TEXT NOT NULL,
                    query_hash TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    PRIMARY KEY(embedding_model, provider_fingerprint, query_hash)
                );

                """
            )
            ensure_column(
                conn,
                table="repos",
                column="remote_url",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(conn, table="repos", column="last_error", definition="TEXT")
            ensure_column(
                conn,
                table="repos",
                column="error_count",
                definition="INTEGER NOT NULL DEFAULT 0",
            )
            ensure_column(
                conn,
                table="chunks",
                column="symbol_kind",
                definition="TEXT",
            )
            ensure_column(
                conn,
                table="chunks",
                column="symbol_line",
                definition="INTEGER",
            )
            ensure_column(
                conn,
                table="chunks",
                column="symbol_confidence",
                definition="TEXT",
            )
            ensure_column(
                conn,
                table="chunks",
                column="content_hash",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(
                conn,
                table="chunks",
                column="embedding_model",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(
                conn,
                table="chunks",
                column="chunker_version",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(
                conn,
                table="indexed_files",
                column="commit_sha",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(
                conn,
                table="indexed_files",
                column="embedding_model",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            ensure_column(
                conn,
                table="indexed_files",
                column="chunker_version",
                definition="TEXT NOT NULL DEFAULT ''",
            )
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
                CREATE INDEX IF NOT EXISTS idx_chunks_language ON chunks(language);
                CREATE INDEX IF NOT EXISTS idx_chunks_model ON chunks(embedding_model);
                CREATE INDEX IF NOT EXISTS idx_indexed_files_repo ON indexed_files(repo_id);
                CREATE INDEX IF NOT EXISTS idx_repos_repo_path ON repos(repo_path);
                """
            )
            if ensure_fts_table(conn):
                current_fts_version = meta_value(conn, "fts_index_version")
                if current_fts_version != FTS_INDEX_VERSION:
                    backfill_fts(conn)
                    set_meta_value(conn, "fts_index_version", FTS_INDEX_VERSION)


def meta_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM storage_meta WHERE key = ?", (key,)).fetchone()
    return None if row is None else str(row[0])


def set_meta_value(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO storage_meta(key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def query_embedding_hash(conn: sqlite3.Connection, query_text: str) -> str:
    salt = meta_value(conn, "query_embedding_cache_salt")
    if salt is None:
        salt = os.urandom(32).hex()
        set_meta_value(conn, "query_embedding_cache_salt", salt)
    return hmac.new(bytes.fromhex(salt), query_text.encode("utf-8"), hashlib.sha256).hexdigest()


def delete_cached_query_embedding(
    conn: sqlite3.Connection,
    *,
    embedding_model: str,
    provider_fingerprint: str,
    query_hash: str,
) -> None:
    conn.execute(
        """
        DELETE FROM query_embedding_cache
        WHERE embedding_model = ? AND provider_fingerprint = ? AND query_hash = ?
        """,
        (embedding_model, provider_fingerprint, query_hash),
    )


def ensure_column(
    conn: sqlite3.Connection,
    *,
    table: str,
    column: str,
    definition: str,
) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def chunk_rows(
    chunks: Iterable[Chunk],
    embeddings: Iterable[list[float]],
    commit_sha: str,
    embedding_model: str,
    chunker_version: str,
) -> list[tuple[object, ...]]:
    indexed_at = now_iso()
    return [
        (
            chunk_id_for(chunk),
            chunk.repo_id,
            chunk.repo_path,
            chunk.path,
            chunk.language,
            chunk.symbol_name,
            chunk.symbol_kind,
            chunk.symbol_line,
            chunk.symbol_confidence,
            chunk.start_line,
            chunk.end_line,
            commit_sha,
            chunk.content,
            content_hash_for(chunk.content),
            json.dumps(embedding),
            embedding_model,
            chunker_version,
            indexed_at,
        )
        for chunk, embedding in zip(chunks, embeddings, strict=True)
    ]


def insert_chunks(conn: sqlite3.Connection, rows: Sequence[tuple[object, ...]]) -> None:
    conn.executemany(
        """
        INSERT INTO chunks(
            chunk_id,
            repo_id,
            repo_path,
            path,
            language,
            symbol_name,
            symbol_kind,
            symbol_line,
            symbol_confidence,
            start_line,
            end_line,
            commit_sha,
            content,
            content_hash,
            embedding,
            embedding_model,
            chunker_version,
            indexed_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    insert_fts_rows(conn, rows)
    insert_vector_rows(conn, rows)


def ensure_fts_table(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
                chunk_id UNINDEXED,
                repo_id UNINDEXED,
                path,
                symbol_name,
                content
            )
            """
        )
        return True
    except sqlite3.OperationalError as exc:
        if "no such module" in str(exc).lower() and "fts5" in str(exc).lower():
            return False
        raise


def optional_fts_error(exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    return "no such table: chunks_fts" in message or (
        "no such module" in message and "fts5" in message
    )


def delete_fts_for_repo(conn: sqlite3.Connection, repo_id: str) -> None:
    try:
        conn.execute("DELETE FROM chunks_fts WHERE repo_id = ?", (repo_id,))
    except sqlite3.OperationalError as exc:
        if not optional_fts_error(exc):
            raise


def delete_fts_for_path(conn: sqlite3.Connection, repo_id: str, path: str) -> None:
    try:
        conn.execute(
            """
            DELETE FROM chunks_fts
            WHERE chunk_id IN (
                SELECT chunk_id FROM chunks WHERE repo_id = ? AND path = ?
            )
            """,
            (repo_id, path),
        )
    except sqlite3.OperationalError as exc:
        if not optional_fts_error(exc):
            raise


def insert_fts_rows(conn: sqlite3.Connection, rows: Sequence[tuple[object, ...]]) -> None:
    if not rows:
        return
    try:
        conn.executemany(
            """
            INSERT INTO chunks_fts(chunk_id, repo_id, path, symbol_name, content)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    row[0],
                    row[1],
                    normalized_fts_text(str(row[3])),
                    normalized_fts_text(str(row[5] or "")),
                    normalized_fts_text(str(row[12])),
                )
                for row in rows
            ],
        )
    except sqlite3.OperationalError as exc:
        if not optional_fts_error(exc):
            raise


def backfill_fts(conn: sqlite3.Connection, batch_size: int = 1000) -> None:
    try:
        conn.execute("DELETE FROM chunks_fts")
    except sqlite3.OperationalError as exc:
        if optional_fts_error(exc):
            return
        raise
    cursor = conn.execute(
        """
        SELECT
            c.chunk_id,
            c.repo_id,
            c.repo_path,
            c.path,
            c.language,
            c.symbol_name,
            c.symbol_kind,
            c.symbol_line,
            c.symbol_confidence,
            c.start_line,
            c.end_line,
            c.commit_sha,
            c.content,
            c.content_hash,
            '' AS embedding,
            c.embedding_model,
            c.chunker_version,
            c.indexed_at
        FROM chunks c
        """
    )
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        insert_fts_rows(conn, rows)


def normalized_fts_text(text: str) -> str:
    return " ".join(tokenize_code(text))


def load_sqlite_vec(conn: sqlite3.Connection):  # type: ignore[no-untyped-def]
    try:
        import sqlite_vec

        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        return sqlite_vec
    except Exception:
        return None


def ensure_vector_table(
    conn: sqlite3.Connection,
    dimensions: int,
    *,
    rebuild_on_mismatch: bool = False,
) -> bool:
    sqlite_vec = load_sqlite_vec(conn)
    if sqlite_vec is None:
        return False
    current_dimensions = meta_value(conn, "vector_dimensions")
    if vector_table_incompatible(conn):
        if not rebuild_on_mismatch:
            return False
        conn.execute("DROP TABLE IF EXISTS chunk_vectors")
        set_meta_value(conn, "vector_dimensions", str(dimensions))
    elif current_dimensions and int(current_dimensions) != dimensions:
        if not rebuild_on_mismatch:
            return False
        conn.execute("DROP TABLE IF EXISTS chunk_vectors")
        set_meta_value(conn, "vector_dimensions", str(dimensions))
    elif not current_dimensions:
        set_meta_value(conn, "vector_dimensions", str(dimensions))
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE IF NOT EXISTS chunk_vectors USING vec0(
            embedding float[{dimensions}],
            chunk_id text,
            repo_id text
        )
        """
    )
    return True


def elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def attach_telemetry(
    rows: list[dict[str, object]],
    telemetry: dict[str, object],
) -> list[dict[str, object]]:
    for row in rows:
        row["telemetry"] = telemetry
    return rows


def vector_table_exists(conn: sqlite3.Connection) -> bool:
    return (
        conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_vectors'"
        ).fetchone()
        is not None
    )


def vector_table_incompatible(conn: sqlite3.Connection) -> bool:
    if not vector_table_exists(conn):
        return False
    try:
        columns = {item[1] for item in conn.execute("PRAGMA table_info(chunk_vectors)")}
    except sqlite3.OperationalError:
        return True
    return not {"chunk_id", "repo_id"}.issubset(columns)


def delete_vectors_for_repo(conn: sqlite3.Connection, repo_id: str) -> None:
    if not vector_table_exists(conn) or load_sqlite_vec(conn) is None:
        return
    try:
        conn.execute(
            """
            DELETE FROM chunk_vectors
            WHERE rowid IN (SELECT rowid FROM chunks WHERE repo_id = ?)
            """,
            (repo_id,),
        )
    except sqlite3.OperationalError:
        return


def delete_vectors_for_path(conn: sqlite3.Connection, repo_id: str, path: str) -> None:
    if not vector_table_exists(conn) or load_sqlite_vec(conn) is None:
        return
    try:
        conn.execute(
            """
            DELETE FROM chunk_vectors
            WHERE rowid IN (SELECT rowid FROM chunks WHERE repo_id = ? AND path = ?)
            """,
            (repo_id, path),
        )
    except sqlite3.OperationalError:
        return


def insert_vector_rows(conn: sqlite3.Connection, rows: Sequence[tuple[object, ...]]) -> None:
    if not rows or not should_maintain_vectors(conn):
        return
    embeddings = [json.loads(str(row[14])) for row in rows]
    dimensions = len(embeddings[0]) if embeddings else 0
    sqlite_vec = load_sqlite_vec(conn)
    if (
        sqlite_vec is None
        or not dimensions
        or not ensure_vector_table(conn, dimensions, rebuild_on_mismatch=False)
    ):
        clear_vector_coverage(conn, rows)
        return
    chunk_ids = [str(row[0]) for row in rows]
    placeholders = ", ".join("?" for _chunk_id in chunk_ids)
    rowids = {
        chunk_id: rowid
        for rowid, chunk_id in conn.execute(
            f"SELECT rowid, chunk_id FROM chunks WHERE chunk_id IN ({placeholders})",
            chunk_ids,
        )
    }
    try:
        conn.executemany(
            "INSERT INTO chunk_vectors(rowid, embedding, chunk_id, repo_id) VALUES (?, ?, ?, ?)",
            [
                (rowids[str(row[0])], sqlite_vec.serialize_float32(embedding), row[0], row[1])
                for row, embedding in zip(rows, embeddings, strict=True)
                if str(row[0]) in rowids
            ],
        )
    except sqlite3.OperationalError:
        clear_vector_coverage(conn, rows)
        return


def should_maintain_vectors(conn: sqlite3.Connection) -> bool:
    if os.environ.get("CODESCRY_MAINTAIN_VECTORS") == "1":
        return True
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='chunk_vectors'"
    ).fetchone()
    return row is not None


def vector_count(conn: sqlite3.Connection) -> int:
    try:
        load_sqlite_vec(conn)
        return int(conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0])
    except sqlite3.OperationalError:
        return 0


def insert_vector_backfill_rows(
    conn: sqlite3.Connection,
    rows: Sequence[tuple[object, ...]],
) -> int:
    if not rows:
        return 0
    embeddings = [json.loads(str(row[3])) for row in rows]
    dimensions = len(embeddings[0]) if embeddings else 0
    sqlite_vec = load_sqlite_vec(conn)
    if (
        sqlite_vec is None
        or not dimensions
        or not ensure_vector_table(conn, dimensions, rebuild_on_mismatch=True)
    ):
        return 0
    values = [
        (int(row[0]), sqlite_vec.serialize_float32(embedding), row[1], row[2])
        for row, embedding in zip(rows, embeddings, strict=True)
    ]
    try:
        conn.executemany(
            "INSERT INTO chunk_vectors(rowid, embedding, chunk_id, repo_id) VALUES (?, ?, ?, ?)",
            values,
        )
    except sqlite3.OperationalError:
        return 0
    return len(values)


def backfill_vectors(conn: sqlite3.Connection, batch_size: int = 1000) -> int:
    first = conn.execute("SELECT embedding FROM chunks LIMIT 1").fetchone()
    if first is None:
        return 0
    dimensions = len(json.loads(first[0]))
    if not ensure_vector_table(conn, dimensions, rebuild_on_mismatch=True):
        return 0
    try:
        conn.execute("DELETE FROM chunk_vectors")
    except sqlite3.OperationalError:
        return 0
    total = 0
    last_rowid = 0
    while True:
        rows = conn.execute(
            """
            SELECT c.rowid, c.chunk_id, c.repo_id, c.embedding
            FROM chunks c
            WHERE c.rowid > ?
            ORDER BY c.rowid
            LIMIT ?
            """,
            (last_rowid, batch_size),
        ).fetchall()
        if not rows:
            mark_vector_coverage_complete_if_counts_match(conn)
            return total
        last_rowid = int(rows[-1][0])
        total += insert_vector_backfill_rows(conn, rows)


def vector_scores(
    conn: sqlite3.Connection,
    *,
    query_embedding: list[float],
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_VECTOR_CANDIDATE_LIMIT,
) -> dict[int, float]:
    if repo or path_prefix or language:
        return {
            candidate.rowid: candidate.backend_score
            for candidate in ranked_vector_candidates(
                conn,
                query_embedding=query_embedding,
                embedding_model=embedding_model,
                repo=repo,
                path_prefix=path_prefix,
                language=language,
                limit=limit,
            )
        }
    sqlite_vec = load_sqlite_vec(conn)
    if sqlite_vec is None or not ensure_vector_table(conn, len(query_embedding)):
        return {}
    where = ["v.embedding MATCH ?", "k = ?", "c.embedding_model = ?"]
    params: list[object] = [sqlite_vec.serialize_float32(query_embedding), limit, embedding_model]
    sql = f"""
        SELECT v.rowid, v.distance
        FROM chunk_vectors v
        JOIN chunks c ON c.rowid = v.rowid AND c.chunk_id = v.chunk_id
        WHERE {' AND '.join(where)}
        ORDER BY v.distance
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return {}
    if not rows:
        return {}
    distances = [float(row[1]) for row in rows]
    best = min(distances)
    worst = max(distances)
    if best == worst:
        return {int(row[0]): 1.0 for row in rows}
    return {int(row[0]): (worst - float(row[1])) / (worst - best) for row in rows}


def ranked_vector_candidates(
    conn: sqlite3.Connection,
    *,
    query_embedding: list[float],
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_VECTOR_CANDIDATE_LIMIT,
) -> list[RankedCandidate]:
    if repo or path_prefix or language:
        return ranked_vector_candidates_by_overfetch(
            conn,
            query_embedding=query_embedding,
            embedding_model=embedding_model,
            repo=repo,
            path_prefix=path_prefix,
            language=language,
            limit=limit,
        )
    sqlite_vec = load_sqlite_vec(conn)
    if sqlite_vec is None or not ensure_vector_table(conn, len(query_embedding)):
        return []
    sql = """
        SELECT c.rowid, v.distance, c.path, c.start_line
        FROM chunk_vectors v
        JOIN chunks c ON c.rowid = v.rowid AND c.chunk_id = v.chunk_id
        WHERE v.embedding MATCH ? AND k = ? AND c.embedding_model = ?
        ORDER BY v.distance ASC, c.path ASC, c.start_line ASC, c.rowid ASC
    """
    params: list[object] = [sqlite_vec.serialize_float32(query_embedding), limit, embedding_model]
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []
    return [
        RankedCandidate(
            rowid=int(row[0]),
            backend_score=1.0 / (1.0 + max(float(row[1]), 0.0)),
            path=str(row[2]),
            start_line=int(row[3]),
        )
        for row in rows
    ]


def ranked_vector_candidates_by_overfetch(
    conn: sqlite3.Connection,
    *,
    query_embedding: list[float],
    embedding_model: str,
    repo: str | None,
    path_prefix: str | None,
    language: str | None,
    limit: int,
) -> list[RankedCandidate]:
    sqlite_vec = load_sqlite_vec(conn)
    if sqlite_vec is None or not ensure_vector_table(conn, len(query_embedding)):
        return []
    overfetch = min(RRF_FILTERED_VECTOR_OVERFETCH_LIMIT, max(limit, DEFAULT_VECTOR_CANDIDATE_LIMIT))
    try:
        vector_rows = conn.execute(
            """
            SELECT rowid, distance
            FROM chunk_vectors
            WHERE embedding MATCH ? AND k = ?
            ORDER BY distance ASC
            """,
            (sqlite_vec.serialize_float32(query_embedding), overfetch),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    if not vector_rows:
        return []

    distances = {int(row[0]): float(row[1]) for row in vector_rows}
    rowids = list(distances)
    placeholders = ",".join("?" for _ in rowids)
    where = [f"c.rowid IN ({placeholders})", "c.embedding_model = ?"]
    params: list[object] = [*rowids, embedding_model]
    if repo:
        where.append("(c.repo_id = ? OR c.repo_path = ?)")
        params.extend([repo, repo])
    if path_prefix:
        where.append("c.path LIKE ?")
        params.append(f"{path_prefix}%")
    if language:
        where.append("c.language = ?")
        params.append(language)
    sql = f"""
        SELECT c.rowid, c.path, c.start_line
        FROM chunks c
        WHERE {' AND '.join(where)}
    """
    candidates = []
    for row in conn.execute(sql, params):
        rowid = int(row[0])
        distance = distances[rowid]
        candidates.append(
            (rowid, 1.0 / (1.0 + max(distance, 0.0)), str(row[1]), int(row[2]), distance)
        )
    candidates.sort(key=lambda item: (item[4], item[2], item[3], item[0]))
    return [
        RankedCandidate(rowid=rowid, backend_score=score, path=path, start_line=start_line)
        for rowid, score, path, start_line, _distance in candidates[:limit]
    ]


def filtered_chunk_count(
    conn: sqlite3.Connection,
    *,
    embedding_model: str,
    repo: str | None,
    path_prefix: str | None,
    language: str | None,
) -> tuple[int, list[str], list[object]]:
    where = ["c.embedding_model = ?"]
    params: list[object] = [embedding_model]
    if repo:
        where.append("(c.repo_id = ? OR c.repo_path = ?)")
        params.extend([repo, repo])
    if path_prefix:
        where.append("c.path LIKE ?")
        params.append(f"{path_prefix}%")
    if language:
        where.append("c.language = ?")
        params.append(language)
    count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM chunks c WHERE {' AND '.join(where)}",
            params,
        ).fetchone()[0]
    )
    return count, where, params


def ranked_path_candidates(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_FTS_CANDIDATE_LIMIT,
) -> list[RankedCandidate]:
    count, where, params = filtered_chunk_count(
        conn,
        embedding_model=embedding_model,
        repo=repo,
        path_prefix=path_prefix,
        language=language,
    )
    if count > RRF_EXACT_SCAN_LIMIT:
        return []
    sql = f"""
        SELECT c.rowid, c.path, c.start_line
        FROM chunks c
        WHERE {' AND '.join(where)}
    """
    candidates = []
    for row in conn.execute(sql, params):
        path = str(row[1])
        score = path_exact_score(query_text, path)
        if score > 0:
            candidates.append((int(row[0]), score, path, int(row[2])))
    candidates.sort(key=lambda item: (-item[1], item[2], item[3], item[0]))
    return [
        RankedCandidate(rowid=rowid, backend_score=score, path=path, start_line=start_line)
        for rowid, score, path, start_line in candidates[:limit]
    ]


def ranked_symbol_candidates(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_FTS_CANDIDATE_LIMIT,
) -> list[RankedCandidate]:
    count, where, params = filtered_chunk_count(
        conn,
        embedding_model=embedding_model,
        repo=repo,
        path_prefix=path_prefix,
        language=language,
    )
    if count > RRF_EXACT_SCAN_LIMIT:
        return []
    where.append("c.symbol_name IS NOT NULL")
    sql = f"""
        SELECT c.rowid, c.path, c.start_line, c.symbol_name, c.symbol_confidence
        FROM chunks c
        WHERE {' AND '.join(where)}
    """
    candidates = []
    for row in conn.execute(sql, params):
        score = symbol_match_score(query_text, str(row[3]) if row[3] else None)
        if row[4] == "parser" and score > 0:
            score += 0.25
        if score > 0:
            candidates.append((int(row[0]), score, str(row[1]), int(row[2])))
    candidates.sort(key=lambda item: (-item[1], item[2], item[3], item[0]))
    return [
        RankedCandidate(rowid=rowid, backend_score=score, path=path, start_line=start_line)
        for rowid, score, path, start_line in candidates[:limit]
    ]


def ranked_lexical_candidates(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_FTS_CANDIDATE_LIMIT,
) -> list[RankedCandidate]:
    count, where, params = filtered_chunk_count(
        conn,
        embedding_model=embedding_model,
        repo=repo,
        path_prefix=path_prefix,
        language=language,
    )
    if count > RRF_EXACT_SCAN_LIMIT:
        return []
    sql = f"""
        SELECT c.rowid, c.path, c.start_line, c.content
        FROM chunks c
        WHERE {' AND '.join(where)}
    """
    candidates = []
    for row in conn.execute(sql, params):
        score = token_overlap(query_text, str(row[3]))
        if score > 0:
            candidates.append((int(row[0]), score, str(row[1]), int(row[2])))
    candidates.sort(key=lambda item: (-item[1], item[2], item[3], item[0]))
    return [
        RankedCandidate(rowid=rowid, backend_score=score, path=path, start_line=start_line)
        for rowid, score, path, start_line in candidates[:limit]
    ]


def ranked_fts_candidates(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_FTS_CANDIDATE_LIMIT,
) -> list[RankedCandidate]:
    fts_query = fts_query_for(query_text)
    if not fts_query:
        return []
    where = ["chunks_fts MATCH ?", "c.embedding_model = ?"]
    params: list[object] = [fts_query, embedding_model]
    if repo:
        where.append("(c.repo_id = ? OR c.repo_path = ?)")
        params.extend([repo, repo])
    if path_prefix:
        where.append("c.path LIKE ?")
        params.append(f"{path_prefix}%")
    if language:
        where.append("c.language = ?")
        params.append(language)
    params.append(limit)
    sql = f"""
        SELECT c.rowid, bm25(chunks_fts) AS rank, c.path, c.start_line
        FROM chunks_fts f
        JOIN chunks c ON c.chunk_id = f.chunk_id
        WHERE {' AND '.join(where)}
        ORDER BY rank ASC, c.path ASC, c.start_line ASC, c.rowid ASC
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() or "no such table" in str(exc).lower():
            return []
        raise
    return [
        RankedCandidate(
            rowid=int(row[0]),
            backend_score=abs(float(row[1])) / (1.0 + abs(float(row[1]))),
            path=str(row[2]),
            start_line=int(row[3]),
        )
        for row in rows
    ]


def rrf_ranking_enabled() -> bool:
    return os.environ.get("CODESCRY_RRF_RANKING") == "1"


def rrf_top_rank_bonus_enabled() -> bool:
    return (
        os.environ.get("CODESCRY_RRF_TOP_BONUS") == "1"
        or os.environ.get("CODESCRY_RRF_QMD_STYLE") == "1"
    )


def rrf_exact_lists_enabled() -> bool:
    return (
        os.environ.get("CODESCRY_RRF_EXACT_LISTS") == "1"
        or os.environ.get("CODESCRY_RRF_QMD_STYLE") == "1"
    )


def path_exact_score(query_text: str, path: str) -> float:
    normalized_query = query_text.lower().strip().replace(" ", "_")
    normalized_path = path.lower().replace("/", " ").replace(".", " ").replace("-", "_")
    if normalized_query and normalized_query in normalized_path.replace(" ", "_"):
        return 2.0
    return token_overlap(query_text, normalized_path)


def rrf_fuse(
    *,
    fts_candidates: Sequence[RankedCandidate],
    vector_candidates: Sequence[RankedCandidate],
    extra_sources: Sequence[tuple[str, Sequence[RankedCandidate]]] = (),
    top_rank_bonus: bool = False,
    k: int = RRF_K,
) -> tuple[list[int], dict[int, dict[str, object]]]:
    candidate_by_rowid: dict[int, RankedCandidate] = {}
    totals: dict[int, float] = {}
    best_rank: dict[int, int] = {}
    source_ranks: dict[int, dict[str, int]] = {}
    source_contributions: dict[int, dict[str, float]] = {}

    sources: list[tuple[str, Sequence[RankedCandidate]]] = [
        ("fts", fts_candidates),
        ("vector", vector_candidates),
        *extra_sources,
    ]
    for source_name, candidates in sources:
        for rank, candidate in enumerate(candidates, start=1):
            candidate_by_rowid.setdefault(candidate.rowid, candidate)
            best_rank[candidate.rowid] = min(rank, best_rank.get(candidate.rowid, rank))
            contribution = 1.0 / (k + rank)
            totals[candidate.rowid] = totals.get(candidate.rowid, 0.0) + contribution
            source_ranks.setdefault(candidate.rowid, {})[source_name] = rank
            source_contributions.setdefault(candidate.rowid, {})[source_name] = contribution

    top_rank_bonuses: dict[int, float] = {}
    if top_rank_bonus:
        for rowid, rank in best_rank.items():
            bonus = 0.0
            if rank == 1:
                bonus = RRF_TOP_RANK_BONUS
            elif rank <= 3:
                bonus = RRF_NEAR_TOP_RANK_BONUS
            if bonus:
                totals[rowid] += bonus
                top_rank_bonuses[rowid] = bonus

    ordered = sorted(
        totals,
        key=lambda rowid: (
            -totals[rowid],
            candidate_by_rowid[rowid].path,
            candidate_by_rowid[rowid].start_line,
            rowid,
        ),
    )
    traces = {
        rowid: {
            "ranking_mode": "rrf_v1",
            "rrf_score": totals[rowid],
            "source_ranks": source_ranks.get(rowid, {}),
            "source_contributions": source_contributions.get(rowid, {}),
            "top_rank_bonus": top_rank_bonuses.get(rowid, 0.0),
        }
        for rowid in ordered
    }
    return ordered, traces


def chunk_id_for(chunk: Chunk) -> str:
    content_hash = content_hash_for(chunk.content)
    h = hashlib.sha256()
    h.update(chunk.repo_id.encode("utf-8"))
    h.update(b"\0")
    h.update(chunk.path.encode("utf-8"))
    h.update(b"\0")
    h.update(str(chunk.start_line).encode("ascii"))
    h.update(b"\0")
    h.update(str(chunk.end_line).encode("ascii"))
    h.update(b"\0")
    h.update((chunk.symbol_name or "").encode("utf-8"))
    h.update(b"\0")
    h.update((chunk.symbol_confidence or "").encode("utf-8"))
    h.update(b"\0")
    h.update(content_hash.encode("ascii"))
    return h.hexdigest()


def content_hash_for(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def hybrid_score(
    *,
    query_text: str,
    vector_score: float,
    path: str,
    content: str,
    symbol_name: str | None,
    bm25_score: float = 0.0,
) -> float:
    return float(
        score_breakdown(
            query_text=query_text,
            vector_score=vector_score,
            path=path,
            content=content,
            symbol_name=symbol_name,
            bm25_score=bm25_score,
        )["base"]
    )


def adjusted_hybrid_score(
    *,
    query_text: str,
    vector_score: float,
    path: str,
    content: str,
    symbol_name: str | None,
    bm25_score: float = 0.0,
) -> float:
    return float(
        score_breakdown(
            query_text=query_text,
            vector_score=vector_score,
            path=path,
            content=content,
            symbol_name=symbol_name,
            bm25_score=bm25_score,
        )["score"]
    )


def score_breakdown(
    *,
    query_text: str,
    vector_score: float,
    path: str,
    content: str,
    symbol_name: str | None,
    bm25_score: float = 0.0,
) -> dict[str, float]:
    normalized_vector = max(0.0, min(1.0, (vector_score + 1.0) / 2.0))
    lexical = token_overlap(query_text, content)
    symbol = symbol_match_score(query_text, symbol_name)
    path_score = token_overlap(query_text, path.replace("/", " ").replace(".", " "))
    base = (
        (0.50 * normalized_vector)
        + (0.20 * lexical)
        + (0.14 * bm25_score)
        + (0.09 * symbol)
        + (0.07 * path_score)
    )
    multiplier = path_rank_multiplier(query_text, path)
    return {
        "raw_vector": vector_score,
        "normalized_vector": normalized_vector,
        "lexical": lexical,
        "symbol": symbol,
        "path": path_score,
        "bm25": bm25_score,
        "path_multiplier": multiplier,
        "base": base,
        "score": base * multiplier,
    }


def path_rank_multiplier(query_text: str, path: str) -> float:
    normalized = path.lower()
    if wants_docs_query(query_text):
        return 1.0
    if is_generated_path(normalized):
        return 0.55
    if is_docs_path(normalized):
        return 0.70
    if is_test_path(normalized) and not wants_test_query(query_text):
        return 0.65
    return 1.0


def wants_docs_query(query_text: str) -> bool:
    normalized = query_text.lower()
    if any(keyword in normalized for keyword in ("prd", "sedd", "readme")):
        return True
    query_tokens = set(tokenize_code(query_text))
    explicit_doc_tokens = {
        "doc",
        "docs",
        "plan",
        "phase",
        "install",
        "config",
        "security",
        "pilot",
    }
    if query_tokens & explicit_doc_tokens:
        return True
    return False


def is_docs_path(path: str) -> bool:
    name = path.rsplit("/", maxsplit=1)[-1]
    return path.startswith("docs/") or name in {"readme.md", "security.md"}


def wants_test_query(query_text: str) -> bool:
    query_tokens = set(tokenize_code(query_text))
    return bool(query_tokens & {"test", "tests", "testing", "pytest", "spec", "assert"})


def is_test_path(path: str) -> bool:
    name = path.rsplit("/", maxsplit=1)[-1]
    return (
        path.startswith("tests/")
        or path.startswith("testing/")
        or "/tests/" in path
        or "/testing/" in path
        or name.startswith("test_")
        or name.endswith("_test.py")
    )


def is_generated_path(path: str) -> bool:
    return (
        path.startswith("evals/results/")
        or path.endswith(".pb.go")
        or path.endswith(".pb.gw.go")
        or path.endswith("_pb2.py")
        or path.endswith("_pb2_grpc.py")
        or "/generated/" in path
    )


def should_try_candidate_union(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None,
    path_prefix: str | None,
    language: str | None,
    k: int | None,
) -> bool:
    if os.environ.get("CODESCRY_DISABLE_CANDIDATE_UNION") == "1":
        return False
    if k is None or wants_docs_query(query_text):
        return False
    if estimated_indexed_chunks(conn) < candidate_threshold():
        return False
    return vector_coverage_complete(conn, embedding_model=embedding_model)


def should_use_candidate_union(
    *,
    vector_scores: dict[int, float],
    bm25_scores: dict[int, float],
    k: int | None,
) -> bool:
    if k is None or not vector_scores:
        return False
    if not bm25_scores:
        return len(vector_scores) >= k
    return len(set(vector_scores) | set(bm25_scores)) >= max(50, k * 10)


def estimated_indexed_chunks(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COALESCE(SUM(chunk_count), 0) FROM indexed_files").fetchone()
    return int(row[0])


def vector_coverage_complete(conn: sqlite3.Connection, *, embedding_model: str) -> bool:
    return meta_value(conn, f"vector_coverage:{embedding_model}") == "complete"


def mark_vector_coverage_complete_if_counts_match(conn: sqlite3.Connection) -> None:
    rows = conn.execute("SELECT DISTINCT embedding_model FROM chunks").fetchall()
    for (embedding_model,) in rows:
        if vector_coverage_count(conn, embedding_model=embedding_model) == matching_chunk_count(
            conn,
            embedding_model=embedding_model,
        ):
            set_meta_value(conn, f"vector_coverage:{embedding_model}", "complete")
        else:
            set_meta_value(conn, f"vector_coverage:{embedding_model}", "incomplete")


def clear_vector_coverage(conn: sqlite3.Connection, rows: Sequence[tuple[object, ...]]) -> None:
    for embedding_model in {str(row[15]) for row in rows if len(row) > 15}:
        set_meta_value(conn, f"vector_coverage:{embedding_model}", "incomplete")


def vector_coverage_count(conn: sqlite3.Connection, *, embedding_model: str) -> int:
    try:
        load_sqlite_vec(conn)
        return int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM chunk_vectors v
                JOIN chunks c ON c.rowid = v.rowid AND c.chunk_id = v.chunk_id
                WHERE c.embedding_model = ?
                """,
                (embedding_model,),
            ).fetchone()[0]
        )
    except sqlite3.OperationalError:
        return 0


def matching_chunk_count(conn: sqlite3.Connection, *, embedding_model: str) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM chunks WHERE embedding_model = ?",
            (embedding_model,),
        ).fetchone()[0]
    )


def candidate_threshold() -> int:
    configured = os.environ.get("CODESCRY_CANDIDATE_THRESHOLD", DEFAULT_CANDIDATE_THRESHOLD)
    return int(configured)


def fts_scores(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = DEFAULT_FTS_CANDIDATE_LIMIT,
) -> dict[int, float]:
    fts_query = fts_query_for(query_text)
    if not fts_query:
        return {}
    where = ["chunks_fts MATCH ?", "c.embedding_model = ?"]
    params: list[object] = [fts_query, embedding_model]
    if repo:
        where.append("(c.repo_id = ? OR c.repo_path = ?)")
        params.extend([repo, repo])
    if path_prefix:
        where.append("c.path LIKE ?")
        params.append(f"{path_prefix}%")
    if language:
        where.append("c.language = ?")
        params.append(language)
    params.append(limit)
    sql = f"""
        SELECT c.rowid, bm25(chunks_fts) AS rank
        FROM chunks_fts f
        JOIN chunks c ON c.chunk_id = f.chunk_id
        WHERE {' AND '.join(where)}
        ORDER BY rank
        LIMIT ?
    """
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() or "no such table" in str(exc).lower():
            return {}
        raise
    if not rows:
        return {}
    ranks = [float(row[1]) for row in rows]
    best = min(ranks)
    worst = max(ranks)
    if best == worst:
        confidence = 1.0 if len(rows) == 1 else 0.25
        return {int(row[0]): confidence for row in rows}
    return {int(row[0]): (worst - float(row[1])) / (worst - best) for row in rows}


def expand_query_text(query_text: str) -> str:
    tokens = tokenize_code(query_text)
    expanded: list[str] = list(tokens)
    for token in tokens:
        expanded.extend(sorted(QUERY_EXPANSIONS.get(token, ())))
    return " ".join(dict.fromkeys(expanded))


def fts_query_for(query_text: str) -> str:
    tokens = [
        token
        for token in tokenize_code(expand_query_text(query_text))
        if len(token) > 2 and token not in FTS_STOPWORDS
    ]
    return " OR ".join(dict.fromkeys(tokens[:16]))


def diversify_results(
    results: list[SearchResult],
    k: int,
    max_per_file: int = 2,
) -> list[SearchResult]:
    selected: list[SearchResult] = []
    counts: dict[tuple[str, str], int] = {}
    deferred: list[SearchResult] = []
    for result in results:
        key = (result.repo, result.path)
        if counts.get(key, 0) < max_per_file:
            selected.append(result)
            counts[key] = counts.get(key, 0) + 1
        else:
            deferred.append(result)
        if len(selected) == k:
            return selected
    return (selected + deferred)[:k]


def token_overlap(query_text: str, candidate_text: str) -> float:
    query_tokens = set(tokenize_code(query_text))
    if not query_tokens:
        return 0.0
    candidate_tokens = set(tokenize_code(candidate_text))
    return len(query_tokens & candidate_tokens) / len(query_tokens)


def symbol_match_score(query_text: str, symbol_name: str | None) -> float:
    if not symbol_name:
        return 0.0
    query_tokens = set(tokenize_code(query_text))
    symbol_tokens = set(tokenize_code(symbol_name))
    if not query_tokens or not symbol_tokens:
        return 0.0
    if symbol_name.lower() in query_text.lower():
        return 1.0
    return len(query_tokens & symbol_tokens) / len(symbol_tokens)


def search_sort_key(result: SearchResult) -> tuple[float, int, int, int]:
    confidence = 1 if result.symbol_confidence == "parser" else 0
    return (result.score, confidence, -len(result.path), -result.start_line)


def symbol_rank(
    name: str,
    symbol_name: str | None,
    confidence: str | None,
) -> tuple[int, int, int] | None:
    if not symbol_name:
        return None
    requested = name.lower()
    candidate = symbol_name.lower()
    if requested == candidate:
        match_rank = 0
    elif requested in candidate:
        match_rank = 1
    else:
        return None
    confidence_rank = 0 if confidence == "parser" else 1
    return (match_rank, confidence_rank, len(symbol_name))


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
