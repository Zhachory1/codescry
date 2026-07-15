<div align="center">
  <img src="https://raw.githubusercontent.com/Zhachory1/codescry/main/assets/codescry_logo.png" alt="CodeScry logo" width="180">
  <h1>CodeScry</h1>
  <p>
    <a href="https://github.com/Zhachory1/codescry/actions/workflows/ci.yml"><img src="https://github.com/Zhachory1/codescry/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
    <a href="https://pypi.org/project/codescry/"><img src="https://img.shields.io/pypi/v/codescry.svg" alt="PyPI"></a>
    <a href="https://www.npmjs.com/package/codescry"><img src="https://img.shields.io/npm/v/codescry.svg" alt="npm"></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="License: MIT"></a>
  </p>
</div>

CodeScry is a local codebase retrieval tool for coding agents. It indexes committed code from local git repos into a local SQLite database, then exposes ranked snippets through a CLI and MCP stdio server.

## Why CodeScry

- Local-first by default: auto-selects local Ollama `mxbai-embed-large` when available, otherwise falls back to hash embeddings and SQLite storage.
- Agent-ready: MCP tools for `search_code`, `get_symbol`, `list_repos`, and `reindex`.
- Large-index aware: bounded sqlite-vec candidate paths avoid scoring every chunk once vectors are backfilled.
- Semantic opt-in: Ollama, OpenAI, and sentence-transformers providers are available when quality matters more than default speed.
- Measured on real repos: public agent-natural evals and ranking/performance findings live in `docs/ranking-experiment-findings.md`.

Recent private `~/code` mxbai eval improved from ~20.7s average query latency to ~1.8s after filtered vector serving optimizations, with Recall@10 stable at 0.800. See `docs/performance.md` for knobs and diagnostics.

## Install

Fast path:

```bash
curl -LsSf https://raw.githubusercontent.com/Zhachory1/codescry/main/scripts/install.sh | sh
```

The installer uses `uv tool install codescry` when `uv` is available, otherwise `pipx install codescry`. If neither `uv` nor `pipx` is installed, it bootstraps `pipx` with `python3 -m pip --user`.

If you prefer explicit installs:

```bash
pipx install codescry
# or, if uv is already installed
uv tool install codescry
```

Node users can run the npm wrapper after installing [`uv`](https://docs.astral.sh/uv/getting-started/installation/):

```bash
npx codescry doctor
```

The npm package is a thin wrapper around the Python package. It does not bundle local SQLite index data.

For development:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

Check local readiness:

```bash
codescry doctor
```

## First success path

For a deterministic five-minute smoke test, see `docs/getting-started.md`.

Index this repo or another local git repo:

```bash
codescry index /path/to/git/repo
```

Query it:

```bash
codescry query "where is request retry handled" -k 5
```

Lookup a symbol:

```bash
codescry get-symbol RepoIndex --repo /path/to/git/repo
```

Discover and index every git repo under a root:

```bash
codescry index-root ~/code
```

Show indexed repos, stale/dirty state, and CodeScry hook coverage:

```bash
codescry status
```

## MCP setup

Run the MCP server over stdio:

```bash
codescry serve
```

The MCP server answers queries only. It is not a file watcher; keep committed-code freshness by running `codescry reindex` or installing git hooks.

Agent config example:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "/Users/YOU/.local/bin/codescry",
      "args": ["--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {}
    }
  }
}
```

npm/npx config example:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "codescry", "--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {}
    }
  }
}
```

Use `which codescry` to find the absolute command path for your machine when using direct CLI installs.

## Freshness hooks

Install hooks for one repo or a repo root:

```bash
codescry install-hooks /path/to/git/repo
codescry install-hooks ~/code --recursive
```

Hooks run best-effort after commit/merge:

```bash
codescry --db <db> reindex "$PWD"
```

They preserve the selected DB path and must not fail git commands.

If hooks are not practical, run an opt-in committed-state watcher:

```bash
codescry watch ~/code/my-repo
codescry watch --once --jsonl
```

The watcher polls git `HEAD` for indexed repos or a specified repo, then reindexes committed snapshots only when the commit changes.

## Docs

- `docs/getting-started.md` — install to first useful query.
- `docs/mcp-clients.md` — MCP config examples.
- `docs/troubleshooting.md` — common setup/query/freshness issues.
- `docs/cli-reference.md` — command reference.
- `docs/output-schema.md` — JSON fields.
- `docs/evals.md` — eval authoring and gate.
- `docs/performance.md` — query/index latency knobs, candidate union, batching, and debug telemetry.
- `docs/embedding-providers.md` — hash, Ollama, OpenAI, and sentence-transformers embedding providers.
- `docs/pilot.md` — 5-engineer pilot measurement plan and local reporting commands.
- `docs/language-support.md` — parser/regex/window support matrix.
- `docs/recipes.md` — common operations.
- `docs/upgrade-uninstall.md` — lifecycle commands.
- `docs/release.md` — PyPI-first and npm-wrapper release flow.
- `docs/ranking-experiment-findings.md` — retrieval/ranking experiments and eval findings.

## Evals

The seed golden set lives in `evals/golden.codescry.jsonl`.

Run the eval gate:

```bash
codescry eval evals/golden.codescry.jsonl . -k 10 --fail-under 0.85
```

## Pilot proof

Pilot task/activation/miss events are recorded in `~/.codescry/usage.jsonl` without snippets. Passive query logging is opt-in with `CODESCRY_ENABLE_USAGE_LOG=1`. Use:

```bash
codescry pilot report
```

See `docs/pilot.md` for activation, timing, miss capture, and decision gates.

## Retrieval behavior

- Default `auto` embeddings use local Ollama `mxbai-embed-large` when available, otherwise local deterministic hash vectors.
- Optional embedding providers include Ollama, OpenAI, and sentence-transformers. See `docs/embedding-providers.md`.
- Changing embedding provider or model requires reindexing because stored vectors are model-specific.
- Python functions/classes/methods get parser-backed symbol metadata.
- TS/JS/Go/Java/Rust/C/C++/SQL get Tree-sitter parser-backed symbol metadata.
- Other common declaration patterns get regex-backed symbol metadata.
- `get_symbol` uses stored symbol metadata before search fallback.
- Search blends vector, lexical, symbol, and path scores.
- Results include stale/dirty flags.

## Data boundary and safety

- Default `auto` provider does not use hosted APIs. It uses local Ollama if available, otherwise local hash embeddings.
- Default configuration does not send source code to hosted external APIs.
- OpenAI and non-local Ollama embedding endpoints send chunks and queries outside your machine. See `SECURITY.md` and `docs/embedding-providers.md`.
- Index data is local SQLite derived data and can be deleted/rebuilt.
- Files matching high-confidence secret patterns are skipped and prior chunks for those paths are removed.
- Secret skipping is a best-effort local guardrail, not a guarantee. See `SECURITY.md`.

## Current limits

- Python uses stdlib AST parser chunks; TS/JS/Go/Java/Rust/C/C++/SQL use Tree-sitter parser chunks; other languages use regex-backed symbol hints plus line windows.
- Default `auto` embeddings prefer local semantic Ollama when available and fall back to hash embeddings otherwise; hosted semantic embeddings are opt-in only.
- SQLite remains the default local store; large-index serving uses bounded sqlite-vec candidate paths where vector coverage exists.
- Freshness is committed-code freshness; dirty working-tree edits are reported but not indexed.
