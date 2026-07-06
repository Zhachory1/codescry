# Embedding providers

CodeScry supports pluggable embedding providers. The default provider is local deterministic hashing and does not send source code or queries anywhere.

Changing providers changes the `embedding_model` stored with indexed chunks. Reindex after changing provider:

```bash
codescry reindex /path/to/repo
# or
codescry index-root ~/code
```

## Provider summary

| Provider | Env value | Data leaves machine? | Extra setup |
| --- | --- | --- | --- |
| Hash | `hash` | No | None |
| Ollama | `ollama` | No, if Ollama is local | Running Ollama + pulled embedding model |
| OpenAI | `openai` | Yes | API key |
| Sentence Transformers | `sentence-transformers` | No | Optional Python dependency + model download |

## Hash provider

Default:

```bash
CODESCRY_EMBEDDING_PROVIDER=hash
```

Optional dimensions:

```bash
CODESCRY_HASH_DIMENSIONS=256
```

Model id format:

```text
hash-v1:dims=256
```

This is local, deterministic, cheap, and dependency-free. It is not a trained semantic embedding model.

## Ollama provider

Use a local Ollama embedding model. Current recommended quality-first local model:

```bash
ollama pull mxbai-embed-large
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=mxbai-embed-large \
CODESCRY_EMBEDDING_MAX_CHARS=500 \
codescry index-root ~/code
```

Smaller/faster model:

```bash
ollama pull nomic-embed-text
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=nomic-embed-text \
codescry index-root ~/code
```

Optional base URL:

```bash
CODESCRY_OLLAMA_URL=http://localhost:11434
```

Model id formats:

```text
ollama:mxbai-embed-large@http://localhost:11434
ollama:nomic-embed-text@http://localhost:11434
```

CodeScry calls Ollama's local `/api/embeddings` endpoint. Source code and queries stay on the machine if Ollama is local.

Public evals found `mxbai-embed-large` was the strongest local provider tested so far, but it needs `CODESCRY_EMBEDDING_MAX_CHARS=500` to avoid context-limit errors.

## OpenAI provider

Use hosted OpenAI embeddings:

```bash
CODESCRY_EMBEDDING_PROVIDER=openai \
OPENAI_API_KEY=... \
CODESCRY_OPENAI_MODEL=text-embedding-3-small \
codescry index-root ~/code
```

Optional base URL and org:

```bash
CODESCRY_OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_ORG_ID=...
```

Model id format:

```text
openai:text-embedding-3-small
```

Warning: this provider sends indexed source chunks and search queries to OpenAI. It is explicit opt-in and is not local-first.

OpenAI embedding requests are batched during indexing. Tune with `CODESCRY_EMBEDDING_BATCH_SIZE`; default is `64`.

## Sentence Transformers provider

Install optional dependency:

```bash
pipx install 'codescry[sentence-transformers]'
```

Use a local model:

```bash
CODESCRY_EMBEDDING_PROVIDER=sentence-transformers \
CODESCRY_ST_MODEL=BAAI/bge-small-en-v1.5 \
codescry index-root ~/code
```

Model id format:

```text
sentence-transformers:BAAI/bge-small-en-v1.5
```

The model runs locally after download. No source code is uploaded by CodeScry.

Sentence-transformers batches chunks during indexing. Tune with `CODESCRY_EMBEDDING_BATCH_SIZE`; default is `64`.

## Long chunks

External providers receive at most `CODESCRY_EMBEDDING_MAX_CHARS` characters per chunk. Default: `6000`.

```bash
CODESCRY_EMBEDDING_MAX_CHARS=6000
```

This avoids context-window failures for local and hosted embedding models. Hash embeddings do not use this limit.

## Batch size

OpenAI and sentence-transformers providers batch non-empty chunks during indexing. Default batch size: `64`.

```bash
CODESCRY_EMBEDDING_BATCH_SIZE=64
```

Lower this if a provider/API returns payload-size or memory errors. Ollama uses its batch embedding endpoint when available and falls back to one-at-a-time requests if the batch endpoint fails.

Ollama fallback concurrency defaults to `4` and can be tuned:

```bash
CODESCRY_OLLAMA_CONCURRENCY=4
```

## Evaluation

Compare providers against both self-repo and public agent-natural evals:

```bash
codescry eval evals/golden.codescry.jsonl . -k 10
scripts/eval-public-repos.sh
```

Example:

```bash
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=mxbai-embed-large \
CODESCRY_EMBEDDING_MAX_CHARS=500 \
scripts/eval-public-repos.sh
```

Do not promote a provider unless it improves public agent-natural evals without unacceptable latency or data-boundary tradeoffs.
