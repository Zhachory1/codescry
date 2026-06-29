import importlib.util
import subprocess
from pathlib import Path

import pytest

from repo_index_mcp.cli import main, positive_int
from repo_index_mcp.doctor import run_doctor


def test_positive_int() -> None:
    assert positive_int("3") == 3


def test_doctor_returns_healthy_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(module: str):  # type: ignore[no-untyped-def]
        if module == "mcp":
            return object()
        return real_find_spec(module)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    result, exit_code = run_doctor(tmp_path / "index.sqlite")

    assert exit_code == 0
    assert result["ok"] is True
    assert result["checks"]["git"]["ok"] is True
    assert result["checks"]["db_writable"]["ok"] is True
    assert result["checks"]["mcp_dependency"]["ok"] is True


def test_doctor_returns_nonzero_for_unwritable_db_path(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-db"
    directory.mkdir()

    result, exit_code = run_doctor(directory)

    assert exit_code == 1
    assert result["ok"] is False
    assert result["checks"]["db_writable"]["ok"] is False


def test_get_symbol_cli_returns_symbol(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.sqlite"
    repo.mkdir()
    (repo / "app.py").write_text("def hello_world():\n    return True\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    assert main(["--db", str(db_path), "index", str(repo)]) == 0
    assert main(["--db", str(db_path), "get-symbol", "hello_world"]) == 0

    output = capsys.readouterr().out
    assert "hello_world" in output
    assert "app.py" in output


def test_query_empty_result_prints_hint(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    db_path = tmp_path / "index.sqlite"

    assert main(["--db", str(db_path), "query", "nothing"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"
    assert "No results" in captured.err


def test_eval_returns_nonzero_when_indexing_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    golden = tmp_path / "golden.jsonl"
    repo.mkdir()
    golden.write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    result = main(["--db", str(tmp_path / "index.sqlite"), "eval", str(golden), str(repo)])

    assert result == 1


def init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            message,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
    )
