# Output schema

## Search result

Returned by CLI `query`, CLI `get-symbol`, MCP `search_code`, and symbol fallback.

- `repo`: repo id/path.
- `path`: repo-relative path.
- `start_line`, `end_line`: 1-based line range.
- `snippet`: returned code text.
- `score`: blended retrieval score.
- `language`: detected language.
- `symbol_name`, `symbol_kind`, `symbol_line`, `symbol_confidence`: optional symbol metadata.
- `is_stale`: indexed commit differs from repo `HEAD` or repo has index errors.
- `has_dirty_tracked_files`: repo has uncommitted tracked changes; dirty content is not indexed.

## MCP `get_symbol` result

MCP `get_symbol` keeps the original MCP contract and uses `definition` instead of `snippet`:

- `repo`, `path`, `start_line`, `end_line`.
- `definition`: snippet text for the matched symbol.
- `score`.
- `symbol_name`, `symbol_kind`, `symbol_confidence`.
- `is_stale`, `has_dirty_tracked_files`.

CLI `repo-index get-symbol` returns the normal search-result shape with `snippet`.

## Index result

Returned by `index`, `index-root`, `reindex`.

- `repo_id`, `repo_path`, `commit_sha`.
- `files_indexed`: committed text files represented by the index.
- `files_changed`: files re-embedded this run.
- `files_removed`: files removed from index this run.
- `files_skipped`: secret-looking files skipped this run.
- `chunks_indexed`: chunks written this run.
- `chunks_total`: chunks stored for repo.
- `duration_ms`.
- `error_count`, `last_error`.
