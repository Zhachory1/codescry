from pathlib import Path

from repo_index_mcp.eval import (
    GoldenCase,
    find_rank,
    load_golden_cases,
    mean_reciprocal_rank,
    run_recall_diagnostics,
    run_recall_eval,
)
from repo_index_mcp.models import SearchResult


class StubEngine:
    def query(
        self,
        query: str,
        *,
        k: int,
        repo: str | None = None,
    ) -> list[SearchResult]:
        return [
            SearchResult(
                repo="repo",
                path="src/app.py",
                start_line=1,
                end_line=3,
                snippet=f"def handle():\n    {query}\n",
                score=1.0,
                language="python",
            )
        ][:k]

    def query_debug(
        self,
        query: str,
        *,
        k: int,
        repo: str | None = None,
    ):  # type: ignore[no-untyped-def]
        return [
            {
                "result": result,
                "score": {
                    "raw_vector": 1.0,
                    "normalized_vector": 1.0,
                    "lexical": 1.0,
                    "symbol": 0.0,
                    "path": 0.0,
                    "path_multiplier": 1.0,
                    "base": result.score,
                    "score": result.score,
                },
            }
            for result in self.query(query, k=k)
        ]

    def expected_path_debug(
        self,
        query: str,
        *,
        expected_path: str,
        expected_text: str | None = None,
        repo: str | None = None,
    ) -> dict[str, object]:
        return {
            "expected_found_in_index": True,
            "expected_path_found_in_index": True,
            "expected_best_rank": 1,
            "expected_best_score_parts": {"score": 1.0, "lexical": 1.0},
            "expected_text_match": expected_text is None,
        }


def test_load_golden_cases_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"id":"case-1","query":"handle request","expected_path":"src/app.py"}\n',
        encoding="utf-8",
    )

    cases = load_golden_cases(path)

    assert cases == [GoldenCase(id="case-1", query="handle request", expected_path="src/app.py")]


def test_find_rank_requires_expected_text_when_present() -> None:
    case = GoldenCase(
        id="case-1",
        query="handle request",
        expected_path="src/app.py",
        expected_text="target text",
    )
    results = [
        SearchResult(
            repo="repo",
            path="src/app.py",
            start_line=1,
            end_line=3,
            snippet="wrong text",
            score=1.0,
            language="python",
        ),
        SearchResult(
            repo="repo",
            path="src/app.py",
            start_line=4,
            end_line=6,
            snippet="target text",
            score=0.9,
            language="python",
        ),
    ]

    assert find_rank(case, results) == 2


def test_run_recall_diagnostics_includes_score_parts() -> None:
    cases = [GoldenCase(id="case-1", query="handle request", expected_path="src/app.py")]

    report = run_recall_diagnostics(StubEngine(), cases, k=10)  # type: ignore[arg-type]

    top_result = report["cases"][0]["top_results"][0]  # type: ignore[index]
    assert report["recall_at_k"] == 1.0
    assert "score_parts" in top_result
    assert top_result["score_parts"]["lexical"] == 1.0


def test_run_recall_eval_reports_hits() -> None:
    cases = [GoldenCase(id="case-1", query="handle request", expected_path="src/app.py")]

    report = run_recall_eval(StubEngine(), cases, k=10)  # type: ignore[arg-type]

    assert report.total == 1
    assert report.hits == 1
    assert report.recall_at_k == 1.0
    assert report.mrr == 1.0
    assert report.misses == []


def test_mean_reciprocal_rank_handles_hits_and_misses() -> None:
    assert mean_reciprocal_rank([1, 2, None]) == (1 + 0.5 + 0) / 3


def test_run_recall_diagnostics_reports_rrf_active_count() -> None:
    class RrfStubEngine(StubEngine):
        def query_debug(
            self,
            query: str,
            *,
            k: int,
            repo: str | None = None,
        ):  # type: ignore[no-untyped-def]
            rows = super().query_debug(query, k=k, repo=repo)
            rows[0]["score"] = {
                "ranking_mode": "rrf_v1",
                "score": 1.0,
                "rrf_score": 1.0,
                "source_ranks": {"fts": 1, "vector": 1},
                "source_contributions": {"fts": 1 / 61, "vector": 1 / 61},
            }
            return rows

    cases = [GoldenCase(id="case-1", query="handle request", expected_path="src/app.py")]

    report = run_recall_diagnostics(RrfStubEngine(), cases, k=10)  # type: ignore[arg-type]

    assert report["rrf_active_count"] == 1
    assert report["fallback_count"] == 0
