from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from repo_index_mcp.embeddings import cosine_similarity, tokenize_code
from repo_index_mcp.models import Chunk, SearchResult

BUSY_TIMEOUT_MS = 5000
FTS_INDEX_VERSION = "fts-v1"
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


class SQLiteStorage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def cleanup_repo_path_aliases(self, *, repo_id: str, repo_path: str) -> int:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT repo_id FROM repos WHERE repo_path = ? AND repo_id != ?",
                (repo_path, repo_id),
            ).fetchall()
            old_ids = [row[0] for row in rows]
            for old_id in old_ids:
                delete_fts_for_repo(conn, old_id)
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
            conn.execute("DELETE FROM chunks WHERE repo_id = ?", (repo_id,))
            conn.execute("DELETE FROM indexed_files WHERE repo_id = ?", (repo_id,))

    def delete_paths(self, *, repo_id: str, paths: Sequence[str]) -> int:
        if not paths:
            return 0
        with self._connect() as conn:
            deleted_chunks = 0
            for path in paths:
                delete_fts_for_path(conn, repo_id, path)
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
        where = ["embedding_model = ?"]
        params: list[str] = [embedding_model]
        if repo:
            where.append("(repo_id = ? OR repo_path = ?)")
            params.extend([repo, repo])
        if path_prefix:
            where.append("path LIKE ?")
            params.append(f"{path_prefix}%")
        if language:
            where.append("language = ?")
            params.append(language)

        sql = f"""
            SELECT
                chunk_id,
                repo_id,
                path,
                start_line,
                end_line,
                content,
                embedding,
                language,
                symbol_name,
                symbol_kind,
                symbol_line,
                symbol_confidence
            FROM chunks
            WHERE {' AND '.join(where)}
        """
        rows: list[dict[str, object]] = []
        with self._connect() as conn:
            bm25_scores = fts_scores(
                conn,
                query_text=query_text,
                embedding_model=embedding_model,
                repo=repo,
                path_prefix=path_prefix,
                language=language,
            )
            for row in conn.execute(sql, params):
                embedding = json.loads(row[6])
                vector_score = cosine_similarity(query_embedding, embedding)
                parts = score_breakdown(
                    query_text=query_text,
                    vector_score=vector_score,
                    path=row[2],
                    content=row[5],
                    symbol_name=row[8],
                    bm25_score=bm25_scores.get(row[0], 0.0),
                )
                result = SearchResult(
                    repo=row[1],
                    path=row[2],
                    start_line=row[3],
                    end_line=row[4],
                    snippet=row[5],
                    score=float(parts["score"]),
                    language=row[7],
                    symbol_name=row[8],
                    symbol_kind=row[9],
                    symbol_line=row[10],
                    symbol_confidence=row[11],
                )
                rows.append({"result": result, "score": parts})

        rows.sort(key=lambda item: search_sort_key(item["result"]), reverse=True)
        if k is None:
            return rows
        if wants_docs_query(query_text):
            return rows[:k]
        selected = diversify_results([item["result"] for item in rows], k)
        debug_by_result_id = {id(item["result"]): item for item in rows}
        return [debug_by_result_id[id(result)] for result in selected]

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
            SELECT chunk_id, path, content, embedding, symbol_name
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
            for chunk_id, path, content, embedding_json, symbol_name in conn.execute(sql, params):
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
                        bm25_score=bm25_scores.get(chunk_id, 0.0),
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
    return 1.0


def wants_docs_query(query_text: str) -> bool:
    normalized = query_text.lower()
    if any(keyword in normalized for keyword in ("prd", "sedd", "readme")):
        return True
    query_tokens = set(tokenize_code(query_text))
    return bool(
        query_tokens
        & {
            "doc",
            "docs",
            "plan",
            "phase",
            "install",
            "mcp",
            "config",
            "security",
            "pilot",
        }
    )


def is_docs_path(path: str) -> bool:
    name = path.rsplit("/", maxsplit=1)[-1]
    return path.startswith("docs/") or name in {"readme.md", "security.md"}


def is_generated_path(path: str) -> bool:
    return (
        path.startswith("evals/results/")
        or path.endswith(".pb.go")
        or path.endswith(".pb.gw.go")
        or path.endswith("_pb2.py")
        or path.endswith("_pb2_grpc.py")
        or "/generated/" in path
    )


def fts_scores(
    conn: sqlite3.Connection,
    *,
    query_text: str,
    embedding_model: str,
    repo: str | None = None,
    path_prefix: str | None = None,
    language: str | None = None,
    limit: int = 200,
) -> dict[str, float]:
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
        SELECT f.chunk_id, bm25(chunks_fts) AS rank
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
        return {row[0]: confidence for row in rows}
    return {row[0]: (worst - float(row[1])) / (worst - best) for row in rows}


def fts_query_for(query_text: str) -> str:
    tokens = [
        token
        for token in tokenize_code(query_text)
        if len(token) > 2 and token not in FTS_STOPWORDS
    ]
    return " OR ".join(dict.fromkeys(tokens[:12]))


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
