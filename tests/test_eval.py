from pathlib import Path

from repo_index_mcp.eval import GoldenCase, find_rank, load_golden_cases, run_recall_eval
from repo_index_mcp.models import SearchResult


class StubEngine:
    def query(self, query: str, *, k: int) -> list[SearchResult]:
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


def test_run_recall_eval_reports_hits() -> None:
    cases = [GoldenCase(id="case-1", query="handle request", expected_path="src/app.py")]

    report = run_recall_eval(StubEngine(), cases, k=10)  # type: ignore[arg-type]

    assert report.total == 1
    assert report.hits == 1
    assert report.recall_at_k == 1.0
    assert report.misses == []
