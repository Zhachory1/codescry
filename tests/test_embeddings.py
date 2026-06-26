import pytest

from repo_index_mcp.embeddings import HashEmbeddingProvider, cosine_similarity, tokenize_code


def test_tokenize_code_splits_snake_and_camel_case() -> None:
    assert tokenize_code("def retryRequest(max_attempts):") == [
        "def",
        "retry",
        "request",
        "max",
        "attempts",
    ]


def test_hash_embedding_is_deterministic_and_normalized() -> None:
    provider = HashEmbeddingProvider(dimensions=32)

    left = provider.embed("retry request timeout")
    right = provider.embed("retry request timeout")

    assert left == right
    assert cosine_similarity(left, right) == pytest.approx(1.0)
