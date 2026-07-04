from __future__ import annotations

import hashlib
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Protocol

TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|[0-9]+")
CAMEL_RE = re.compile(r"(?<!^)(?=[A-Z])")
DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
DEFAULT_SENTENCE_TRANSFORMERS_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_EXTERNAL_EMBEDDING_MAX_CHARS = 6000
DEFAULT_EMBEDDING_BATCH_SIZE = 64


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


class HashEmbeddingProvider:
    def __init__(self, dimensions: int = 256) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._dimensions = dimensions

    @property
    def model_id(self) -> str:
        return f"hash-v1:dims={self.dimensions}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for token in tokenize_code(text):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign

        return normalize_vector(vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OllamaEmbeddingProvider:
    def __init__(
        self,
        *,
        model: str = DEFAULT_OLLAMA_MODEL,
        base_url: str = DEFAULT_OLLAMA_URL,
        timeout_seconds: float = 60.0,
        max_chars: int = DEFAULT_EXTERNAL_EMBEDDING_MAX_CHARS,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars
        self._dimensions: int | None = None

    @property
    def model_id(self) -> str:
        return f"ollama:{self.model}@{self.base_url}"

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._dimensions = len(self.embed("codescry dimension probe"))
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        if not text.strip():
            return [0.0] * self.dimensions
        payload = json.dumps(
            {"model": self.model, "prompt": truncate_text(text, self.max_chars)}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"Ollama embedding request failed for model {self.model!r} at {self.base_url}. "
                "Ensure Ollama is running and the model is pulled."
            ) from exc
        embedding = data.get("embedding")
        if not isinstance(embedding, list):
            raise RuntimeError("Ollama embedding response did not include an embedding list")
        vector = [float(value) for value in embedding]
        if self._dimensions is None:
            self._dimensions = len(vector)
        return normalize_vector(vector)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]


class OpenAIEmbeddingProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_OPENAI_MODEL,
        base_url: str = DEFAULT_OPENAI_BASE_URL,
        organization: str | None = None,
        timeout_seconds: float = 60.0,
        max_chars: int = DEFAULT_EXTERNAL_EMBEDDING_MAX_CHARS,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for the openai embedding provider")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.organization = organization
        self.timeout_seconds = timeout_seconds
        self.max_chars = max_chars
        self.batch_size = batch_size
        self._dimensions: int | None = None

    @property
    def model_id(self) -> str:
        return f"openai:{self.model}"

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._dimensions = len(self.embed("codescry dimension probe"))
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        output: list[list[float] | None] = [None] * len(texts)
        empty_indexes: list[int] = []
        non_empty: list[tuple[int, str]] = []
        for index, text in enumerate(texts):
            if text.strip():
                non_empty.append((index, truncate_text(text, self.max_chars)))
            else:
                empty_indexes.append(index)
        if not non_empty:
            return [[0.0] * self.dimensions for _text in texts]

        for batch in batched(non_empty, self.batch_size):
            payload = json.dumps(
                {"model": self.model, "input": [text for _index, text in batch]}
            ).encode("utf-8")
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            }
            if self.organization:
                headers["OpenAI-Organization"] = self.organization
            request = urllib.request.Request(
                f"{self.base_url}/embeddings",
                data=payload,
                headers=headers,
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except (OSError, urllib.error.URLError, TimeoutError) as exc:
                raise RuntimeError(
                    f"OpenAI embedding request failed for model {self.model!r} at {self.base_url}"
                ) from exc
            try:
                embeddings = data["data"]
            except (KeyError, TypeError) as exc:
                raise RuntimeError("OpenAI embedding response did not include data") from exc
            if len(embeddings) != len(batch):
                raise RuntimeError("OpenAI embedding response count did not match input count")
            for fallback_index, item in enumerate(embeddings):
                source_index = item.get("index", fallback_index)
                output_index = batch[int(source_index)][0]
                vector = [float(value) for value in item["embedding"]]
                if self._dimensions is None:
                    self._dimensions = len(vector)
                output[output_index] = normalize_vector(vector)
        dimensions = self.dimensions
        for index in empty_indexes:
            output[index] = [0.0] * dimensions
        return [vector or [0.0] * dimensions for vector in output]


class SentenceTransformerEmbeddingProvider:
    def __init__(
        self,
        model: str = DEFAULT_SENTENCE_TRANSFORMERS_MODEL,
        max_chars: int = DEFAULT_EXTERNAL_EMBEDDING_MAX_CHARS,
        batch_size: int = DEFAULT_EMBEDDING_BATCH_SIZE,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "sentence-transformers provider requires optional dependency: "
                "pip install 'codescry[sentence-transformers]'"
            ) from exc
        self.model_name = model
        self.max_chars = max_chars
        self.batch_size = batch_size
        self._model = SentenceTransformer(model)
        dimensions = (
            self._model.get_embedding_dimension()
            if hasattr(self._model, "get_embedding_dimension")
            else self._model.get_sentence_embedding_dimension()
        )
        self._dimensions = int(dimensions) if dimensions is not None else len(self.embed("probe"))

    @property
    def model_id(self) -> str:
        return f"sentence-transformers:{self.model_name}"

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        output: list[list[float] | None] = [None] * len(texts)
        empty_indexes: list[int] = []
        non_empty: list[tuple[int, str]] = []
        for index, text in enumerate(texts):
            if text.strip():
                non_empty.append((index, truncate_text(text, self.max_chars)))
            else:
                empty_indexes.append(index)
        for batch in batched(non_empty, self.batch_size):
            embeddings = self._model.encode(
                [text for _index, text in batch],
                normalize_embeddings=True,
            )
            if hasattr(embeddings, "tolist"):
                values = embeddings.tolist()
            else:
                values = embeddings
            for (index, _text), vector in zip(batch, values, strict=True):
                output[index] = normalize_vector([float(value) for value in vector])
        dimensions = self.dimensions
        for index in empty_indexes:
            output[index] = [0.0] * dimensions
        return [vector or [0.0] * dimensions for vector in output]


def embedding_provider_from_env(
    environ: Mapping[str, str] | None = None,
) -> EmbeddingProvider:
    env = os.environ if environ is None else environ
    provider = env.get("CODESCRY_EMBEDDING_PROVIDER", "hash").strip().lower()
    if provider == "hash":
        dimensions = int(env.get("CODESCRY_HASH_DIMENSIONS", "256"))
        return HashEmbeddingProvider(dimensions=dimensions)
    if provider == "ollama":
        return OllamaEmbeddingProvider(
            model=env.get("CODESCRY_OLLAMA_MODEL", DEFAULT_OLLAMA_MODEL),
            base_url=env.get("CODESCRY_OLLAMA_URL", DEFAULT_OLLAMA_URL),
            max_chars=external_embedding_max_chars(env),
        )
    if provider == "openai":
        return OpenAIEmbeddingProvider(
            api_key=env.get("OPENAI_API_KEY", ""),
            model=env.get("CODESCRY_OPENAI_MODEL", DEFAULT_OPENAI_MODEL),
            base_url=env.get("CODESCRY_OPENAI_BASE_URL", DEFAULT_OPENAI_BASE_URL),
            organization=env.get("OPENAI_ORG_ID"),
            max_chars=external_embedding_max_chars(env),
            batch_size=external_embedding_batch_size(env),
        )
    if provider in {"sentence-transformers", "sentence_transformers", "st"}:
        return SentenceTransformerEmbeddingProvider(
            model=env.get("CODESCRY_ST_MODEL", DEFAULT_SENTENCE_TRANSFORMERS_MODEL),
            max_chars=external_embedding_max_chars(env),
            batch_size=external_embedding_batch_size(env),
        )
    raise ValueError(
        "unknown CODESCRY_EMBEDDING_PROVIDER "
        f"{provider!r}; expected hash, ollama, openai, or sentence-transformers"
    )


def external_embedding_max_chars(env: Mapping[str, str]) -> int:
    return int(env.get("CODESCRY_EMBEDDING_MAX_CHARS", str(DEFAULT_EXTERNAL_EMBEDDING_MAX_CHARS)))


def external_embedding_batch_size(env: Mapping[str, str]) -> int:
    return int(env.get("CODESCRY_EMBEDDING_BATCH_SIZE", str(DEFAULT_EMBEDDING_BATCH_SIZE)))


def batched(items: list[tuple[int, str]], size: int) -> list[list[tuple[int, str]]]:
    chunk_size = max(size, 1)
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars]


def embed_batch(provider: EmbeddingProvider, texts: list[str]) -> list[list[float]]:
    batch = getattr(provider, "embed_batch", None)
    if callable(batch):
        return batch(texts)
    return [provider.embed(text) for text in texts]


def normalize_vector(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def tokenize_code(text: str) -> list[str]:
    tokens: list[str] = []
    for match in TOKEN_RE.finditer(text):
        raw = match.group(0)
        parts = raw.replace("-", "_").split("_")
        for part in parts:
            if not part:
                continue
            for subpart in CAMEL_RE.split(part):
                normalized = subpart.lower()
                if normalized:
                    tokens.append(normalized)
    return tokens


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise ValueError("vectors must have same dimensions")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)
