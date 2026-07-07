import json
import urllib.error
from io import BytesIO

import pytest

from repo_index_mcp.embeddings import (
    HashEmbeddingProvider,
    OllamaEmbeddingProvider,
    OpenAIEmbeddingProvider,
    cosine_similarity,
    embedding_provider_from_env,
    ollama_model_names,
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


def test_embedding_provider_from_env_auto_falls_back_to_hash(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail_urlopen(_request, timeout):  # type: ignore[no-untyped-def]
        raise OSError("offline")

    monkeypatch.setattr("urllib.request.urlopen", fail_urlopen)

    provider = embedding_provider_from_env({})

    assert isinstance(provider, HashEmbeddingProvider)
    assert provider.model_id == "hash-v1:dims=256"


def test_embedding_provider_from_env_auto_prefers_mxbai(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        "repo_index_mcp.embeddings.ollama_model_names",
        lambda _base_url: {"nomic-embed-text", "mxbai-embed-large"},
    )

    provider = embedding_provider_from_env({})

    assert isinstance(provider, OllamaEmbeddingProvider)
    assert provider.model == "mxbai-embed-large"
    assert provider.max_chars == 500


def test_ollama_model_names_strips_tags(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    response = OpenAIResponse({"models": [{"name": "mxbai-embed-large:latest"}]})
    monkeypatch.setattr("urllib.request.urlopen", lambda _request, timeout: response)

    assert ollama_model_names("http://localhost:11434") == {"mxbai-embed-large"}


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
    assert provider.concurrency == 4


def test_ollama_embed_batch_preserves_order(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = OllamaEmbeddingProvider(concurrency=4)

    def fake_embed_many(batch):  # type: ignore[no-untyped-def]
        return [[float(len(text))] for _index, text in batch]

    monkeypatch.setattr(provider, "_embed_many", fake_embed_many)
    provider._dimensions = 1

    assert provider.embed_batch(["a", "abcd", "ab"]) == [[1.0], [4.0], [2.0]]


def test_ollama_batch_falls_back_to_legacy_when_batch_endpoint_fails(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    provider = OllamaEmbeddingProvider(concurrency=1)

    def fail_embed_many(_batch):  # type: ignore[no-untyped-def]
        raise RuntimeError("batch unsupported")

    def fake_legacy(text: str) -> list[float]:
        return [float(len(text))]

    monkeypatch.setattr(provider, "_embed_many", fail_embed_many)
    monkeypatch.setattr(provider, "_embed_one_legacy", fake_legacy)
    provider._dimensions = 1

    assert provider.embed_batch(["a", "abcd"]) == [[1.0], [4.0]]


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


class OpenAIResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):  # type: ignore[no-untyped-def]
        return self

    def __exit__(self, *_args):  # type: ignore[no-untyped-def]
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


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


def test_openai_embed_batch_retries_retryable_http_error(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    calls = {"count": 0}

    def fake_urlopen(_request, timeout):  # type: ignore[no-untyped-def]
        calls["count"] += 1
        if calls["count"] == 1:
            raise urllib.error.HTTPError(
                url="https://api.openai.com/v1/embeddings",
                code=429,
                msg="rate limited",
                hdrs={},
                fp=BytesIO(b"rate limited"),
            )
        return OpenAIResponse({"data": [{"index": 0, "embedding": [1.0, 0.0]}]})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    provider = OpenAIEmbeddingProvider(
        api_key="test-key",
        max_retries=1,
        retry_base_seconds=0,
    )

    assert provider.embed_batch(["alpha"]) == [[1.0, 0.0]]
    assert calls["count"] == 2


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
