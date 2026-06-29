# Upgrade and uninstall

## Upgrade from GitHub

```bash
pipx install --force git+https://github.com/Zhachory1/repo-index-mcp.git@v0.1.1
```

## Uninstall

```bash
pipx uninstall repo-index-mcp
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
