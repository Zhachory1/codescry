import json

import pytest

from repo_index_mcp.embeddings import (
    HashEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    cosine_similarity,
    embedding_provider_from_env,
    tokenize_code,
    truncate_text,
)


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


def test_hash_embedding_batch_matches_single_embeddings() -> None:
    provider = HashEmbeddingProvider(dimensions=32)
    texts = ["retry request", "", "timeout"]

    assert provider.embed_batch(texts) == [provider.embed(text) for text in texts]


def test_embedding_provider_from_env_defaults_to_hash() -> None:
    provider = embedding_provider_from_env({})

    assert isinstance(provider, HashEmbeddingProvider)
    assert provider.model_id == "hash-v1:dims=256"


def test_embedding_provider_from_env_allows_hash_dimensions() -> None:
    provider = embedding_provider_from_env(
        {"CODESCRY_EMBEDDING_PROVIDER": "hash", "CODESCRY_HASH_DIMENSIONS": "32"}
    )

    assert provider.dimensions == 32
    assert provider.model_id == "hash-v1:dims=32"


def test_embedding_provider_from_env_creates_ollama_without_network_call() -> None:
    provider = embedding_provider_from_env(
        {
            "CODESCRY_EMBEDDING_PROVIDER": "ollama",
            "CODESCRY_OLLAMA_MODEL": "nomic-embed-text",
            "CODESCRY_OLLAMA_URL": "http://localhost:11434",
        }
    )

    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.model_id == "ollama:nomic-embed-text@http://localhost:11434"


def test_embedding_provider_from_env_creates_openai_without_network_call() -> None:
    provider = embedding_provider_from_env(
        {
            "CODESCRY_EMBEDDING_PROVIDER": "openai",
            "OPENAI_API_KEY": "test-key",
            "CODESCRY_OPENAI_MODEL": "text-embedding-3-small",
        }
    )

    assert isinstance(provider, OpenAIEmbeddingProvider)
    assert provider.model_id == "openai:text-embedding-3-small"


def test_embedding_provider_from_env_rejects_unknown_provider() -> None:
    with pytest.raises(ValueError, match="unknown CODESCRY_EMBEDDING_PROVIDER"):
        embedding_provider_from_env({"CODESCRY_EMBEDDING_PROVIDER": "claude"})


def test_openai_provider_requires_api_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        OpenAIEmbeddingProvider(api_key="")


def test_openai_embed_batch_maps_empty_and_non_empty_inputs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class Response:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": [
                        {"index": 0, "embedding": [1.0, 0.0]},
                        {"index": 1, "embedding": [0.0, 1.0]},
                    ]
                }
            ).encode()

    def fake_urlopen(_request, timeout):  # type: ignore[no-untyped-def]
        assert timeout == 60.0
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = OpenAIEmbeddingProvider(api_key="test-key")
    provider._dimensions = 2

    assert provider.embed_batch(["alpha", "", "beta"]) == [
        [1.0, 0.0],
        [0.0, 0.0],
        [0.0, 1.0],
    ]


def test_truncate_text_respects_non_positive_limit() -> None:
    assert truncate_text("abcdef", 3) == "abc"
    assert truncate_text("abcdef", 0) == "abcdef"


def test_http_providers_return_sized_zero_vector_for_empty_text() -> None:
    ollama = OllamaEmbeddingProvider()
    ollama._dimensions = 3
    openai = OpenAIEmbeddingProvider(api_key="test-key")
    openai._dimensions = 4

    assert ollama.embed("") == [0.0, 0.0, 0.0]
    assert openai.embed("   ") == [0.0, 0.0, 0.0, 0.0]
