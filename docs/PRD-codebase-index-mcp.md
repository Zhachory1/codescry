# Product Requirements Document (PRD): Local Codebase Index + MCP Retrieval Service

*A PRD defines the **problem, constraints, and success metrics** — the "what" and "why." It precedes the design document, which defines the "how." This document deliberately leaves the detailed architecture (embedding model, vector store, chunking strategy, ranking) to the design phase. See `Open Questions` for the forks to resolve there.*

**Status:** Draft
**Last updated:** Jun 26, 2026
**Owner:** *[Your name]*
**Working name:** `repo-index-mcp` *(placeholder — rename freely)*
**Next artifact:** Design Document

---

## 1. Problem Statement

When a coding agent (Cursor, Claude Code, Copilot, etc.) needs to understand or modify code that spans multiple repositories, it relies on:

- Naive file globbing / grep, which is keyword-bound and misses semantically-related code that uses different vocabulary.
- The engineer manually locating and pasting relevant files into context, which is tedious and burns the agent's limited context window on irrelevant material.
- Re-discovering the same code repeatedly across sessions because there is no persistent, queryable index.

The pain is acute in large or unfamiliar codebases: an engineer or agent landing in a new service has no fast, semantic way to answer *"where is the code that does X?"* across a directory of repos.

### Why Now

Coding agents are only as good as the context they're given. Cheap, local, semantic code retrieval is the highest-leverage missing piece in the local agentic loop — and the cost of not having it compounds with every new repo. A high-quality retrieval layer is a force multiplier for every agent-assisted task.

---

## 2. Goals and Non-Goals

### 2.1 Goals (In Scope)

| # | Goal |
|---|------|
| G1 | Point the tool at a local directory containing one or more git repos and **index** the code into a persistent local store. |
| G2 | Support **semantic retrieval** ("find code related to *concept*") in addition to keyword/symbol lookup. |
| G3 | Expose retrieval to a **local coding agent via MCP** so the agent can self-serve relevant code without the human pasting files. |
| G4 | Keep the index **fresh** as code changes — incremental, git-aware re-indexing rather than full rebuilds. |
| G5 | Be **portable and self-contained** — runnable on a laptop with minimal setup and a swappable storage backend (SQLite for solo/local, Postgres for larger/shared). |

### 2.2 Non-Goals (Out of Scope for v1)

| # | Non-Goal | Rationale |
|---|----------|-----------|
| N1 | A hosted, multi-tenant, team-shared service with auth/RBAC. | Start local-first. Promote to a shared service only after the local tool proves value. |
| N2 | Replacing version control as the source of truth for code. | This tool is a **read-only derived index**, never a source of truth. |
| N3 | Code generation, refactoring, or write-back to repos. | This is a *retrieval* tool. The agent acts on results; the tool does not mutate code. |
| N4 | Indexing non-code artifacts (issue trackers, wikis, chat). | Possible future; keep v1 focused on the codebase. |
| N5 | A polished UI. | The MCP interface (for agents) and a thin CLI (for humans) are sufficient for v1. |

> Aggressively scoping non-goals is the cheapest risk reduction available. N1 in particular: a local single-user tool is far simpler than a shared service and de-risks the whole effort.

---

## 3. North Star Metric & Success Metrics

> **No baseline exists today** for "how long does it take an agent/engineer to find the right code." Establishing that baseline is a prerequisite (see §9). The targets below are *hypotheses* to validate, not commitments.

### 3.1 North Star

**Retrieval usefulness:** *the share of agent retrieval queries that return the relevant code within the top-K results.* If the agent asks and gets the right code, everything else (speed, adoption) follows.

### 3.2 Success Metrics

| Metric Type | Measurement | Target |
|---|---|---|
| **North Star (Quality)** | Recall@10 on a curated "golden query → expected file/symbol" eval set | ≥ 0.85 |
| **Quality (Precision)** | Top-5 results judged relevant (manual or LLM-judge sample) | ≥ 70% |
| **Productivity** | Median time / manual file-pastes to assemble context for an agent task vs. baseline | ≥ 50% reduction |
| **Latency (UX)** | p95 query latency (warm index) | ≤ 500 ms local |
| **Freshness** | Lag between a commit and that change being queryable | ≤ 60 s (incremental) |
| **Indexing throughput** | Full cold index of a medium repo (~100k LOC) | ≤ 10 min |
| **Adoption** | # engineers with the MCP wired into their agent after 4 weeks | *[TODO: set a target]* |

> Recall@10 is a *proxy* for "the agent did better work." Triangulate it with the productivity metric and at least one qualitative signal (do people keep it turned on?). Don't optimize the proxy into the ground.

---

## 4. Users and Key Use Cases

### 4.1 Personas

- **Primary: The local coding agent.** Consumes the MCP. Needs structured, ranked, token-efficient results (snippets + file paths + line ranges), not whole files.
- **Secondary: The engineer.** Configures the tool, points it at repos, and occasionally queries it directly via CLI for orientation in unfamiliar code.

