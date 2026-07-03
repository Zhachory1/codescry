# Evals

Golden evals are JSONL rows:

```json
{"id":"case-001","query":"retry request","expected_path":"src/retry.py","expected_text":"def retry_request"}
```

Fields:

- `id`: stable case id.
- `query`: user/agent search query.
- `expected_path`: repo-relative file expected in top K.
- `expected_text`: optional text that must appear in the returned snippet.
- `expected_symbol`: optional future field for symbol cases.
- `notes`: optional context.
- `repo_url`: optional public repository URL for cross-repo evals.
- `repo_ref`: optional pinned tag or commit for cross-repo evals.
- `query_type`: optional bucket such as `agent_natural`, `symbol_exact`, or `docs_to_code`.

Run:

```bash
codescry eval evals/golden.codescry.jsonl . -k 10
codescry eval evals/golden.codescry.jsonl . -k 10 --json
codescry eval evals/golden.codescry.jsonl . -k 10 --debug > eval-debug.json
codescry eval evals/golden.codescry.jsonl . -k 10 --fail-under 0.85
```

Run public agent-natural evals against pinned public repos:

```bash
scripts/eval-public-repos.sh
```

The script clones repos under `~/.cache/codescry/eval-repos` and writes eval DBs under `~/.cache/codescry/eval-dbs`. Override with `CODESCRY_PUBLIC_EVAL_ROOT`, `CODESCRY_PUBLIC_EVAL_DB_DIR`, or `CODESCRY_PUBLIC_EVAL_K`.

See `docs/ranking-experiment-findings.md` for ranking experiment results and public eval baselines.

Add a scrubbed/synthetic case from a pilot miss. Do not commit proprietary snippets, secrets, customer data, or raw private queries to shared eval files.


```bash
codescry eval-add evals/golden.codescry.jsonl \
  --id pilot-001 \
  --query "retry backoff" \
  --expected-path src/retry.py \
  --expected-text "def retry"
```

Debug output includes per-case top results, score components (vector, lexical, BM25, symbol, path), docs/generated counts, duplicate path counts, and miss diagnostics.

Rules:

- Add scrubbed misses from real pilot tasks.
- Do not delete hard cases just to raise Recall@10.
- Keep query wording close to real user/agent language.
- Pin public eval repos with `repo_ref`; do not target moving default branches.
- Public `agent_natural` queries should avoid exact symbol names unless the bucket is `symbol_exact`.
- Prefer cases with stable `expected_text` so path-only hits do not mask wrong snippets.
