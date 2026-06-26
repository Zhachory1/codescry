import os
import subprocess
from pathlib import Path

import pytest

from repo_index_mcp.hooks import HOOK_NAMES, install_hooks


def test_install_hooks_writes_executable_git_hooks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    installed = install_hooks(repo, command="repo-index-test")

    assert {path.name for path in installed} == set(HOOK_NAMES)
    for path in installed:
        assert "repo-index-test reindex" in path.read_text(encoding="utf-8")
        assert os.access(path, os.X_OK)


def test_install_hooks_preserves_custom_db_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "custom index.sqlite"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    installed = install_hooks(repo, command="repo-index-test", db_path=db_path)

    script = installed[0].read_text(encoding="utf-8")
    assert f"--db '{db_path.resolve()}' reindex" in script


def test_install_hooks_rejects_shell_metacharacters(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    with pytest.raises(ValueError):
        install_hooks(repo, command="repo-index; rm -rf /tmp/nope")


def test_install_hooks_does_not_overwrite_existing_hook_without_force(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("custom", encoding="utf-8")

    installed = install_hooks(repo)

    assert hook.read_text(encoding="utf-8") == "custom"
    assert hook not in installed
