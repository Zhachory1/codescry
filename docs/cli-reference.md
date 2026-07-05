# CLI reference

Global option:

```bash
codescry --db /path/to/index.sqlite <command>
```

`--db` is global and must come before the command.

## Commands

```bash
codescry doctor
codescry index /path/to/repo
codescry index-root ~/code
codescry query "retry backoff" -k 5
codescry query "retry backoff" --repo /path/to/repo --path-prefix src/ --language python -k 5
codescry get-symbol RepoIndex --repo /path/to/repo
codescry status
codescry backfill-vectors
# Candidate union is used automatically after vector coverage is complete.
# Disable it for comparison/debugging with:
CODESCRY_DISABLE_CANDIDATE_UNION=1 codescry query "retry backoff" -k 5
codescry reindex /path/to/repo
codescry install-hooks /path/to/repo
codescry install-hooks ~/code --recursive
codescry eval evals/golden.codescry.jsonl . -k 10 --fail-under 0.85
codescry eval evals/golden.codescry.jsonl . -k 10 --debug > eval-debug.json
codescry eval-add evals/golden.codescry.jsonl --id case-1 --query "retry" --expected-path src/retry.py
codescry pilot activate --engineer Ada --client mewrite --doctor-ok --repo-ready --tools-visible --list-repos-ok --search-code-ok --relevant-result
codescry pilot start-task --engineer Ada --task "find retry implementation"
codescry pilot end-task "$TASK_ID" --engineer Ada --baseline-source observed_paired_task --baseline-minutes 10 --mcp-queries 3 --useful yes --decision-grade
codescry pilot retain --engineer Ada --enabled yes --week2
codescry pilot miss --scrubbed-query "retry backoff" --expected-path src/retry.py
codescry pilot report
codescry pilot report --usage-log ada.usage.jsonl --usage-log grace.usage.jsonl
codescry serve
```

## Filters

- `--repo`: accepts `repo_id` or `repo_path` from `codescry status`.
- `--path-prefix`: repo-relative path prefix.
- `--language`: detected language such as `python`, `typescript`, `go`, `markdown`.

## Environment variables

Embedding providers:

- `CODESCRY_EMBEDDING_PROVIDER`: `hash` default, `ollama`, `openai`, or `sentence-transformers`.
- `CODESCRY_HASH_DIMENSIONS`: hash vector dimensions, default `256`.
- `CODESCRY_OLLAMA_MODEL`: Ollama model, default `nomic-embed-text`.
- `CODESCRY_OLLAMA_URL`: Ollama URL, default `http://localhost:11434`.
- `OPENAI_API_KEY`: required for OpenAI provider.
- `CODESCRY_OPENAI_MODEL`: OpenAI model, default `text-embedding-3-small`.
- `CODESCRY_OPENAI_BASE_URL`: OpenAI-compatible base URL.
- `OPENAI_ORG_ID`: optional OpenAI organization.
- `CODESCRY_ST_MODEL`: sentence-transformers model, default `BAAI/bge-small-en-v1.5`.
- `CODESCRY_EMBEDDING_MAX_CHARS`: max chars sent per chunk for external providers, default `6000`.
- `CODESCRY_EMBEDDING_BATCH_SIZE`: OpenAI/sentence-transformers batch size, default `64`.

Ranking/debug:

- `CODESCRY_DISABLE_CANDIDATE_UNION=1`: disable candidate union for comparison/debugging.
- `CODESCRY_CANDIDATE_THRESHOLD`: candidate-union threshold override.
- `CODESCRY_RRF_RANKING=1`: enable experimental RRF ranking.
- `CODESCRY_RRF_TOP_BONUS=1`: add top-rank bonus to RRF.
- `CODESCRY_RRF_EXACT_LISTS=1`: add exact path/symbol/lexical RRF lists.
- `CODESCRY_RRF_QMD_STYLE=1`: enable top bonus plus exact lists.

Usage logging:

- `CODESCRY_ENABLE_USAGE_LOG=1`: enable passive query logging.
- `CODESCRY_DISABLE_USAGE_LOG=1`: disable usage logging for a command.
- `CODESCRY_USAGE_LOG`: custom usage log path.
- `CODESCRY_LOG_RAW_TEXT=1`: include raw query/miss text in the local log.
