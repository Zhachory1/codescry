from repo_index_mcp.embeddings import HashEmbeddingProvider
from repo_index_mcp.models import Chunk
from repo_index_mcp.storage import SQLiteStorage, expand_query_text, token_overlap, wants_docs_query


def test_expand_query_text_adds_conservative_synonyms() -> None:
    expanded = expand_query_text("retry handler config")

    assert "backoff" in expanded
    assert "endpoint" in expanded
    assert "setting" in expanded


def test_expansion_does_not_dilute_exact_lexical_scoring() -> None:
    assert token_overlap("retry", "retry") == 1.0


def test_docs_intent_wins_over_code_terms() -> None:
    assert wants_docs_query("handler config docs") is True
    assert wants_docs_query("where is handler implemented") is False


def test_fts_candidates_find_expanded_synonym(tmp_path):  # type: ignore[no-untyped-def]
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="src/backoff.py",
        language="python",
        symbol_name="calculate_backoff",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def calculate_backoff(): pass",
    )
    provider = HashEmbeddingProvider()
    storage.replace_file_chunks(
        repo_id="repo",
        path="src/backoff.py",
        content_hash="hash",
        chunks=[chunk],
        embeddings=[provider.embed(chunk.content)],
        commit_sha="commit",
        embedding_model=provider.model_id,
        chunker_version="chunker",
    )

    results = storage.search(
        query_embedding=provider.embed("retry"),
        embedding_model=provider.model_id,
        k=1,
        query_text="retry",
        repo="repo",
    )

    assert results[0].path == "src/backoff.py"
    assert results[0].score > 0
