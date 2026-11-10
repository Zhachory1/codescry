import os
import subprocess
from pathlib import Path

import pytest

from repo_index_mcp.hooks import HOOK_NAMES, inspect_hooks, install_hooks, summarize_hook_status


def test_install_hooks_writes_executable_git_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    installed = install_hooks(repo, command="codescry-test")

    assert {path.name for path in installed} == set(HOOK_NAMES)
    for path in installed:
        assert "codescry-test reindex" in path.read_text(encoding="utf-8")
        assert os.access(path, os.X_OK)


def test_install_hooks_preserves_custom_db_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom index.sqlite"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    installed = install_hooks(repo, command="codescry-test", db_path=db_path)

    script = installed[0].read_text(encoding="utf-8")
    assert f"--db '{db_path.resolve()}' reindex" in script


def test_install_hooks_rejects_shell_metacharacters(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    with pytest.raises(ValueError):
        install_hooks(repo, command="codescry; rm -rf /tmp/nope")


def test_install_hooks_does_not_overwrite_existing_hook_without_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("custom", encoding="utf-8")

    installed = install_hooks(repo)

    assert hook.read_text(encoding="utf-8") == "custom"
    assert hook not in installed


def test_inspect_hooks_reports_installed_codescry_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    install_hooks(repo, command="codescry-test")

    status = inspect_hooks(repo)

    assert status["installed"] is True
    assert status["missing"] == []
    assert status["non_codescry"] == []


def test_inspect_hooks_reports_missing_and_custom_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho custom\n", encoding="utf-8")
    hook.chmod(0o755)

    status = inspect_hooks(repo)

    assert status["installed"] is False
    assert status["missing"] == ["post-merge"]
    assert status["non_codescry"] == ["post-commit"]


def test_summarize_hook_status_counts_repos_missing_any_hook() -> None:
    summary = summarize_hook_status(
        [
            {"repo_path": "/repo/ok", "freshness_hooks": {"installed": True}},
            {
                "repo_path": "/repo/missing",
                "freshness_hooks": {
                    "installed": False,
                    "missing": ["post-commit"],
                    "non_codescry": [],
                    "non_executable": [],
                },
            },
        ]
    )

    assert summary["repos_checked"] == 2
    assert summary["installed"] == 1
    assert summary["missing"] == 1
    assert summary["missing_repos"] == [
        {
            "repo_path": "/repo/missing",
            "missing": ["post-commit"],
            "non_codescry": [],
            "non_executable": [],
            "error": None,
        }
    ]
