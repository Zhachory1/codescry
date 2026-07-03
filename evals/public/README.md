# Public agent-natural evals

These evals measure how CodeScry handles natural-language coding-agent queries on pinned public repositories.

## Repositories

| Dataset | Repo | Ref | Cases |
| --- | --- | --- | ---: |
| `requests.v2.32.3.agent-natural.jsonl` | `https://github.com/psf/requests` | `v2.32.3` | 25 |
| `flask.3.0.3.agent-natural.jsonl` | `https://github.com/pallets/flask` | `3.0.3` | 25 |
| `pytest.8.2.2.agent-natural.jsonl` | `https://github.com/pytest-dev/pytest` | `8.2.2` | 25 |

## Case rules

- Queries use behavior/intent wording a coding agent might ask.
- Cases avoid exact symbol names unless a future `symbol_exact` bucket says otherwise.
- Each row pins `repo_url`, `repo_ref`, and `query_type`.
- Each row includes `expected_text` to avoid path-only false positives.
- Expected paths are repo-relative at the pinned ref.

## Run

```bash
scripts/eval-public-repos.sh
```

Overrides:

```bash
CODESCRY_PUBLIC_EVAL_ROOT=/tmp/eval-repos \
CODESCRY_PUBLIC_EVAL_DB_DIR=/tmp/eval-dbs \
CODESCRY_PUBLIC_EVAL_K=10 \
scripts/eval-public-repos.sh
```

## Initial baseline

Run on 2026-07-02 with default local hash embeddings:

| Dataset | Recall@10 | Avg latency |
| --- | ---: | ---: |
| requests | 13/25 = 0.520 | 278.2ms |
| flask | 20/25 = 0.800 | 442.7ms |
| pytest | 12/25 = 0.480 | 2354.8ms |

These results are intentionally lower than the self-repo eval. Natural-language agent queries expose vocabulary mismatch that exact/symbol-heavy evals hide.
