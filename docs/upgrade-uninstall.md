# Upgrade and uninstall

## Upgrade from GitHub

```bash
pipx install --force git+https://github.com/Zhachory1/repo-index-mcp.git@v0.2.3
```

## Upgrade npm wrapper

```bash
npm install -g repo-index-mcp@latest
```

For one-shot usage, `npx repo-index-mcp@latest doctor` fetches the latest npm wrapper and runs the Python package through `uvx`.

## Uninstall

```bash
pipx uninstall repo-index-mcp
npm uninstall -g repo-index-mcp
```

## Remove local index

```bash
rm ~/.repo-index-mcp/index.sqlite
```

The index is derived data. Rebuild with:

```bash
repo-index index-root ~/code
```

## 0.x compatibility

During 0.x, local DB schema may change. If an upgrade behaves oddly, delete the local SQLite DB and rebuild.
