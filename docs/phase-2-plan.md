# Phase 2 plan: fresh + multi-repo

## Decision

Phase 2 ships committed-code freshness and multi-repo indexing. It does not ship working-tree freshness, watcher, poller, Postgres, hybrid retrieval, or quality tuning.

## Goals

- Discover git repos under a root directory.
- Index each repo independently.
- Reindex only changed tracked files.
- Remove chunks for deleted tracked files.
- Keep index fresh after commit and merge with git hooks.
- Make stale state visible in `status` and `list_repos`.
- Preserve Phase 0 eval harness, but do not gate Phase 2 on Recall@10.

## Non-goals

- Dirty working-tree freshness.
- File watcher.
- Periodic poller.
- Postgres backend.
- Shared service behavior.
- Tree-sitter chunking.
- Hybrid retrieval.
- MCP schema versioning ceremony beyond stable tool names.

## Freshness contract

Phase 2 indexes the committed git state at `HEAD`.

- `fresh` means indexed commit equals current `HEAD` and last index run completed successfully.
- Dirty working-tree edits are outside Phase 2 freshness.
- `status` should expose whether a repo has dirty tracked files so users know the index may not include uncommitted edits.
- Hook-triggered reindex is best-effort and must not fail git commands. It may add local reindex latency.
- Hooks are not the only freshness signal: `status` and query paths must detect when `HEAD` differs from indexed commit and report stale state.

## Repo identity

Use a local checkout identity for the local SQLite index:

- `repo_id`: canonical resolved repo root path.
- `repo_path`: same resolved root path for display/filter compatibility.
- `remote_url`: stored metadata when available, not primary key.

Upgrade requirement:

- If an existing row has the same `repo_path` but a different `repo_id`, delete the old rows across `repos`, `chunks`, and `indexed_files`, then rebuild from source.
- No complex migration is needed because the index is derived data.
- After cleanup, one repo path must map to one active repo row.

## Incremental indexing model

Track file-level state:

- `repo_id`
- `path`
- `content_hash`
- `chunker_version`
- `embedding_model`

Track repo-level state:

- `last_commit_sha`
- `last_indexed_at`
- `last_error`
- `error_count`

A file is changed when any of these differ from current expected state:

- content hash
- embedding model
- chunker version

Chunk rows need stable identity independent of content uniqueness:

- `chunk_id`: hash of repo, path, ordinal/start line, and content hash.
- `content_hash`: hash of chunk text.
- `start_line` / `end_line`
- `embedding_model`
- `chunker_version`

Repeated identical chunks in one file must not collide.

## Atomicity

Indexing must avoid marking stale data fresh.

Minimum Phase 2 rule:

1. Compute file hashes and chunks first.
2. Write file chunk replacement and file state in one SQLite transaction per file.
3. Delete removed file chunks and file state in one transaction.
4. Update repo `last_commit_sha` only after changed/removed files finish.
5. Update repo `last_commit_sha` only if every changed/removed file succeeds.
6. If any file fails, keep repo stale, record repo-level `last_error` / `error_count`, and show it in `status`.
7. Set SQLite `busy_timeout` so concurrent hooks do not fail immediately on a locked DB.

Repo-level all-or-nothing generation can wait unless per-file transaction plus success-only repo metadata proves insufficient.

## Hook behavior

Install hooks:

- `post-commit`
- `post-merge`

Hook command must preserve selected DB path:

```bash
repo-index --db <db> reindex "$PWD" >/dev/null 2>&1 || true
```

Hook installer must reject shell metacharacters in command name and shell-quote the DB path.

If the DB is locked by another indexing process, hook reindex should exit successfully while leaving stale state visible for the next manual or hook-triggered run.

## CLI surface

Commands:

```bash
repo-index index /path/to/repo
repo-index index-root ~/code
repo-index status
repo-index install-hooks /path/to/repo
repo-index install-hooks ~/code --recursive
repo-index reindex /path/to/repo
```

`index` and `index-root` output must include:

- files seen
- files changed
- files removed
- chunks indexed this run
- total chunks
- duration

## Tests

Required tests:

- discovers multiple repos under root.
- detects `.git` directories and gitfile worktrees.
- skips generated/vendor dirs.
- unchanged reindex embeds zero files.
- content change reindexes one file.
- model or chunker version change reindexes affected files.
- deleted file removes old chunks.
- legacy remote-url repo ID rows for the same path are deleted and rebuilt under path repo ID.
- custom `--db` is preserved in installed hook.
- hook command rejects shell metacharacters.
- legacy DB schema migration succeeds.
- dirty tracked file is visible in `status` but not considered Phase 2 freshness.
- partial indexing failure keeps repo stale and records repo-level error state.
- locked DB behavior does not fail hook-installed git commands.

## Validation

Before merge:

```bash
python3 -m pytest -q
python3 -m ruff check .
PYTHONPATH=src python3 -m repo_index_mcp --db .repo-index-mcp/phase2.sqlite index-root .
PYTHONPATH=src python3 -m repo_index_mcp --db .repo-index-mcp/phase2.sqlite index-root .
PYTHONPATH=src python3 -m repo_index_mcp --db .repo-index-mcp/phase2.sqlite status
```

Expected smoke result:

- first `index-root`: files changed > 0, chunks indexed > 0.
- second `index-root`: files changed = 0, chunks indexed = 0.
- `status`: `is_stale = false`, dirty state visible, error count visible.

Run Phase 0 eval as a smoke check only. Do not block Phase 2 on Recall@10.

## Exit gate

Phase 2 exits when:

- Multi-repo indexing works on a directory of repos.
- Hook reindex makes committed changes queryable within 60 seconds on representative repos.
- Incremental reindex skips unchanged files.
- Deleted files disappear from results.
- Status shows stale, dirty, and error states clearly.
