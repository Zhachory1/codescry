# Troubleshooting

## `codescry doctor` fails

- `git not found`: install git and ensure it is on `PATH`.
- `db_writable` false: choose a writable DB path with `--db /path/to/index.sqlite`.
- `mcp_dependency` false: reinstall with `curl -LsSf https://raw.githubusercontent.com/Zhachory1/codescry/main/scripts/install.sh | sh`, or `pipx install --force codescry`.
- MCP server not appearing in GUI client: use an absolute `command` path from `which codescry`, then restart the client.

## Query returns `[]`

Run:

```bash
codescry status
```

If no repos are listed:

```bash
codescry index /path/to/repo
# or
codescry index-root ~/code
```

If filters are used, remove or check `--repo`, `--path-prefix`, and `--language`.

If you changed embedding provider, reindex with the same provider environment used for query/serve. The default `auto` provider can switch from hash to Ollama after you install `mxbai-embed-large`; reindex after that switch. For MCP clients, ensure the server config includes the same `CODESCRY_EMBEDDING_PROVIDER` and model env vars used during indexing.

## `index-root` takes too long

Large roots can take a long time, especially with external embedding providers. Progress is persisted per repo, so rerun the same command to continue through already-current repos quickly.

Use progress or bounded sessions:

```bash
codescry index-root ~/code --progress
codescry index-root ~/code --jsonl --max-duration 1800
codescry index-root ~/code --jsonl --limit 5
```

`--jsonl` streams one result per line plus a summary line, so caller timeouts do not lose all progress output.

Linked git worktrees are skipped by default because they usually duplicate another checkout. Use `--include-worktrees` only if you intentionally want each worktree indexed as its own repo.

## Results are stale

```bash
codescry status
codescry reindex /path/to/repo
```

Freshness is committed-code freshness. Dirty tracked files are reported but not indexed.

## Hooks not firing

Reinstall hooks:

```bash
codescry install-hooks /path/to/repo --force
```

Existing hooks are not overwritten unless `--force` is used.

## Secret-looking files skipped

Index output includes `files_skipped`. Skipped file chunks are not stored, and old chunks for the same path are removed on reindex.

If a secret may have been indexed, see `SECURITY.md` for purge/rebuild steps.
