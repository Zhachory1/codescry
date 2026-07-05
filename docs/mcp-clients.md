# MCP client setup

## Generic MCP JSON

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

Global `--db` must come before `serve`. Use `which codescry` to find the absolute command path; GUI clients often do not inherit shell `PATH`.

Use the same embedding provider environment for indexing, CLI queries, and MCP serving. If you index with Ollama but serve without the Ollama env, the server will search for the default hash embedding model and results may be empty or weak.

## npm/npx wrapper

If `uv` is installed, the npm package can run the Python package through `uvx`:

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

The npm wrapper ships code only. The SQLite index remains local derived data at the configured `--db` path.

## Embedding provider env examples

Ollama:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "/Users/YOU/.local/bin/codescry",
      "args": ["--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {
        "CODESCRY_EMBEDDING_PROVIDER": "ollama",
        "CODESCRY_OLLAMA_MODEL": "mxbai-embed-large",
        "CODESCRY_EMBEDDING_MAX_CHARS": "500"
      }
    }
  }
}
```

OpenAI:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "/Users/YOU/.local/bin/codescry",
      "args": ["--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {
        "CODESCRY_EMBEDDING_PROVIDER": "openai",
        "CODESCRY_OPENAI_MODEL": "text-embedding-3-small",
        "OPENAI_API_KEY": "YOUR_KEY"
      }
    }
  }
}
```

Sentence-transformers:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "/Users/YOU/.local/bin/codescry",
      "args": ["--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {
        "CODESCRY_EMBEDDING_PROVIDER": "sentence-transformers",
        "CODESCRY_ST_MODEL": "BAAI/bge-small-en-v1.5"
      }
    }
  }
}
```

## Cave / mewrite / roktcode

Config files used locally:

- `~/.mewrite/mcp.json`
- `~/.roktcode/mcp.json`

Add the generic server block under `mcpServers`, then restart the client.

## Verify

CLI checks:

```bash
codescry doctor
codescry status
codescry query hello_world -k 1
npx codescry doctor
```

Client checks:

1. Restart the MCP client.
2. Confirm these tools appear: `search_code`, `get_symbol`, `list_repos`, `reindex`.
3. Call `list_repos` from the client.
4. Call `search_code` for a known indexed symbol such as `hello_world`.

Pilot “MCP configured” means a successful MCP tool call, not just editing JSON.

MCP tools exposed:

- `search_code`
- `get_symbol`
- `list_repos`
- `reindex`
