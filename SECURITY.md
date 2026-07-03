# Security

`codescry` is local-first. By default it does not send source code or embeddings to external services. Future non-default integrations should be reviewed separately before use.

## Data boundary

- Code is read from local git repositories.
- Chunks and embeddings are stored in a local SQLite database.
- MCP runs locally over stdio.
- No telemetry or hosted embedding API is used by default.
- OpenAI embeddings are available only by explicit opt-in and send indexed source chunks and search queries to OpenAI.

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
