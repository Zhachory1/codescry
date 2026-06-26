from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from repo_index_mcp.embeddings import cosine_similarity
from repo_index_mcp.models import Chunk, SearchResult


class SQLiteStorage:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def upsert_repo(self, *, repo_id: str, repo_path: str, commit_sha: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO repos(repo_id, repo_path, last_commit_sha, indexed_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(repo_id) DO UPDATE SET
                    repo_path = excluded.repo_path,
                    last_commit_sha = excluded.last_commit_sha,
                    indexed_at = excluded.indexed_at
                """,
                (repo_id, repo_path, commit_sha, now_iso()),
            )

    def replace_chunks(
        self,
        *,
        repo_id: str,
        chunks: Iterable[Chunk],
        embeddings: Iterable[list[float]],
        commit_sha: str,
        embedding_model: str,
    ) -> int:
        rows = []
        indexed_at = now_iso()
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            rows.append(
                (
                    chunk_id_for(chunk),
                    chunk.repo_id,
                    chunk.repo_path,
                    chunk.path,
                    chunk.language,
                    chunk.symbol_name,
                    chunk.start_line,
                    chunk.end_line,
                    commit_sha,
                    chunk.content,
                    json.dumps(embedding),
                    embedding_model,
                    indexed_at,
                )
            )

        with self._connect() as conn:
            conn.execute("DELETE FROM chunks WHERE repo_id = ?", (repo_id,))
            conn.executemany(
                """
                INSERT INTO chunks(
                    chunk_id,
                    repo_id,
                    repo_path,
                    path,
                    language,
                    symbol_name,
                    start_line,
                    end_line,
                    commit_sha,
                    content,
                    embedding,
                    embedding_model,
                    indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def search(
        self,
        *,
        query_embedding: list[float],
        embedding_model: str,
        k: int,
        repo: str | None = None,
        path_prefix: str | None = None,
        language: str | None = None,
    ) -> list[SearchResult]:
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
            SELECT repo_id, path, start_line, end_line, content, embedding, language, symbol_name
            FROM chunks
            WHERE {' AND '.join(where)}
        """
        results: list[SearchResult] = []
        with self._connect() as conn:
            for row in conn.execute(sql, params):
                embedding = json.loads(row[5])
                score = cosine_similarity(query_embedding, embedding)
                results.append(
                    SearchResult(
                        repo=row[0],
                        path=row[1],
                        start_line=row[2],
                        end_line=row[3],
                        snippet=row[4],
                        score=score,
                        language=row[6],
                        symbol_name=row[7],
                    )
                )

        results.sort(key=lambda item: item.score, reverse=True)
        return results[:k]

    def list_repos(self) -> list[dict[str, object]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT r.repo_id, r.repo_path, r.last_commit_sha, r.indexed_at, COUNT(c.chunk_id)
                FROM repos r
                LEFT JOIN chunks c ON c.repo_id = r.repo_id
                GROUP BY r.repo_id, r.repo_path, r.last_commit_sha, r.indexed_at
                ORDER BY r.repo_id
                """
            ).fetchall()
        return [
            {
                "repo_id": row[0],
                "repo_path": row[1],
                "last_commit_sha": row[2],
                "indexed_at": row[3],
                "chunk_count": row[4],
                "is_stale": False,
            }
            for row in rows
        ]

    def repo_paths(self) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT repo_path FROM repos ORDER BY repo_id").fetchall()
        return [row[0] for row in rows]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS repos (
                    repo_id TEXT PRIMARY KEY,
                    repo_path TEXT NOT NULL,
                    last_commit_sha TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    repo_id TEXT NOT NULL,
                    repo_path TEXT NOT NULL,
                    path TEXT NOT NULL,
                    language TEXT NOT NULL,
                    symbol_name TEXT,
                    start_line INTEGER NOT NULL,
                    end_line INTEGER NOT NULL,
                    commit_sha TEXT NOT NULL,
                    content TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    embedding_model TEXT NOT NULL,
                    indexed_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_chunks_repo ON chunks(repo_id);
                CREATE INDEX IF NOT EXISTS idx_chunks_path ON chunks(path);
                CREATE INDEX IF NOT EXISTS idx_chunks_language ON chunks(language);
                CREATE INDEX IF NOT EXISTS idx_chunks_model ON chunks(embedding_model);
                """
            )


def chunk_id_for(chunk: Chunk) -> str:
    h = hashlib.sha256()
    h.update(chunk.repo_id.encode("utf-8"))
    h.update(b"\0")
    h.update(chunk.path.encode("utf-8"))
    h.update(b"\0")
    h.update(str(chunk.start_line).encode("ascii"))
    h.update(b"\0")
    h.update(chunk.content.encode("utf-8"))
    return h.hexdigest()


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
