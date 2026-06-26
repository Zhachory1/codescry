from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from repo_index_mcp.engine import RepoIndex
from repo_index_mcp.models import SearchResult


@dataclass(frozen=True)
class GoldenCase:
    id: str
    query: str
    expected_path: str
    expected_text: str | None = None
    expected_symbol: str | None = None
    notes: str | None = None


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


def run_recall_eval(engine: RepoIndex, cases: list[GoldenCase], *, k: int = 10) -> EvalReport:
    results: list[CaseResult] = []
    for case in cases:
        start = time.monotonic()
        search_results = engine.query(case.query, k=k)
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
        avg_latency_ms=avg_latency_ms,
        cases=results,
    )


def find_rank(case: GoldenCase, results: list[SearchResult]) -> int | None:
    expected_text = case.expected_text.lower() if case.expected_text else None
    for index, result in enumerate(results, start=1):
        if result.path != case.expected_path:
            continue
        if expected_text and expected_text not in result.snippet.lower():
            continue
        return index
    return None


def format_report(report: EvalReport) -> str:
    lines = [
        f"Recall@{report.k}: {report.hits}/{report.total} = {report.recall_at_k:.3f}",
        f"Avg latency: {report.avg_latency_ms:.1f}ms",
    ]
    if report.misses:
        lines.append("Misses:")
        for miss in report.misses:
            top = ", ".join(miss.top_paths[:5])
            lines.append(f"- {miss.id}: expected {miss.expected_path}; top={top}")
    return "\n".join(lines)
