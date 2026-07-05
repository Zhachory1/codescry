# Ranking experiment findings

This document records retrieval/ranking experiments that did not beat CodeScry's current default hybrid ranker. Keep it as context before starting new ranking work.

## Current default

CodeScry defaults to local deterministic hash embeddings plus code-specific ranking signals:

- vector similarity from `HashEmbeddingProvider`
- FTS/BM25
- lexical token overlap
- symbol match
- path match
- docs/generated path downranking
- result diversification

The default does not use OpenAI, ChatGPT, hosted embeddings, or a learned reranker.

## Eval methodology

Two eval groups were used.

### Self-repo golden eval

Dataset:

```bash
evals/golden.codescry.jsonl
```

This eval is mostly code-shaped:

- exact identifiers
- known file/module names
- implementation terms already present in the repo
- self-repo code and docs

It is useful for regression testing, but it likely overstates agent-natural retrieval quality.

### Public agent-natural evals

Datasets:

```bash
evals/public/requests.v2.32.3.agent-natural.jsonl
evals/public/flask.3.0.3.agent-natural.jsonl
evals/public/pytest.8.2.2.agent-natural.jsonl
```

Each public dataset has 25 cases against a pinned public repo. Queries use natural-language behavior wording and usually avoid exact symbol names.

Run with:

```bash
scripts/eval-public-repos.sh
```

These evals are better at exposing vocabulary mismatch, weak semantic recall, and test-vs-source confusion.

## Experiment 1: plain RRF

Flag:

```bash
CODESCRY_RRF_RANKING=1
```

Design:

- FTS/BM25 ranked list
- vector ranked list
- unweighted RRF: `score += 1 / (60 + rank)`
- no top-rank bonus
- no symbol/path/lexical ranked lists
- no query expansion change

Self-repo result:

| Mode | Recall@10 | MRR | RRF active |
| --- | ---: | ---: | ---: |
| Default hybrid | 24/26 = 0.923 | ~0.684-0.690 | n/a |
| Plain RRF | 21/26 = 0.808 | ~0.503-0.525 | 26 |

Public agent-natural result after filtered vector activation fix:

| Dataset | Default Recall/MRR | Plain RRF Recall/MRR |
| --- | ---: | ---: |
| requests | 0.520 / 0.352 | 0.560 / 0.270 |
| flask | 0.800 / 0.520 | 0.800 / 0.529 |
| pytest | 0.480 / 0.221 | 0.400 / 0.155 |

Finding:

Plain RRF can improve Recall@10 in one dataset, but it generally hurts rank quality and latency. It should stay flagged.

## Experiment 2: qmd-style RRF variants

Flag:

```bash
CODESCRY_RRF_RANKING=1 CODESCRY_RRF_QMD_STYLE=1
```

Design:

- plain RRF sources
- top-rank bonus:
  - rank 1: `+0.05`
  - rank 2-3: `+0.02`
- extra ranked lists:
  - path exact/near match
  - symbol exact/near match
  - lexical overlap

Self-repo result:

| Mode | Recall@10 | MRR | RRF active |
| --- | ---: | ---: | ---: |
| Plain RRF | 21/26 = 0.808 | ~0.503 | 26 |
| qmd-style RRF | 22/26 = 0.846 | ~0.655 | 26 |
| Default hybrid | 24/26 = 0.923 | ~0.684 | n/a |

Public agent-natural result after filtered vector activation fix:

| Dataset | Default Recall/MRR | qmd-style Recall/MRR |
| --- | ---: | ---: |
| requests | 0.520 / 0.352 | 0.400 / 0.291 |
| flask | 0.800 / 0.520 | 0.720 / 0.493 |
| pytest | 0.480 / 0.221 | 0.360 / 0.178 |

Finding:

qmd-style additions improved over plain RRF on the self-repo eval, but underperformed default hybrid and regressed public agent-natural evals. Do not promote.

## Experiment 3: filtered vector candidate activation

Problem found during RRF testing:

- public pytest eval used a repo filter
- RRF vector candidate path silently fell back because filtered vector candidates were not found
- `rrf_active_count` was `0`

Fix:

