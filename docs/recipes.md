# Recipes

## Index all repos

```bash
repo-index index-root ~/code
repo-index status
```

## Use a custom DB per client

```bash
repo-index --db ~/.repo-index-mcp/work.sqlite index-root ~/code/rokt
repo-index --db ~/.repo-index-mcp/work.sqlite serve
```

## Rebuild after secret exposure

```bash
rm ~/.repo-index-mcp/index.sqlite
repo-index index-root ~/code
```

## Fix stale results

```bash
repo-index status
repo-index reindex /path/to/repo
```

## Install freshness hooks

```bash
repo-index install-hooks ~/code --recursive
```

Existing hooks are not overwritten unless `--force` is used.
