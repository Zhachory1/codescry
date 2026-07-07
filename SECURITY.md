# Security

`codescry` is local-first. By default it does not use hosted embedding APIs. The default `auto` provider uses local Ollama `mxbai-embed-large` when available, otherwise it falls back to local hash embeddings. Optional embedding providers have different data boundaries: hash and local sentence-transformers stay local; Ollama stays local only when pointed at a local Ollama server; OpenAI sends indexed source chunks and search queries to OpenAI or the configured OpenAI-compatible endpoint.

## Data boundary

- Code is read from local git repositories.
- Chunks and embeddings are stored in a local SQLite database.
- MCP runs locally over stdio.
- No telemetry or hosted embedding API is used by default.
- The default `auto` provider uses local Ollama when available and local hash otherwise.
- Hash embeddings are local deterministic vectors.
- Sentence-transformers runs locally after model download; CodeScry does not upload source code for this provider.
- Ollama sends chunks and queries to the configured Ollama URL. This stays local only if `CODESCRY_OLLAMA_URL` points to a local server.
- OpenAI embeddings are explicit opt-in and send indexed source chunks and search queries to OpenAI or the configured OpenAI-compatible endpoint.

## Usage logs

Usage logs are local and opt-in for passive search events.

- Default path: `~/.codescry/usage.jsonl`.
- Passive query logging requires `CODESCRY_ENABLE_USAGE_LOG=1`.
- Snippets are not stored in usage logs.
- Raw query and miss text are not stored by default; CodeScry stores local salted text IDs and lengths.
- `CODESCRY_LOG_RAW_TEXT=1` stores raw query/miss text locally and should be used only when you explicitly want that data retained.
- Disable logging for a command with `CODESCRY_DISABLE_USAGE_LOG=1`.

## Secret handling

The indexer has a best-effort local guardrail for high-confidence secret patterns:

- PEM private key blocks.
- AWS access key IDs.
- GitHub token prefixes.
- Sensitive path patterns such as `.env` and key files.

When a tracked file matches these patterns, the file is skipped and any previously indexed chunks for that path are removed on the next index run.

This is not a guarantee. The tool is not a full secret scanner. Do not intentionally index repos that contain secrets.

## Purge and rebuild

If a secret may have been indexed:

1. Remove or rotate the secret at the source.
2. Delete the local index database, usually:

   ```bash
   rm ~/.codescry/index.sqlite
   ```

3. Re-index safe repos:

   ```bash
   codescry index-root ~/code
   ```

For custom DB paths, delete the DB passed via `--db`.

## Reporting vulnerabilities

Open a private security advisory or contact the maintainer directly. Do not file public issues containing secrets or exploit details.