- filtered vector candidate generation now overfetches bounded sqlite-vec candidates and post-filters them
- cap is kept at sqlite-vec KNN limit
- RRF eval reports active/fallback counts

Result:

- pytest RRF now activates (`rrf_active_count = 25`)
- quality did not improve; it got worse once RRF actually ran

Finding:

Activation proof matters. Every future ranking experiment needs active/fallback counts, not only Recall@K.

## Experiment 4: hash-v2 embeddings

Local unmerged experiment:

- dimensions: 384 instead of 256
- exact token features
- light stem features
- character trigram features

Results:

| Dataset | Current default | hash-v2 experiment |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.684 | 0.923 / 0.706 |
| requests | 0.520 / 0.352 | 0.480 / 0.350 |
| flask | 0.800 / 0.520 | 0.840 / 0.566 |
| pytest | 0.480 / 0.221 | 0.360 / 0.198 |

Finding:

Hash-v2 helped Flask but hurt Requests and Pytest. It is not a safe default replacement.

## Experiment 5: broader deterministic query expansion

Local unmerged experiment expanded the hardcoded synonym map for agent-natural vocabulary, including terms like:

- choose / select / dispatch
- proxy / proxies
- fixture / fixtures
- hook / callback
- request / session

Results:

| Dataset | Current default | expanded query map |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.684 | 0.923 / 0.687 |
| requests | 0.520 / 0.352 | 0.480 / 0.347 |
| flask | 0.800 / 0.520 | 0.880 / 0.595 |
| pytest | 0.480 / 0.221 | 0.360 / 0.182 |

Finding:

Broad global expansion overfit Flask and hurt Requests/Pytest. Do not add broad synonyms globally without per-bucket evidence.

## Experiment 6: Ollama `nomic-embed-text`

Merged provider support, then evaluated local Ollama embeddings:

```bash
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=nomic-embed-text \
codescry --db <db> eval <golden> <repo> -k 10
```

Implementation fixes needed before eval was reliable:

- external providers must return provider-sized zero vectors for empty chunks
- external providers must truncate long chunks with `CODESCRY_EMBEDDING_MAX_CHARS` to avoid Ollama context-limit failures

Results:

| Dataset | Default hash | Ollama `nomic-embed-text` |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.687 | 0.962 / 0.850 |
| requests | 0.520 / 0.388 | 0.560 / 0.467 |
| flask | 0.800 / 0.523 | 0.760 / 0.533 |
| pytest | 0.480 / 0.268 | 0.480 / 0.305 |

Average latency increased because local Ollama embeddings are slower at query time and indexing time:

| Dataset | Ollama avg query latency |
| --- | ---: |
| self eval | 305ms |
| requests | 514ms |
| flask | 835ms |
| pytest | 4364ms |

Finding:

Ollama real embeddings improve self eval, requests, and pytest MRR, but regress Flask Recall@10 and add substantial latency. This is promising as an opt-in provider, not a default replacement.

## Experiment 7: Ollama `mxbai-embed-large`

Evaluated local Ollama `mxbai-embed-large` with a smaller external input limit:

```bash
CODESCRY_EMBEDDING_PROVIDER=ollama \
CODESCRY_OLLAMA_MODEL=mxbai-embed-large \
CODESCRY_EMBEDDING_MAX_CHARS=500 \
codescry --db <db> eval <golden> <repo> -k 10
```

A lower `CODESCRY_EMBEDDING_MAX_CHARS` was needed because this model returned context-length errors on longer inputs.

Results:

| Dataset | Default hash | Ollama `mxbai-embed-large` |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.687 | 0.962 / 0.909 |
| requests | 0.520 / 0.388 | 0.560 / 0.460 |
| flask | 0.800 / 0.523 | 0.800 / 0.545 |
| pytest | 0.480 / 0.268 | 0.560 / 0.345 |

Finding:

`mxbai-embed-large` is the strongest local embedding provider tested so far. It improves MRR across all public datasets and improves pytest Recall@10, but needs aggressive truncation and has higher latency than hash embeddings.

## Experiment 8: OpenAI `text-embedding-3-small`

Evaluated hosted OpenAI embeddings on self, requests, and flask before provider batching existed. After batching was added, pytest evaluation completed.

