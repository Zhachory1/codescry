# Recipes

## Index all repos

```bash
codescry index-root ~/code
codescry status
```

`index-root` skips linked git worktrees by default to avoid duplicate branch indexes. Index one worktree directly with `codescry index /path/to/worktree`, or include discovered worktrees with `--include-worktrees`.

For large roots, stream progress and resume by rerunning the same command:

```bash
codescry index-root ~/code --progress
```

For bounded indexing sessions:

```bash
codescry index-root ~/code --jsonl --max-duration 1800
codescry index-root ~/code --jsonl --limit 5
codescry index-root ~/code --include-worktrees
```

## Ignore repo-local noise

Add `.codescryignore` at the repo root to skip specs, generated clients, or bulky fixtures for that repo only:

```gitignore
# Tests/specs
**/*.spec.ts
**/*.spec.tsx
**/*.test.ts
**/*.test.tsx

# Generated API clients / fixtures
libs/api-interfaces/src/api/**
libs/api-interfaces/src/types/**
libs/e2e-shared/src/fixtures/auto-generated/**
libs/op2-campaigns/api/*.json
```

Commit the file, then reindex:

```bash
codescry reindex /path/to/repo
```

Index output reports `files_ignored`; old chunks for newly ignored paths are removed.

## Use a custom DB per client

```bash
codescry --db ~/.codescry/work.sqlite index-root ~/code/rokt
codescry --db ~/.codescry/work.sqlite serve
```

## Rebuild after secret exposure

```bash
rm ~/.codescry/index.sqlite
codescry index-root ~/code
```

## Remove deleted repos from the index

If you delete a repo from disk, prune its derived index rows:

```bash
codescry prune
```

Remove one indexed repo explicitly:

```bash
codescry remove-repo /path/to/repo
```

These commands only delete CodeScry index rows, never source files.

## Fix stale results

```bash
codescry status
codescry reindex /path/to/repo
```

## Install freshness hooks

```bash
codescry install-hooks ~/code --recursive
```

Existing hooks are not overwritten unless `--force` is used.

## Switch embedding provider

Changing provider or model changes stored `embedding_model`; reindex after changing it.

```bash
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=mxbai-embed-large \
CODESCRY_EMBEDDING_MAX_CHARS=500 \
codescry reindex /path/to/repo
```

## Tune external embedding batching

```bash
CODESCRY_EMBEDDING_PROVIDER=openai \
CODESCRY_EMBEDDING_BATCH_SIZE=32 \
OPENAI_API_KEY=... \
codescry index-root ~/code
```

## Run public evals

```bash
scripts/eval-public-repos.sh
```

Use isolated cache locations:

```bash
CODESCRY_PUBLIC_EVAL_ROOT=/tmp/codescry-eval-repos \
CODESCRY_PUBLIC_EVAL_DB_DIR=/tmp/codescry-eval-dbs \
scripts/eval-public-repos.sh
```

## Compare experimental RRF ranking

```bash
CODESCRY_RRF_RANKING=1 codescry eval evals/golden.codescry.jsonl . -k 10 --debug
CODESCRY_RRF_RANKING=1 CODESCRY_RRF_QMD_STYLE=1 codescry eval evals/golden.codescry.jsonl . -k 10 --debug
```

Keep RRF flagged unless it beats default ranking on self and public evals, including MRR and latency.
