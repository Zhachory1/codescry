import subprocess
from pathlib import Path

from repo_index_mcp.repo import (
    content_hash,
    discover_repos,
    discover_repos_with_skipped,
    sanitize_remote_url,
    should_prune_dir,
    should_skip,
)


def test_discover_repos_skips_generated_dirs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    generated = tmp_path / "node_modules" / "dep"
    repo.mkdir()
    generated.mkdir(parents=True)
    (repo / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (generated / "app.py").write_text("print('skip')\n", encoding="utf-8")
    init_repo(repo)
    init_repo(generated)
    commit_all(repo, "init")
    commit_all(generated, "init")

    assert discover_repos(tmp_path) == [repo]


def test_discover_repos_skips_linked_worktree_by_default(tmp_path: Path) -> None:
    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / ".git").write_text("gitdir: /tmp/example.git/worktrees/worktree\n", encoding="utf-8")

    repos, skipped = discover_repos_with_skipped(tmp_path)

    assert repos == []
    assert skipped == 1
    assert discover_repos(tmp_path, include_worktrees=True) == [repo]


def test_skip_rules() -> None:
    assert should_skip(".env") is True
    assert should_skip("node_modules/pkg/index.js") is True
    assert should_skip("rokt/rdn/raw/v1/create.pb.go") is True
    assert should_skip(".zbrain/index.sqlite") is True
    assert should_skip(".zbrain/index.sqlite.tmp-wal") is True
    assert should_skip("cache/data.parquet") is True
    assert should_skip("data/events.jsonl") is True
    assert should_skip("src/app.py") is False
    assert should_prune_dir("node_modules") is True
    assert should_prune_dir(".zbrain") is True


def test_sanitize_remote_url_removes_userinfo() -> None:
    assert (
        sanitize_remote_url("https://ghp_secret@github.com/acme/repo.git?token=secret#frag")
        == "https://github.com/acme/repo.git"
    )
    assert sanitize_remote_url("git@github.com:acme/repo.git") == "git@github.com:acme/repo.git"
    assert sanitize_remote_url("TOKEN@gitlab.com:acme/repo.git") == "gitlab.com:acme/repo.git"


def test_content_hash_changes_with_content() -> None:
    assert content_hash("one") == content_hash("one")
    assert content_hash("one") != content_hash("two")


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
