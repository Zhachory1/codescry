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
repo-index reindex /path/to/repo
repo-index install-hooks /path/to/repo
repo-index install-hooks ~/code --recursive
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --fail-under 0.85
repo-index serve
```

## Filters

- `--repo`: accepts `repo_id` or `repo_path` from `repo-index status`.
- `--path-prefix`: repo-relative path prefix.
- `--language`: detected language such as `python`, `typescript`, `go`, `markdown`.
