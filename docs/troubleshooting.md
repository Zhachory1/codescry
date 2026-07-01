# Troubleshooting

## `repo-index doctor` fails

- `git not found`: install git and ensure it is on `PATH`.
- `db_writable` false: choose a writable DB path with `--db /path/to/index.sqlite`.
- `mcp_dependency` false: reinstall with `pipx install --force git+https://github.com/Zhachory1/repo-index-mcp.git@v0.2.3`.
- MCP server not appearing in GUI client: use an absolute `command` path from `which repo-index`, then restart the client.

## Query returns `[]`

Run:

```bash
repo-index status
```

If no repos are listed:

```bash
repo-index index /path/to/repo
# or
repo-index index-root ~/code
```

If filters are used, remove or check `--repo`, `--path-prefix`, and `--language`.

## Results are stale

```bash
repo-index status
repo-index reindex /path/to/repo
```

Freshness is committed-code freshness. Dirty tracked files are reported but not indexed.

## Hooks not firing

Reinstall hooks:

```bash
repo-index install-hooks /path/to/repo --force
```

Existing hooks are not overwritten unless `--force` is used.

## Secret-looking files skipped

Index output includes `files_skipped`. Skipped file chunks are not stored, and old chunks for the same path are removed on reindex.

If a secret may have been indexed, see `SECURITY.md` for purge/rebuild steps.