### 4.2 User Stories

| ID | As a… | I want to… | So that… |
|----|-------|-----------|----------|
| U1 | coding agent | semantically search across all indexed repos for code related to a concept | I can pull only the relevant snippets into my context window |
| U2 | coding agent | retrieve by symbol/definition (function, class, endpoint) | I can ground edits in the actual implementation |
| U3 | coding agent | get results scoped to a single repo or path prefix | I avoid cross-repo noise when the task is local |
| U4 | engineer | point the tool at `~/code/` and have it discover and index every git repo under it | I don't manually register each repo |
| U5 | engineer | have the index update automatically when I pull/commit | results never reflect stale code |
| U6 | engineer | choose SQLite (zero-setup) or Postgres (bigger/shared) via config | the tool fits both my laptop and a beefier shared box later |

---

## 5. Constraints

| # | Constraint | Hard/Soft | Notes |
|---|-----------|-----------|-------|
| C1 | **Local-first / offline-capable.** No network dependency on the query path. | Hard | Protects the "agent is mid-task" loop and avoids cloud cost/latency. |
| C2 | **Source code must not leave the trust boundary in plaintext to an unapproved third party.** | **Hard — security** | See §7. The single biggest risk. |
| C3 | **Swappable storage backend** (SQLite ↔ Postgres) behind one interface. | Hard | Program to an interface (`StorageProvider`), not a concrete store. |
| C4 | **Mainstream implementation language** (e.g., Python for embedding-heavy work, or Go for a daemon/server). | Soft | Pick in the design phase based on where the weight sits. |
| C5 | **MCP-compliant interface.** Any local agent can consume it. | Hard | |
| C6 | **Git-aware.** Understands commits/branches for cheap incremental updates. | Hard | Full re-index on every change fails the freshness target. |
| C7 | **Prefer proven technology.** No exotic datastore without a clear, documented justification. | Soft | Postgres + a vector extension is proven and sufficient. |

---

## 6. Functional Requirements

Prioritized P0 (must-have for v1), P1 (fast follow), P2 (later).

### 6.1 Ingestion & Discovery
- **[P0]** Recursively discover git repos under a configured root directory.
- **[P0]** Walk tracked files (respect `.gitignore`); skip binaries, vendored deps/`node_modules`, build artifacts, and lockfiles by default (configurable).
- **[P0]** Language-aware **chunking** of source files (by function/class/logical block rather than fixed byte windows) — *strategy TBD in design phase.*
- **[P1]** Configurable include/exclude globs and per-repo overrides.

