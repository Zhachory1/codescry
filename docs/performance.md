# Performance

CodeScry is local-first: indexing and query serving run on your machine with a local SQLite database. Performance depends on index size, embedding provider, vector coverage, and whether a query can use bounded candidate paths.

## Query serving path

Default ranking blends:

- vector similarity
- FTS/BM25
- lexical token overlap
- symbol match
- path match

For large indexes, CodeScry uses bounded candidate paths where possible instead of scoring every chunk. Candidate union combines lexical and vector candidates once vector coverage exists.

## Vector coverage

Build sqlite-vec coverage after indexing or after changing embedding provider:

```bash
codescry backfill-vectors
```

Candidate union can be disabled for comparison/debugging:

```bash
CODESCRY_DISABLE_CANDIDATE_UNION=1 codescry query "retry backoff" -k 10
```

Tune candidate-union threshold if testing large-index behavior:

```bash
CODESCRY_CANDIDATE_THRESHOLD=100000 codescry query "retry backoff" -k 10
```

## Tiny chunk filtering

Indexing skips empty chunks and chunks smaller than `CODESCRY_MIN_CHUNK_BYTES` when they do not contain the current symbol line. Default: `50` bytes. This reduces low-value one-line rows and vector work while preserving declaration-bearing symbol chunks.

Disable byte-size filtering for comparison or recovery:

```bash
CODESCRY_MIN_CHUNK_BYTES=0 codescry reindex /path/to/repo
```

## Embedding providers and latency

- `auto` is default: local Ollama `mxbai-embed-large` when available, otherwise hash.
- `hash` is fastest and dependency-free.
- `ollama` and `sentence-transformers` stay local but add indexing and query latency.
- `openai` is hosted, sends chunks/queries to the configured endpoint, and uses request batching.

Recommended quality-first local provider from current evals:

```bash
ollama pull mxbai-embed-large
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=mxbai-embed-large \
CODESCRY_EMBEDDING_MAX_CHARS=500 \
codescry index-root ~/code
```

## External provider knobs

Limit input size for external providers:

```bash
CODESCRY_EMBEDDING_MAX_CHARS=6000
```

Tune OpenAI and sentence-transformers indexing batch size:

```bash
CODESCRY_EMBEDDING_BATCH_SIZE=64
```

Lower batch size if a provider returns payload-size or memory errors.

## Query embedding cache

CodeScry caches query embeddings in the local SQLite DB by embedding model, provider fingerprint, and a per-DB HMAC of the exact query text. Raw query text is not stored.

Repeated real-embedding queries should show:

- first run: `query_embedding_cache_hit: false`
- repeat runs: `query_embedding_cache_hit: true`
- lower `embed_query_ms`

## Debug query performance

Use eval debug output to inspect serving timings:

```bash
codescry eval evals/golden.codescry.jsonl . -k 10 --debug > eval-debug.json
```

Useful fields:

- `embed_query_ms`
- `storage_ms`
- `repo_status_ms`
- `engine_total_ms`
- `fts_ms`
- `vector_ms`
- `row_score_ms`
- `diversify_ms`
- `candidate_path`
- `fts_candidates`
- `vector_candidates`
- `candidate_union_size`
- `rows_scored`
- `rows_returned`
- `query_embedding_cache_hit`

Quick inspection:

```bash
python3 - <<'PY'
import json
report = json.load(open('eval-debug.json'))
print(report['telemetry'])
PY
```

## Experimental RRF ranking

RRF modes are diagnostic only. Do not make them default unless they beat default ranking on self and public evals, including MRR and latency.

Plain RRF:

```bash
CODESCRY_RRF_RANKING=1 codescry eval evals/golden.codescry.jsonl . -k 10 --debug
```

qmd-style RRF:

```bash
CODESCRY_RRF_RANKING=1 CODESCRY_RRF_QMD_STYLE=1 codescry eval evals/golden.codescry.jsonl . -k 10 --debug
```

Other knobs:

- `CODESCRY_RRF_TOP_BONUS=1`
- `CODESCRY_RRF_EXACT_LISTS=1`

See `docs/ranking-experiment-findings.md` before changing ranking defaults.
