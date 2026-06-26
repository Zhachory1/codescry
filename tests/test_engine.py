from __future__ import annotations

import subprocess
from pathlib import Path

from repo_index_mcp.engine import RepoIndex


def test_index_repo_and_query_returns_code(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def retry_request(max_attempts):\n"
        "    for attempt in range(max_attempts):\n"
        "        print('retrying request')\n",
        encoding="utf-8",
    )
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    index_result = engine.index_repo(repo)
    results = engine.query("retry request", k=1)

    assert index_result.files_indexed == 1
    assert index_result.chunks_indexed == 1
    assert results[0].path == "app.py"
    assert "retry_request" in results[0].snippet


def test_status_marks_repo_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "app.py").write_text("print('two')\n", encoding="utf-8")
    commit_all(repo, "change")

    repos = engine.list_repos()

    assert repos[0]["is_stale"] is True


def init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
