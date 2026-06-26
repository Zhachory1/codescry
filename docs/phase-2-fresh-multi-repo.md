# Phase 2 fresh + multi-repo

Phase 2 adds repo-root indexing and committed-code freshness primitives.

## Commands

Index one repo incrementally:

```bash
repo-index index /path/to/repo
```

Discover and index every git repo under a root:

```bash
repo-index index-root ~/code
```

Install freshness hooks:

```bash
repo-index install-hooks /path/to/repo
repo-index install-hooks ~/code --recursive
```

Force overwrite existing hook files only when you have reviewed them:

```bash
repo-index install-hooks /path/to/repo --force
```

## Incremental indexing

For each indexed repo:

1. Capture current `HEAD`.
2. Enumerate committed blobs at that exact commit, skipping generated/vendor paths, gitlinks/submodules, binary files, and large blobs.
3. For first index or model/chunker changes, read all eligible committed blobs.
4. For normal reindex, diff prior successful commit against current commit and read changed committed blobs only.
5. Compare file content hash, embedding model, and chunker version against `indexed_files`.
6. Re-chunk and re-embed changed files only.
7. Delete chunks for files removed since prior successful commit.
8. Update repo commit metadata only after changed/removed files succeed.

`IndexResult` reports:

- `files_indexed`: committed text files represented by the index
- `files_changed`: files re-embedded this run
- `files_removed`: indexed files removed this run
- `chunks_indexed`: chunks written for changed files
- `chunks_total`: chunks currently stored for repo
- `duration_ms`: end-to-end indexing time
- `error_count`: indexing errors from this run
- `last_error`: summarized error, when any

## Freshness hooks

Installed hooks:

- `post-commit`
- `post-merge`

Hook action preserves the selected database path:

```bash
repo-index --db <db> reindex "$PWD" >/dev/null 2>&1 || true
```

The hook is best-effort and must not fail git commands. It may add local reindex latency. `status` still surfaces stale repos by comparing indexed commit with current `HEAD`, shows dirty tracked files separately, and reports repo-level error state.

## Local smoke result

Latest local smoke on this repo:

- Cold `index-root .`: 30 committed text files changed, 63 chunks indexed, 0 errors.
- Warm `index-root .`: 0 files changed, 0 chunks indexed, 63 chunks total, 0 errors.
- `status`: `is_stale: false`, `has_dirty_tracked_files: true` while local edits are uncommitted.
- Phase 0 eval smoke: Recall@10 20/32 = 0.625. This is not a Phase 2 gate.

## Phase 2 exit check

Local code now supports the path, but real exit still needs measurement on representative repos:

- Install hooks on target repos.
- Commit or merge a change.
- Confirm `repo-index status` flips back to `is_stale: false` within 60 seconds.
- Confirm dirty tracked files show as `has_dirty_tracked_files: true` without changing committed-code freshness.
- Record cold `index-root` duration and repo/file/chunk counts.
