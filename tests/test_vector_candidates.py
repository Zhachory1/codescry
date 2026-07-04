import pytest

from repo_index_mcp.embeddings import HashEmbeddingProvider
from repo_index_mcp.models import Chunk
from repo_index_mcp.storage import (
    SQLiteStorage,
    should_try_candidate_union,
    should_use_candidate_union,
    vector_coverage_count,
    vector_scores,
)

pytest.importorskip("sqlite_vec")


def make_chunk(content: str, path: str = "src/rate_limit.py") -> Chunk:
    return Chunk(
        repo_id="repo",
        repo_path="/repo",
        path=path,
        language="python",
        symbol_name="rate_limit",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content=content,
    )


def test_vector_scores_return_candidate(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    chunk = make_chunk("def rate_limit(): pass")
    storage.replace_file_chunks(
        repo_id="repo",
        path=chunk.path,
        content_hash="hash",
        chunks=[chunk],
        embeddings=[provider.embed(chunk.content)],
        commit_sha="commit",
        embedding_model=provider.model_id,
        chunker_version="chunker",
    )
    storage.backfill_vectors()

    with storage._connect() as conn:  # noqa: SLF001
        scores = vector_scores(
            conn,
            query_embedding=provider.embed("rate_limit"),
            embedding_model=provider.model_id,
            repo="repo",
        )

        coverage = vector_coverage_count(conn, embedding_model=provider.model_id)

    assert scores
    assert coverage == 1


def test_backfill_vectors_indexes_existing_chunks(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    chunk = make_chunk("def rate_limit(): pass")
    storage.replace_file_chunks(
        repo_id="repo",
        path=chunk.path,
        content_hash="hash",
        chunks=[chunk],
        embeddings=[provider.embed(chunk.content)],
        commit_sha="commit",
        embedding_model=provider.model_id,
        chunker_version="chunker",
    )

    assert storage.backfill_vectors() == 1


def test_default_candidate_union_preserves_vector_hit_with_fts_distractors(
    tmp_path,
    monkeypatch,
):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    target = make_chunk("def rate_limit(): pass", path="src/rate_limit.py")
    distractors = [
        make_chunk(f"def throttling_distractor_{index}(): pass", path=f"src/distractor_{index}.py")
        for index in range(80)
    ]
    chunks = [target, *distractors]
    for chunk in chunks:
        storage.replace_file_chunks(
            repo_id="repo",
            path=chunk.path,
            content_hash=chunk.path,
            chunks=[chunk],
            embeddings=[provider.embed(chunk.content)],
            commit_sha="commit",
            embedding_model=provider.model_id,
            chunker_version="chunker",
        )
    storage.backfill_vectors()
    monkeypatch.setenv("CODESCRY_CANDIDATE_THRESHOLD", "1")
    with storage._connect() as conn:  # noqa: SLF001
        assert should_try_candidate_union(
            conn,
            query_text="rate_limit throttling",
            embedding_model=provider.model_id,
            repo=None,
            path_prefix=None,
            language=None,
            k=5,
        ) is True

    union_results = storage.search(
        query_embedding=provider.embed("rate_limit throttling"),
        embedding_model=provider.model_id,
        k=5,
        query_text="rate_limit throttling",
    )
    monkeypatch.setenv("CODESCRY_DISABLE_CANDIDATE_UNION", "1")
    with storage._connect() as conn:  # noqa: SLF001
        assert should_try_candidate_union(
            conn,
            query_text="rate_limit throttling",
            embedding_model=provider.model_id,
            repo=None,
            path_prefix=None,
            language=None,
            k=5,
        ) is False
    full_scan_results = storage.search(
        query_embedding=provider.embed("rate_limit throttling"),
        embedding_model=provider.model_id,
        k=5,
        query_text="rate_limit throttling",
    )

    assert union_results[0].path == full_scan_results[0].path == "src/rate_limit.py"


def test_candidate_union_preflight_allows_repo_filter(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    target = make_chunk("def rate_limit(): pass", path="src/rate_limit.py")
    other = Chunk(
        repo_id="other",
        repo_path="/other",
        path="src/other.py",
        language="python",
        symbol_name="other",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def other(): pass",
    )
    for chunk in [target, other]:
        storage.replace_file_chunks(
            repo_id=chunk.repo_id,
            path=chunk.path,
            content_hash=chunk.path,
            chunks=[chunk],
            embeddings=[provider.embed(chunk.content)],
            commit_sha="commit",
            embedding_model=provider.model_id,
            chunker_version="chunker",
        )
    storage.backfill_vectors()
    monkeypatch.setenv("CODESCRY_CANDIDATE_THRESHOLD", "1")

    with storage._connect() as conn:  # noqa: SLF001
        assert should_try_candidate_union(
            conn,
            query_text="rate_limit",
            embedding_model=provider.model_id,
            repo="repo",
            path_prefix=None,
            language=None,
            k=5,
        ) is True
        scores = vector_scores(
            conn,
            query_embedding=provider.embed("rate_limit"),
            embedding_model=provider.model_id,
            repo="repo",
        )

    assert scores


def test_candidate_union_requires_vector_candidates(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    with storage._connect() as conn:  # noqa: SLF001
        assert should_use_candidate_union(
            vector_scores={},
            bm25_scores={index: 1.0 for index in range(100)},
            k=5,
        ) is False
        assert should_try_candidate_union(
            conn,
            query_text="where is throttling implemented",
            embedding_model="model",
            repo=None,
            path_prefix=None,
            language=None,
            k=5,
        ) is False
