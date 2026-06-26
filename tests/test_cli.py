import subprocess
from pathlib import Path

from repo_index_mcp.cli import main, positive_int


def test_positive_int() -> None:
    assert positive_int("3") == 3


def test_eval_returns_nonzero_when_indexing_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    golden = tmp_path / "golden.jsonl"
    repo.mkdir()
    golden.write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    result = main(["--db", str(tmp_path / "index.sqlite"), "eval", str(golden), str(repo)])

    assert result == 1
