# Getting started

## Install

```bash
pipx install git+https://github.com/Zhachory1/repo-index-mcp.git@v0.2.3
repo-index doctor
```

## Five-minute local smoke

```bash
tmp=$(mktemp -d)
cd "$tmp"
git init -b main
git config user.email test@example.com
git config user.name Test
git config commit.gpgsign false
printf 'def hello_world():\n    return True\n' > app.py
git add app.py && git commit -m init

repo-index --db "$tmp/index.sqlite" index .
repo-index --db "$tmp/index.sqlite" query hello_world -k 1
repo-index --db "$tmp/index.sqlite" get-symbol hello_world
```

Expected: query and symbol lookup return `app.py` with a snippet containing `hello_world`.

## Index your code

```bash
repo-index index-root ~/code
repo-index status
repo-index query "retry backoff" -k 5
```

## Connect MCP

Add the server to your MCP client config:

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

Use `which repo-index` to find the absolute command path. GUI clients often do not inherit shell `PATH`.

Restart the client after editing config. Confirm the MCP tools appear, then call `list_repos` or `search_code` from the client.
