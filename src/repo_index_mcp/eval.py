from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from repo_index_mcp.engine import RepoIndex
from repo_index_mcp.models import SearchResult
from repo_index_mcp.storage import is_docs_path, is_generated_path, rrf_ranking_enabled


@dataclass(frozen=True)
class GoldenCase:
    id: str
    query: str
    expected_path: str
    expected_text: str | None = None
    expected_symbol: str | None = None
    notes: str | None = None
    repo_url: str | None = None
    repo_ref: str | None = None
    query_type: str | None = None


@dataclass(frozen=True)
class CaseResult:
    id: str
    query: str
    expected_path: str
    hit: bool
    rank: int | None
    latency_ms: int
    top_paths: list[str]


@dataclass(frozen=True)
class EvalReport:
    k: int
    total: int
    hits: int
    recall_at_k: float
    mrr: float
    avg_latency_ms: float
    cases: list[CaseResult]

    @property
    def misses(self) -> list[CaseResult]:
        return [case for case in self.cases if not case.hit]

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["misses"] = [asdict(case) for case in self.misses]
        return data


def load_golden_cases(path: str | Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    with Path(path).open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            payload = json.loads(stripped)
            try:
                cases.append(GoldenCase(**payload))
            except TypeError as exc:
                raise ValueError(f"invalid golden case at line {line_number}: {exc}") from exc
    return cases


def run_recall_eval(
    engine: RepoIndex,
    cases: list[GoldenCase],
    *,
    k: int = 10,
    repo: str | None = None,
) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        start = time.monotonic()
        search_results = engine.query(case.query, k=k, repo=repo)
        latency_ms = int((time.monotonic() - start) * 1000)
        rank = find_rank(case, search_results)
        results.append(
            CaseResult(
                id=case.id,
                query=case.query,
                expected_path=case.expected_path,
                hit=rank is not None,
                rank=rank,
                latency_ms=latency_ms,
                top_paths=[result.path for result in search_results],
            )
        )

    hits = sum(1 for result in results if result.hit)
    total = len(results)
    avg_latency_ms = (
        sum(result.latency_ms for result in results) / total if total else 0.0
    )
    return EvalReport(
        k=k,
        total=total,
        hits=hits,
        recall_at_k=hits / total if total else 0.0,
        mrr=mean_reciprocal_rank([result.rank for result in results]),
        avg_latency_ms=avg_latency_ms,
        cases=results,
    )


def run_recall_diagnostics(
    engine: RepoIndex,
    cases: list[GoldenCase],
    *,
    k: int = 10,
    repo: str | None = None,
) -> dict[str, object]:
    case_reports: list[dict[str, object]] = []
    hits = 0
    latencies: list[int] = []
    ranks: list[int | None] = []
    rrf_active_count = 0
    fallback_reason_counts: dict[str, int] = {}
    for case in cases:
        start = time.monotonic()
        debug_rows = engine.query_debug(case.query, k=k, repo=repo)
        expected_debug = engine.expected_path_debug(
            case.query,
            expected_path=case.expected_path,
            expected_text=case.expected_text,
            repo=repo,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        latencies.append(latency_ms)
        results = [row["result"] for row in debug_rows]
        rank = find_rank(case, results)  # type: ignore[arg-type]
        hit = rank is not None
        hits += int(hit)
        ranks.append(rank)
        rrf_state = rrf_case_state(debug_rows)
        if rrf_state[0]:
            rrf_active_count += 1
        elif rrf_state[1] is not None:
            fallback_reason_counts[rrf_state[1]] = fallback_reason_counts.get(rrf_state[1], 0) + 1
        elif rrf_ranking_enabled():
            fallback_reason_counts["no_results"] = fallback_reason_counts.get("no_results", 0) + 1
        case_reports.append(
            {
                "id": case.id,
                "query": case.query,
                "expected_path": case.expected_path,
                "expected_text": case.expected_text,
                "hit": hit,
                "rank": rank,
                "latency_ms": latency_ms,
                "top_results": [debug_result_to_dict(row) for row in debug_rows],
                "diagnostics": case_diagnostics(case, debug_rows, expected_debug),
            }
        )
    total = len(cases)
    fallback_count = sum(fallback_reason_counts.values())
    return {
        "k": k,
        "total": total,
        "hits": hits,
        "recall_at_k": hits / total if total else 0.0,
        "mrr": mean_reciprocal_rank(ranks),
        "avg_latency_ms": sum(latencies) / total if total else 0.0,
        "rrf_active_count": rrf_active_count,
        "fallback_count": fallback_count,
        "fallback_reason_counts": fallback_reason_counts,
        "cases": case_reports,
        "misses": [case for case in case_reports if not case["hit"]],
    }


def rrf_case_state(rows: list[dict[str, object]]) -> tuple[bool, str | None]:
    fallback_reason: str | None = None
    for row in rows:
        score = row.get("score")
        if not isinstance(score, dict):
            continue
        if score.get("ranking_mode") == "rrf_v1":
            return True, None
        reason = score.get("rrf_disabled_reason")
        if isinstance(reason, str):
            fallback_reason = reason
    return False, fallback_reason


def debug_result_to_dict(row: dict[str, object]) -> dict[str, object]:
    result = row["result"]
    return {
        "repo": result.repo,
        "path": result.path,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "score": result.score,
        "language": result.language,
        "symbol_name": result.symbol_name,
        "symbol_kind": result.symbol_kind,
        "symbol_confidence": result.symbol_confidence,
        "is_stale": result.is_stale,
        "has_dirty_tracked_files": result.has_dirty_tracked_files,
        "path_class": classify_path(result.path),
        "score_parts": row["score"],
    }


def case_diagnostics(
    case: GoldenCase,
    rows: list[dict[str, object]],
    expected_debug: dict[str, object],
) -> dict[str, object]:
    top_results = [row["result"] for row in rows]
    top_paths = [result.path for result in top_results]
    classes = [classify_path(path) for path in top_paths]
    return {
        "expected_path_in_top_k": any(result.path == case.expected_path for result in top_results),
        "expected_match_in_top_k": find_rank(case, top_results) is not None,
        **expected_debug,
        "top_path_counts": path_counts(top_paths),
        "code_results": classes.count("code"),
        "docs_results": classes.count("docs"),
        "generated_results": classes.count("generated"),
    }


def classify_path(path: str) -> str:
    if is_generated_path(path):
        return "generated"
    if is_docs_path(path):
        return "docs"
    return "code"


def path_counts(paths: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for path in paths:
        counts[path] = counts.get(path, 0) + 1
    return counts


def find_rank(case: GoldenCase, results: list[SearchResult]) -> int | None:
    expected_text = case.expected_text.lower() if case.expected_text else None
    for index, result in enumerate(results, start=1):
        if result.path != case.expected_path:
            continue
        if expected_text and expected_text not in result.snippet.lower():
            continue
        return index
    return None


def mean_reciprocal_rank(ranks: list[int | None]) -> float:
    if not ranks:
        return 0.0
    return sum((1 / rank) if rank is not None else 0.0 for rank in ranks) / len(ranks)


def format_report(report: EvalReport) -> str:
    lines = [
        f"Recall@{report.k}: {report.hits}/{report.total} = {report.recall_at_k:.3f}",
        f"MRR: {report.mrr:.3f}",
        f"Avg latency: {report.avg_latency_ms:.1f}ms",
    ]
    if report.misses:
        lines.append("Misses:")
        for miss in report.misses:
            top = ", ".join(miss.top_paths[:5])
            lines.append(f"- {miss.id}: expected {miss.expected_path}; top={top}")
    return "\n".join(lines)