```bash
CODESCRY_EMBEDDING_PROVIDER=openai \
CODESCRY_OPENAI_MODEL=text-embedding-3-small \
codescry --db <db> eval <golden> <repo> -k 10
```

Results:

| Dataset | Default hash | OpenAI `text-embedding-3-small` |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.687 | 0.923 / 0.833 |
| requests | 0.520 / 0.388 | 0.600 / 0.483 |
| flask | 0.800 / 0.523 | 0.760 / 0.553 |
| pytest | 0.480 / 0.268 | 0.520 / 0.378 |

Finding:

OpenAI improves requests and pytest MRR, but regresses flask Recall@10 and has hosted data-boundary costs. Provider batching is required for larger repos.

## Experiment 9: sentence-transformers `BAAI/bge-small-en-v1.5`

Evaluated local sentence-transformers embeddings:

```bash
CODESCRY_EMBEDDING_PROVIDER=sentence-transformers \
CODESCRY_ST_MODEL=BAAI/bge-small-en-v1.5 \
codescry --db <db> eval <golden> <repo> -k 10
```

Results:

| Dataset | Default hash | BGE small |
| --- | ---: | ---: |
| self eval | 0.923 / ~0.687 | 0.923 / 0.846 |
| requests | 0.520 / 0.388 | 0.600 / 0.465 |
| flask | 0.800 / 0.523 | 0.720 / 0.514 |
| pytest | 0.480 / 0.268 | 0.560 / 0.348 |

Finding:

BGE small improves requests and pytest, with better pytest latency than Ollama. It regresses flask Recall@10. It is a good opt-in local provider candidate, but not a clear default replacement.

## Overall findings

1. Current hybrid ranking remains strongest default overall.
2. Plain RRF and qmd-style RRF are useful as experiments, not defaults.
3. Natural-language public evals reveal much lower recall than self-repo exact/symbol evals.
4. RRF with weak hash vectors does not reproduce qmd's likely benefit from stronger embeddings and reranking.
5. Broad deterministic expansion is risky because it can add noisy candidates and dilute exact matches.
6. Hash embedding tweaks can help one repo and hurt another.
7. Real embeddings improve MRR more reliably than rank-fusion tweaks, but still show repo-specific recall regressions.
8. `mxbai-embed-large` and BGE small are the best local candidates tested so far.
9. Hosted OpenAI now has batching, but still needs careful data-boundary review and latency/cost tracking.
10. Active/fallback instrumentation is mandatory; a flagged ranking mode can otherwise appear to pass while silently falling back.

## Recommended next bets

### Better embeddings, but opt-in

The most likely large improvement is better embeddings, not more rank fusion. Options:

- local embedding model, opt-in
- hosted embedding provider, explicit opt-in with data-boundary warning

Ollama `mxbai-embed-large`, sentence-transformers BGE small, and OpenAI `text-embedding-3-small` are good enough to keep as supported opt-in providers, but not good enough to make default based on current evals.

Requirements before shipping:

- clear model/provider config
- no source upload by default
- eval comparison across self + public datasets
- latency/storage impact report
- docs explaining data boundary

### Narrow query expansion by query class

If query expansion continues, avoid broad global synonyms. Safer approach:

- target one failure bucket at a time
- add 3-5 cases proving need
- add expansion only for that bucket
- require no regression across public datasets

### Keep RRF as diagnostic mode

RRF modes can stay useful for comparison and ablation, but should not be default unless they beat default hybrid on:

- self eval
- public agent-natural evals
- MRR, not only Recall@10
- latency

## Commands used

Default eval:

```bash
codescry --db <db> eval <golden.jsonl> <repo> -k 10
```

Plain RRF eval:

```bash
CODESCRY_RRF_RANKING=1 codescry --db <db> eval <golden.jsonl> <repo> -k 10 --debug
```

qmd-style RRF eval:

```bash
CODESCRY_RRF_RANKING=1 CODESCRY_RRF_QMD_STYLE=1 codescry --db <db> eval <golden.jsonl> <repo> -k 10 --debug
```

Public evals:

```bash
scripts/eval-public-repos.sh
```
