# CLI reference

Global option:

```bash
repo-index --db /path/to/index.sqlite <command>
```

`--db` is global and must come before the command.

## Commands

```bash
repo-index doctor
repo-index index /path/to/repo
repo-index index-root ~/code
repo-index query "retry backoff" -k 5
repo-index query "retry backoff" --repo /path/to/repo --path-prefix src/ --language python -k 5
repo-index get-symbol RepoIndex --repo /path/to/repo
repo-index status
repo-index backfill-vectors
# Candidate union is used automatically after vector coverage is complete.
# Disable it for comparison/debugging with:
REPO_INDEX_DISABLE_CANDIDATE_UNION=1 repo-index query "retry backoff" -k 5
repo-index reindex /path/to/repo
repo-index install-hooks /path/to/repo
repo-index install-hooks ~/code --recursive
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --fail-under 0.85
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --debug > eval-debug.json
repo-index eval-add evals/golden.repo-index-mcp.jsonl --id case-1 --query "retry" --expected-path src/retry.py
repo-index pilot activate --engineer Ada --client mewrite --doctor-ok --repo-indexed --tools-visible --list-repos-ok --search-code-ok --relevant-result
repo-index pilot start-task --engineer Ada --task "find retry implementation"
repo-index pilot end-task "$TASK_ID" --engineer Ada --baseline-source observed_paired_task --baseline-minutes 10 --mcp-queries 3 --useful yes --decision-grade
repo-index pilot retain --engineer Ada --enabled yes --week2
repo-index pilot miss --scrubbed-query "retry backoff" --expected-path src/retry.py
repo-index pilot report
repo-index pilot report --usage-log ada.usage.jsonl --usage-log grace.usage.jsonl
repo-index serve
```

## Filters

- `--repo`: accepts `repo_id` or `repo_path` from `repo-index status`.
- `--path-prefix`: repo-relative path prefix.
- `--language`: detected language such as `python`, `typescript`, `go`, `markdown`.
