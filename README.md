# repo-index-mcp

Local codebase retrieval tool for coding agents. Phase 1 is a walking skeleton: index one git repo into SQLite, query chunks from the CLI, and expose retrieval over MCP stdio.

## Install

```bash
pipx install .
```

For development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Use

Index a repo:

```bash
repo-index index /path/to/git/repo
```

Discover and index every git repo under a root:

```bash
repo-index index-root ~/code
```

Install freshness hooks for one repo or a repo root:

```bash
repo-index install-hooks /path/to/git/repo
repo-index install-hooks ~/code --recursive
```

Query it:

```bash
repo-index query "where is request retry handled" -k 5
```

Show indexed repos:

```bash
repo-index status
```

Run the Phase 0 eval set:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10
```

Run the MCP server over stdio:

```bash
repo-index serve
```

Agent config example:

```json
{
  "mcpServers": {
    "repo-index": {
      "command": "repo-index",
      "args": ["serve"]
    }
  }
}
```

## Evals

Phase 0 eval docs live in `docs/phase-0-baseline.md`. The seed golden set lives in `evals/golden.repo-index-mcp.jsonl`.

## Phase 3 quality behavior

- Python functions/classes/methods get parser-backed symbol metadata.
- Common declaration patterns get regex-backed symbol metadata.
- `get_symbol` uses stored symbol metadata before search fallback.
- Search blends vector, lexical, symbol, and path scores.

## Phase 2 behavior

- `index-root` discovers git repos under a directory.
- Reindexing compares committed file content hashes and only re-embeds changed files.
- Deleted tracked files remove their old chunks from the index.
- `install-hooks` adds `post-commit` and `post-merge` hooks that run `repo-index --db <db> reindex "$PWD"`.
- `status` / `list_repos` report stale repos by comparing indexed commit to current `HEAD`.

## Current limits

- Python gets parser-backed symbol chunks; other languages use regex-backed symbol hints plus line windows.
- Local deterministic hash embeddings, not quality-tuned semantic embeddings.
- SQLite storage scans/scoring in Python, no ANN/vector extension yet.

## Data boundary

Default embedding is local and deterministic. Source code is not sent to external APIs.
