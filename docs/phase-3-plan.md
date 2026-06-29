# Phase 3 plan: quality

## Decision

Phase 3 improves retrieval quality in two ordered slices. Slice 1: symbol metadata and `get_symbol`. Slice 2: deterministic hybrid ranking. Keep local tool small. No shared-service work.

## Goal

Improve Recall@10 and make `get_symbol` real enough to use.

Target remains DD target:

- Recall@10 >= 0.85 on golden set.

But do not fake win. If score stays lower, record baseline and misses.

## Non-goals

- Hosted embeddings.
- Cross-encoder rerank.
- Postgres.
- Tree-sitter dependency if lighter parser gets signal first.
- New MCP tools.
- Dirty working-tree indexing.

## Scope

### 1. Symbol extraction

Add symbol metadata during chunking.

Minimum:

- Python via stdlib `ast`.
- Regex fallback for common declarations:
  - `function name(`
  - `def name(`
  - `class Name`
  - `func name(`
  - `type Name`
  - `interface Name`

Store:

- `symbol_name`
- `symbol_kind`
- `symbol_line`

If no symbol found, chunk still indexed.

Symbol confidence:

- `parser`: stdlib/parser-confirmed symbol.
- `regex`: declaration-looking fallback symbol.

Regex symbols must ignore comments and obvious string literals before matching. Exact `get_symbol` priority applies to parser symbols first, then regex symbols.

### 2. Symbol-aware chunks

For Python:

- Chunk top-level functions/classes as units when possible.
- Keep line-window fallback for large or unsupported files.
- Preserve `start_line` / `end_line`.

For other languages:

- Keep line windows.
- Attach nearest preceding regex symbol.

### 3. Hybrid retrieval

Blend scores:

- vector score from existing hash embedding.
- lexical score from token overlap between query and chunk content/path/symbol.
- symbol boost when query token matches symbol name.
- path boost when query token matches path parts.

Start simple and deterministic. No learned weights.

Initial formula:

```text
score = (0.60 * normalized_vector_score)
      + (0.24 * lexical_overlap_score)
      + (0.09 * symbol_match_score)
      + (0.07 * path_match_score)
```

Tie-breaks:

1. higher score
2. parser symbol before regex symbol
3. shorter path
4. earlier start line

Ranking tests must pin order for lexical, symbol, and path boosts.

Return score remains one number. Add no MCP schema break.

### 4. `get_symbol`

Use stored symbol metadata first:

- exact parser symbol match beats exact regex symbol match.
- exact match beats partial match within same confidence.
- repo filter honored.
- existing fields stay: `repo`, `path`, `start_line`, `end_line`, `definition`, `score`.
- additive fields allowed: `symbol_name`, `symbol_kind`, `symbol_confidence`, `is_stale`, `has_dirty_tracked_files`.

Fallback to search only when symbol metadata has no hit.

### 5. Eval

Run existing golden set.

Add focused symbol cases only if needed:

- Python function lookup.
- Python class lookup.
- CLI command handler lookup.
- storage method lookup.

Do not remove hard cases just to raise score.

## Tests

Required:

- schema migration adds symbol fields without breaking old DBs.
- Python AST function chunk has `symbol_name` and parser confidence.
- Python class chunk has `symbol_name` and parser confidence.
- regex symbols in comments/strings are ignored.
- fallback line chunk still works for unsupported text.
- lexical score lifts exact identifier match.
- path score lifts path match.
- `get_symbol` returns exact symbol before semantic fallback.
- stale/dirty flags still appear in search and get_symbol.
- eval runner still works.

## Validation

Run:

```bash
python3 -m pytest -q
python3 -m ruff check .
PYTHONPATH=src python3 -m repo_index_mcp --db .repo-index-mcp/phase3.sqlite index-root .
PYTHONPATH=src python3 -m repo_index_mcp --db .repo-index-mcp/phase3.sqlite eval evals/golden.repo-index-mcp.jsonl . -k 10
```

Record:

- Recall@10 before.
- Recall@10 after.
- misses.
- latency smoke.

## Local result

Latest local smoke on this repo:

- Cold `index-root .`: Phase 3 symbol-aware chunks indexed successfully with 0 errors.
- Recall@10: 29/32 = 0.906.
- Average eval query latency: ~65ms.
- Remaining misses: `rim-003`, `rim-027`, `rim-030`.

## Exit gate

Phase 3 exits only if:

- `get_symbol` works from symbol metadata.
- hybrid retrieval does not regress Phase 2 freshness behavior.
- Recall@10 >= 0.85 on the golden set.

If Recall@10 remains below 0.85, ship only a diagnostic/symbol slice and mark Phase 3 incomplete.
