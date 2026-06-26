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

Query it:

```bash
repo-index query "where is request retry handled" -k 5
```

Show indexed repos:

```bash
repo-index status
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

## Phase 1 limits

- One-repo indexing flow.
- Naive line-window chunks.
- Local deterministic hash embeddings, not quality-tuned semantic embeddings.
- SQLite storage implemented with Python cosine search, no ANN/vector extension yet.
- `get_symbol` is best-effort search until tree-sitter symbol extraction lands.

## Data boundary

Default embedding is local and deterministic. Source code is not sent to external APIs.
