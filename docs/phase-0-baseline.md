# Phase 0 baseline

Phase 0 exists to stop fake progress. Build needs measurable retrieval quality and manual context-gathering baseline before Phase 2+ work claims impact.

## Golden eval set

Current seed set:

- `evals/golden.repo-index-mcp.jsonl`
- 32 query → expected code/doc location cases
- Each case has:
  - `id`
  - `query`
  - `expected_path`
  - optional `expected_text`
  - optional `expected_symbol`
  - optional `notes`

Run:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10
```

JSON output:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --json
```

Quality gate once corpus is representative:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --fail-under 0.85
```

## Seed baseline result

Current seed run on this repo:

- Result file: `evals/results/2026-06-26-seed-baseline.json`
- Recall@10: `24/32 = 0.750`
- Average query latency: `1.5ms`

This is below target. That is useful baseline signal, not failure of Phase 0. Phase 3 owns quality lift.

## Productivity baseline

Measure 5-10 real agent tasks without this tool. Record time and manual context-pastes. Then rerun same task class with MCP retrieval.

| Task ID | Repo | Task | Start | End | Minutes | Manual files pasted | Notes |
| --- | --- | --- | --- | --- | ---: | ---: | --- |
| pb-001 | TBD | Find where a request retry policy is implemented | TBD | TBD | TBD | TBD |  |
| pb-002 | TBD | Locate symbol definition and adjacent tests | TBD | TBD | TBD | TBD |  |
| pb-003 | TBD | Find config path for a runtime feature flag | TBD | TBD | TBD | TBD |  |
| pb-004 | TBD | Identify API endpoint and handler ownership | TBD | TBD | TBD | TBD |  |
| pb-005 | TBD | Trace write path from CLI/API to storage | TBD | TBD | TBD | TBD |  |

## Exit gate

Phase 0 exits only when:

- 30-50 golden cases cover real target repos.
- Recall@10 baseline is recorded.
- 5-10 productivity tasks have measured current-state time and manual paste count.
- Future work can compare against this baseline without changing the cases after seeing results.

Current status: harness, seed set, and seed Recall@10 baseline exist; representative human productivity baseline still needs real task measurements.
