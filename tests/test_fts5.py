from repo_index_mcp.embeddings import HashEmbeddingProvider
from repo_index_mcp.models import Chunk
from repo_index_mcp.storage import SQLiteStorage, fts_scores, score_breakdown


def test_fts_scores_rank_matching_chunk(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/retry.py",
        language="python",
        symbol_name="retry_request",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=2,
        content="def retry_request():\n    return 'backoff'",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="src/retry.py",
        content_hash="hash",
        chunks=[chunk],
        embeddings=[HashEmbeddingProvider().embed(chunk.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )

    with storage._connect() as conn:  # noqa: SLF001
        scores = fts_scores(
            conn,
            query_text="retry backoff",
            embedding_model="model",
        )

    assert scores
    assert max(scores.values()) == 1.0


def test_fts_splits_camel_case_like_query_tokenizer(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/retry.ts",
        language="typescript",
        symbol_name="retryRequest",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="function retryRequest() {}",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="src/retry.ts",
        content_hash="hash",
        chunks=[chunk],
        embeddings=[HashEmbeddingProvider().embed(chunk.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )

    with storage._connect() as conn:  # noqa: SLF001
        scores = fts_scores(conn, query_text="retry request", embedding_model="model")

    assert scores


def test_fts_backfills_existing_chunks(tmp_path):  # type: ignore[no-untyped-def]
    db_path = tmp_path / "index.sqlite"
    storage = SQLiteStorage(db_path)
    chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/retry.py",
        language="python",
        symbol_name="retry_request",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def retry_request(): pass",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="src/retry.py",
        content_hash="hash",
        chunks=[chunk],
        embeddings=[HashEmbeddingProvider().embed(chunk.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )
    with storage._connect() as conn:  # noqa: SLF001
        conn.execute("DELETE FROM chunks_fts")
        conn.execute("DELETE FROM storage_meta WHERE key = 'fts_index_version'")

    SQLiteStorage(db_path)
    with storage._connect() as conn:  # noqa: SLF001
        count = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    assert count == 1


def test_alias_cleanup_removes_fts_rows(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    chunk = Chunk(
        repo_id="old",
        repo_path="/repo",
        path="src/retry.py",
        language="python",
        symbol_name="retry_request",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def retry_request(): pass",
    )
    storage.record_repo_success(repo_id="old", repo_path="/repo", commit_sha="a", remote_url="")
    storage.replace_file_chunks(
        repo_id="old",
        path="src/retry.py",
        content_hash="hash",
        chunks=[chunk],
        embeddings=[HashEmbeddingProvider().embed(chunk.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )

    storage.cleanup_repo_path_aliases(repo_id="new", repo_path="/repo")

    with storage._connect() as conn:  # noqa: SLF001
        count = conn.execute("SELECT COUNT(*) FROM chunks_fts WHERE repo_id = 'old'").fetchone()[0]
    assert count == 0


def test_fts_rows_replace_and_delete_by_raw_chunk_path(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    first = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/retry.py",
        language="python",
        symbol_name="retry_request",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def retry_request(): pass",
    )
    second = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/retry.py",
        language="python",
        symbol_name="retry_request",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def retry_request(): return True",
    )
    provider = HashEmbeddingProvider()

    storage.replace_file_chunks(
        repo_id="repo",
        path="src/retry.py",
        content_hash="one",
        chunks=[first],
        embeddings=[provider.embed(first.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="src/retry.py",
        content_hash="two",
        chunks=[second],
        embeddings=[provider.embed(second.content)],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )
    with storage._connect() as conn:  # noqa: SLF001
        count_after_replace = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    storage.delete_paths(repo_id="repo", paths=["src/retry.py"])
    with storage._connect() as conn:  # noqa: SLF001
        count_after_delete = conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]

    assert count_after_replace == 1
    assert count_after_delete == 0


def test_score_breakdown_includes_bm25_component() -> None:
    without_bm25 = score_breakdown(
        query_text="retry",
        vector_score=0.0,
        path="src/retry.py",
        content="retry",
        symbol_name=None,
        bm25_score=0.0,
    )
    with_bm25 = score_breakdown(
        query_text="retry",
        vector_score=0.0,
        path="src/retry.py",
        content="retry",
        symbol_name=None,
        bm25_score=1.0,
    )

    assert with_bm25["bm25"] == 1.0
    assert with_bm25["score"] > without_bm25["score"]
