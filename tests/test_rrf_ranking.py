import pytest

from repo_index_mcp.embeddings import HashEmbeddingProvider
from repo_index_mcp.models import Chunk
from repo_index_mcp.storage import (
    RankedCandidate,
    SQLiteStorage,
    rrf_fuse,
)


def require_sqlite_vec() -> None:
    pytest.importorskip("sqlite_vec")


def make_chunk(content: str, path: str, repo_id: str = "repo") -> Chunk:
    return Chunk(
        repo_id=repo_id,
        repo_path=f"/{repo_id}",
        path=path,
        language="python",
        symbol_name=None,
        symbol_kind=None,
        symbol_line=None,
        symbol_confidence=None,
        start_line=1,
        end_line=1,
        content=content,
    )


def store_chunks(
    storage: SQLiteStorage,
    provider: HashEmbeddingProvider,
    chunks: list[Chunk],
) -> None:
    for chunk in chunks:
        storage.replace_file_chunks(
            repo_id=chunk.repo_id,
            path=chunk.path,
            content_hash=f"{chunk.repo_id}:{chunk.path}",
            chunks=[chunk],
            embeddings=[provider.embed(chunk.content)],
            commit_sha="commit",
            embedding_model=provider.model_id,
            chunker_version="chunker",
        )


def test_rrf_fuse_orders_by_rank_sum_and_tie_key() -> None:
    fts = [
        RankedCandidate(1, 0.9, "b.py", 1),
        RankedCandidate(2, 0.8, "a.py", 1),
    ]
    vector = [
        RankedCandidate(2, 0.9, "a.py", 1),
        RankedCandidate(3, 0.8, "c.py", 1),
        RankedCandidate(4, 0.7, "d.py", 1),
    ]

    ordered, traces = rrf_fuse(fts_candidates=fts, vector_candidates=vector)

    assert ordered[0] == 2
    assert traces[2]["source_ranks"] == {"fts": 2, "vector": 1}
    assert ordered[1:3] == [1, 3]


def test_rrf_ranking_emits_active_trace_with_repo_filter(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    require_sqlite_vec()
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    chunks = [
        make_chunk("def rate_limit(): return True", "src/rate_limit.py"),
        make_chunk("def unrelated(): return False", "src/unrelated.py"),
    ]
    store_chunks(storage, provider, chunks)
    storage.backfill_vectors()

    monkeypatch.setenv("CODESCRY_RRF_RANKING", "1")
    rows = storage.search_debug(
        query_embedding=provider.embed("rate_limit"),
        embedding_model=provider.model_id,
        k=5,
        query_text="rate_limit",
        repo="repo",
    )

    assert rows
    assert rows[0]["score"]["ranking_mode"] == "rrf_v1"  # type: ignore[index]
    assert rows[0]["score"]["source_ranks"]  # type: ignore[index]


def test_default_ranking_has_no_rrf_trace(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    require_sqlite_vec()
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    store_chunks(
        storage,
        provider,
        [make_chunk("def rate_limit(): return True", "src/rate_limit.py")],
    )
    storage.backfill_vectors()
    monkeypatch.delenv("CODESCRY_RRF_RANKING", raising=False)

    rows = storage.search_debug(
        query_embedding=provider.embed("rate_limit"),
        embedding_model=provider.model_id,
        k=5,
        query_text="rate_limit",
        repo="repo",
    )

    assert rows
    assert "ranking_mode" not in rows[0]["score"]  # type: ignore[operator]


def test_rrf_vector_absence_falls_back_with_reason(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    store_chunks(storage, provider, [make_chunk("def rate_limit(): return True", "src/rate.py")])

    monkeypatch.setenv("CODESCRY_RRF_RANKING", "1")
    rows = storage.search_debug(
        query_embedding=provider.embed("rate_limit"),
        embedding_model=provider.model_id,
        k=5,
        query_text="rate_limit",
        repo="repo",
    )

    assert rows
    assert rows[0]["score"]["ranking_mode"] == "current"  # type: ignore[index]
    assert rows[0]["score"]["rrf_disabled_reason"] == "vector_table_missing"  # type: ignore[index]


def test_rrf_source_builders_apply_repo_filter(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    require_sqlite_vec()
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    provider = HashEmbeddingProvider()
    chunks = [
        make_chunk("def shared_symbol(): return 'target'", "src/target.py", repo_id="target"),
        make_chunk("def shared_symbol(): return 'other'", "src/other.py", repo_id="other"),
    ]
    store_chunks(storage, provider, chunks)
    storage.backfill_vectors()

    monkeypatch.setenv("CODESCRY_RRF_RANKING", "1")
    rows = storage.search_debug(
        query_embedding=provider.embed("shared_symbol"),
        embedding_model=provider.model_id,
        k=5,
        query_text="shared_symbol",
        repo="target",
    )

    assert rows
    assert {row["result"].repo for row in rows} == {"target"}  # type: ignore[union-attr]
    assert rows[0]["score"]["ranking_mode"] == "rrf_v1"  # type: ignore[index]