### 6.2 Indexing & Storage
- **[P0]** Generate embeddings per chunk and persist them with metadata (repo, path, language, symbol name, line range, commit SHA).
- **[P0]** Storage abstraction with at least two implementations: **SQLite** (default, zero-setup) and **Postgres** (larger/shared).
- **[P0]** **Incremental, git-aware re-indexing**: on change, only re-embed chunks in files whose content changed since the last indexed commit (diff against stored SHAs).
- **[P1]** Background watch (file-watcher or git-hook) to trigger incremental updates automatically (satisfies U5 / freshness target).
- **[P2]** Multi-branch awareness (index a working branch without clobbering main's index).

### 6.3 Retrieval
- **[P0]** Semantic top-K query: text query → ranked chunks with file path + line range + snippet.
- **[P0]** Filters: scope by repo, path prefix, and/or language.
- **[P1]** **Hybrid retrieval** (semantic + lexical/keyword + symbol match) with a blended rank — typically beats pure-vector for code.
- **[P1]** Symbol/definition lookup ("find the definition of `calculate_total`").
- **[P2]** "Related code" expansion (given a file/symbol, find neighbors).

### 6.4 MCP Interface (the contract)
- **[P0]** Expose an MCP server with, at minimum, these tools (final names/schemas defined in the design phase):
  - `search_code(query, repo?, path_prefix?, language?, k?)` → ranked snippets.
  - `get_symbol(name, repo?)` → definition + location.
  - `list_repos()` → indexed repos and their freshness/commit state.
  - `reindex(repo?)` → trigger a refresh (with status).
- **[P0]** Results must be **token-efficient**: return snippets + locations, not whole files, so the agent fetches detail only when needed.
- **[P1]** Stable, versioned tool schemas with backward compatibility — saves pain when agents depend on it.

### 6.5 Configuration & Ops
- **[P0]** Single config file (root dir, backend, embedding provider, include/exclude, model).
- **[P0]** A thin **CLI** for humans: `index`, `query`, `status`, `reindex`.
- **[P1]** Basic structured logging + a `status` view (chunks indexed, last commit, staleness, errors).

---

## 7. Non-Functional Requirements

### 7.1 Security & Data Isolation

> Source code is often proprietary and may reference secrets or sensitive data. Treat the embedding path as the primary security surface.

- **[P0]** **Embedding generation must not send code to an unapproved external API.** Either (a) use a **local/self-hosted embedding model**, or (b) use a vetted/approved embedding endpoint. Make this an explicit, documented decision.
- **[P0]** Never index secrets: detect and skip `.env`, key files, and credential blobs; integrate secret scanning.
- **[P0]** The index store is local and access-controlled; if Postgres is shared, apply least-privilege access and encryption at rest.
- **[P1]** Config flag to hard-exclude specific repos/paths known to contain sensitive data.

### 7.2 Reliability & Portability
- **[P0]** Indexing failures on one file must not abort the whole run (catch specific errors, log with context, continue).
- **[P0]** Index is reconstructible from scratch (it's derived data — a corrupt index is a rebuild, never a data-loss event).
- **[P1]** Graceful degradation: if semantic retrieval is unavailable, fall back to lexical search rather than returning nothing.

### 7.3 Performance
- Targets per §3.2 (p95 ≤ 500 ms query; ≤ 60 s freshness; ≤ 10 min cold index of ~100k LOC). Validate against the baseline.

---

## 8. Open Questions / Decisions for the Design Phase

> These are the real forks. The design doc should present **≥2 options with Pro/Con** for each and defend a choice.

1. **Embedding model.** Local (self-hosted, e.g., a code-tuned open model) vs. a hosted endpoint. Trade-off: quality & cost vs. security/data-isolation (C2).
2. **Vector store.** SQLite + a vector extension vs. Postgres + `pgvector` vs. a dedicated vector DB. *A dedicated vector DB needs a strong justification over the simpler options.*
3. **Chunking strategy.** Fixed-size windows vs. AST/tree-sitter-based semantic chunking. Materially affects retrieval quality.
4. **Hybrid ranking.** How to blend semantic + lexical + symbol signals; is reranking worth it for v1?
5. **Freshness mechanism.** Git post-commit hook vs. file-watcher vs. periodic poll.
6. **Embedding granularity & re-embed cost.** How to minimize re-embedding on small diffs (chunk-level SHA tracking).
7. **Multi-repo identity.** How repos are namespaced so cross-repo results stay attributable.

---

## 9. Measurement Plan (Establishing the Baseline)

Impact can't be proven without a baseline.

1. **Build a golden eval set** (before/alongside build): 30–50 real queries → known correct file/symbol answers, drawn from actual repos. This powers Recall@10 / Precision metrics and regression-guards future changes.
2. **Capture the productivity baseline:** for ~5–10 representative agent tasks, log today's time + manual file-pastes to assemble context *without* the tool. Re-measure with the tool.
3. **Instrument query logs:** record query, top-K returned, latency, and (where feasible) whether the agent used the result — for ongoing quality monitoring.

---

## 10. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Proprietary code sent to an external embedding API | Med | **High** | Hard constraint C2; explicit embedding-provider decision; secret exclusion (§7.1) |
| Retrieval quality too low to be useful (agents ignore it) | Med | High | Golden eval set first; hybrid retrieval; iterate against Recall@10 before broad adoption |
| Re-index cost makes the freshness target unachievable | Med | Med | Chunk-level SHA diffing; incremental-only updates; benchmark early |
| Scope creep toward a shared service prematurely | Med | Med | N1 non-goal; expand to shared only after local value is proven |
| Index drift / staleness silently serving old code | Low | Med | Surface freshness in `list_repos()`/`status`; staleness warnings |

---

## 11. Dependencies

- **MCP client support** in the target coding agent(s) — confirm which agents will consume it.
- **Embedding model/endpoint** access decision (a security review may gate the hosted option).
- **Storage:** local SQLite (none) / a Postgres instance (if shared).

---

## 12. Phased Delivery (Indicative — refine in the design phase)

- **Phase 0 — Baseline & eval set.** Golden queries + productivity baseline captured. *Exit: measurable baseline exists.*
- **Phase 1 — Walking skeleton.** Index one repo (SQLite), naive chunking, semantic `search_code` over MCP, CLI query. *Exit: agent retrieves real code end-to-end.*
- **Phase 2 — Fresh & multi-repo.** Incremental git-aware re-index, multi-repo discovery, filters. *Exit: freshness target met across a directory of repos.*
- **Phase 3 — Quality.** Hybrid retrieval + symbol lookup; tune against the eval set to hit Recall@10 ≥ 0.85. *Exit: North Star target met.*
- **Phase 4 — Hardening / Postgres backend.** Pluggable Postgres store, logging/status, secret exclusion verified. *Exit: ready to share.*

---

## 13. Known Shortcuts & Future Work

Naming the shortcuts now keeps them from rotting silently.

- **v1 shortcuts to track:** naive chunking in Phase 1 (replace with semantic chunking in Phase 3); single-branch indexing (multi-branch deferred to P2); no shared auth (deferred with N1).
- **Future direction:** if the local tool proves its value, expand to a shared, multi-user service — at which point auth, RBAC, and a managed datastore become required and warrant their own design doc.
