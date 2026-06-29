# repo-index-mcp

Local codebase retrieval tool for coding agents. It indexes committed code from local git repos into a local SQLite database, then exposes ranked snippets through a CLI and MCP stdio server.

## Install

From GitHub with `pipx`:

```bash
pipx install git+https://github.com/Zhachory1/repo-index-mcp.git@v0.1.1
```

For development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Check local readiness:

```bash
repo-index doctor
```

## First success path

For a deterministic five-minute smoke test, see `docs/getting-started.md`.

Index this repo or another local git repo:

```bash
repo-index index /path/to/git/repo
```

Query it:

```bash
repo-index query "where is request retry handled" -k 5
```

Lookup a symbol:

```bash
repo-index get-symbol RepoIndex --repo /path/to/git/repo
```

Discover and index every git repo under a root:

```bash
repo-index index-root ~/code
```

Show indexed repos and freshness:

```bash
repo-index status
```

## MCP setup

Run the MCP server over stdio:

```bash
repo-index serve
```

Agent config example:

```json
{
  "mcpServers": {
    "repo-index": {
      "type": "stdio",
      "command": "/Users/YOU/.local/bin/repo-index",
      "args": ["--db", "/Users/YOU/.repo-index-mcp/index.sqlite", "serve"],
      "env": {}
    }
  }
}
```

Use `which repo-index` to find the absolute command path for your machine.

## Freshness hooks

Install hooks for one repo or a repo root:

```bash
repo-index install-hooks /path/to/git/repo
repo-index install-hooks ~/code --recursive
```

Hooks run best-effort after commit/merge:

```bash
repo-index --db <db> reindex "$PWD"
```

They preserve the selected DB path and must not fail git commands.

## Docs

- `docs/getting-started.md` — install to first useful query.
- `docs/mcp-clients.md` — MCP config examples.
- `docs/troubleshooting.md` — common setup/query/freshness issues.
- `docs/cli-reference.md` — command reference.
- `docs/output-schema.md` — JSON fields.
- `docs/evals.md` — eval authoring and gate.
- `docs/pilot.md` — 5-engineer pilot measurement plan.
- `docs/language-support.md` — parser/regex/window support matrix.
- `docs/recipes.md` — common operations.
- `docs/upgrade-uninstall.md` — lifecycle commands.

## Evals

Phase 0 eval docs live in `docs/phase-0-baseline.md`. The seed golden set lives in `evals/golden.repo-index-mcp.jsonl`.

Run the eval gate:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --fail-under 0.85
```

## Retrieval behavior

- Python functions/classes/methods get parser-backed symbol metadata.
- Common declaration patterns get regex-backed symbol metadata.
- `get_symbol` uses stored symbol metadata before search fallback.
- Search blends vector, lexical, symbol, and path scores.
- Results include stale/dirty flags.

## Data boundary and safety

- Default embeddings are local deterministic hash vectors.
- Default configuration does not send source code to external APIs.
- Index data is local SQLite derived data and can be deleted/rebuilt.
- Files matching high-confidence secret patterns are skipped and prior chunks for those paths are removed.
- Secret skipping is a best-effort local guardrail, not a guarantee. See `SECURITY.md`.

## Current limits

- Python gets parser-backed symbol chunks; other languages use regex-backed symbol hints plus line windows.
- Local deterministic hash embeddings, not quality-tuned semantic embeddings.
- SQLite storage scans/scoring in Python, no ANN/vector extension yet.
- Freshness is committed-code freshness; dirty working-tree edits are reported but not indexed.
