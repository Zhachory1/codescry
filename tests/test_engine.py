from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

from repo_index_mcp.embeddings import HashEmbeddingProvider
from repo_index_mcp.engine import RepoIndex
from repo_index_mcp.models import Chunk
from repo_index_mcp.repo import content_hash, current_commit, repo_id_for
from repo_index_mcp.secrets import looks_like_secret
from repo_index_mcp.storage import SQLiteStorage


class CountingEmbeddingProvider:
    model_id = "counting-test"
    dimensions = 4

    def __init__(self) -> None:
        self.calls = 0

    def embed(self, text: str) -> list[float]:
        self.calls += 1
        return [1.0, 0.0, 0.0, 0.0] if text.strip() else [0.0, 0.0, 0.0, 0.0]


def test_query_embedding_cache_avoids_reembedding_and_hides_raw_query(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def retry_request():\n    return True\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")
    provider = CountingEmbeddingProvider()
    engine = RepoIndex(db_path=tmp_path / "index.sqlite", embedding_provider=provider)
    engine.index_repo(repo)

    calls_after_index = provider.calls
    engine.query("Where is retry_request?", k=1)
    calls_after_first_query = provider.calls
    engine.query("Where is retry_request?", k=1)

    assert calls_after_first_query == calls_after_index + 1
    assert provider.calls == calls_after_first_query
    with sqlite3.connect(tmp_path / "index.sqlite") as conn:
        cache_rows = conn.execute(
            "SELECT query_hash, embedding FROM query_embedding_cache"
        ).fetchall()
        meta_rows = conn.execute("SELECT key, value FROM storage_meta").fetchall()
    assert len(cache_rows) == 1
    assert "Where is retry_request?" not in str(cache_rows)
    assert "Where is retry_request?" not in str(meta_rows)


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
    assert index_result.files_changed == 1
    assert index_result.chunks_indexed >= 1
    assert index_result.chunks_total >= 1
    assert results[0].path == "app.py"
    assert "retry_request" in results[0].snippet


def test_index_repo_skips_unchanged_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    result = engine.index_repo(repo)

    assert result.files_indexed == 1
    assert result.files_changed == 0
    assert result.chunks_indexed == 0
    assert result.chunks_total == 1


def test_index_repo_reembeds_when_model_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    first = RepoIndex(
        db_path=tmp_path / "index.sqlite",
        embedding_provider=HashEmbeddingProvider(dimensions=16),
    )
    first.index_repo(repo)
    second = RepoIndex(
        db_path=tmp_path / "index.sqlite",
        embedding_provider=HashEmbeddingProvider(dimensions=32),
    )
    result = second.index_repo(repo)

    assert result.files_changed == 1
    assert result.chunks_indexed == 1


def test_index_repo_reembeds_when_chunker_version_changes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    first = RepoIndex(db_path=tmp_path / "index.sqlite")
    first.index_repo(repo)
    second = RepoIndex(db_path=tmp_path / "index.sqlite", chunker=VersionedChunker("custom-v2"))
    result = second.index_repo(repo)

    assert result.files_changed == 1
    assert result.chunks_indexed == 1


def test_index_repo_removes_deleted_file_chunks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    (repo / "old.py").write_text("print('old')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "old.py").unlink()
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    git_commit(repo, "delete old")

    result = engine.index_repo(repo)
    results = engine.query("old", k=5)

    assert result.files_removed == 1
    assert result.chunks_total == 1
    assert all(search_result.path != "old.py" for search_result in results)


def test_secret_detector_catches_high_confidence_patterns() -> None:
    assert looks_like_secret(pem_header("PRIVATE"))
    assert looks_like_secret(pem_header("ENCRYPTED PRIVATE"))
    assert looks_like_secret("aws_access_key_id = " + "AKIA" + "ABCDEFGHIJKLMNOP")
    assert looks_like_secret("token = " + github_token("ghp"))
    assert looks_like_secret("token = " + github_token("gho"))
    assert looks_like_secret("token = " + github_token("ghu"))
    assert looks_like_secret("token = " + github_token("ghs"))
    assert looks_like_secret("token = " + github_token("ghr"))


def test_index_repo_skips_secret_looking_file(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "secret.py").write_text(
        f"TOKEN = '{github_token('ghp')}'\n",
        encoding="utf-8",
    )
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    result = engine.index_repo(repo)
    results = engine.query("ghp abcdefghijklmnopqrstuvwxyz", k=5)

    assert result.files_skipped == 1
    assert result.chunks_total == 0
    assert results == []


def test_index_repo_secret_filter_upgrade_removes_existing_secret_same_commit(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.sqlite"
    repo.mkdir()
    secret_content = f"TOKEN = '{github_token('ghp')}'\n"
    (repo / "config.py").write_text(secret_content, encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")
    repo_id = repo_id_for(repo)
    model_id = HashEmbeddingProvider().model_id
    storage = SQLiteStorage(db_path)
    storage.record_repo_success(
        repo_id=repo_id,
        repo_path=str(repo),
        commit_sha=current_commit(repo),
        remote_url="",
    )
    storage.replace_file_chunks(
        repo_id=repo_id,
        path="config.py",
        content_hash=content_hash(secret_content),
        chunks=[
            Chunk(
                repo_id=repo_id,
                repo_path=str(repo),
                path="config.py",
                language="python",
                symbol_name=None,
                symbol_kind=None,
                symbol_line=None,
                symbol_confidence=None,
                start_line=1,
                end_line=1,
                content=secret_content,
            )
        ],
        embeddings=[HashEmbeddingProvider().embed(secret_content)],
        commit_sha=current_commit(repo),
        embedding_model=model_id,
        chunker_version="symbol-line-v1:max=80:overlap=10",
    )

    engine = RepoIndex(db_path=db_path)
    result = engine.index_repo(repo)
    results = engine.query("ghp abcdefghijklmnopqrstuvwxyz", k=5)

    assert result.files_skipped == 1
    assert result.files_removed == 1
    assert result.chunks_total == 0
    assert results == []


def test_index_repo_removes_chunks_when_file_becomes_secret(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text("TOKEN = 'safe placeholder'\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "config.py").write_text(
        f"TOKEN = '{github_token('ghp')}'\n",
        encoding="utf-8",
    )
    commit_all(repo, "secret")
    result = engine.index_repo(repo)
    results = engine.query("safe placeholder", k=5)

    assert result.files_skipped == 1
    assert result.files_removed == 1
    assert result.chunks_total == 0
    assert results == []


def test_index_repo_skips_gitlinks(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    subprocess.run(
        [
            "git",
            "update-index",
            "--add",
            "--cacheinfo",
            "160000,e69de29bb2d1d6434b8b29ae775ad8c2e48c5391,submodule",
        ],
        cwd=repo,
        check=True,
    )
    git_commit(repo, "add gitlink")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    result = engine.index_repo(repo)

    assert result.error_count == 0
    assert result.files_indexed == 0


def test_incremental_reindex_removes_file_that_becomes_large(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('small')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "app.py").write_text("x" * 1_000_001, encoding="utf-8")
    commit_all(repo, "large")
    result = engine.index_repo(repo)
    results = engine.query("small", k=5)

    assert result.files_removed == 1
    assert result.chunks_total == 0
    assert results == []


def test_incremental_reindex_removes_file_that_becomes_binary(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('small')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "app.py").write_bytes(b"\0binary")
    commit_all(repo, "binary")
    result = engine.index_repo(repo)
    results = engine.query("small", k=5)

    assert result.files_removed == 1
    assert result.chunks_total == 0
    assert results == []


def test_index_repo_skips_large_committed_blobs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "large.py").write_text("x" * 1_000_001, encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    result = engine.index_repo(repo)

    assert result.error_count == 0
    assert result.files_indexed == 0
    assert result.chunks_total == 0


def test_index_root_continues_past_unborn_repo(tmp_path: Path) -> None:
    good = tmp_path / "good"
    unborn = tmp_path / "unborn"
    good.mkdir()
    unborn.mkdir()
    (good / "app.py").write_text("def good_service(): pass\n", encoding="utf-8")
    init_repo(good)
    init_repo(unborn)
    commit_all(good, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    results = engine.index_root(tmp_path)

    assert len(results) == 2
    assert sorted(result.error_count for result in results) == [0, 1]
    assert any(result.files_indexed == 1 for result in results)


def test_index_root_discovers_multiple_repos(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "nested" / "second"
    first.mkdir()
    second.mkdir(parents=True)
    (first / "app.py").write_text("def first_service(): pass\n", encoding="utf-8")
    (second / "app.py").write_text("def second_service(): pass\n", encoding="utf-8")
    init_repo(first)
    init_repo(second)
    commit_all(first, "init")
    commit_all(second, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    results = engine.index_root(tmp_path)
    repos = engine.list_repos()

    assert len(results) == 2
    assert len(repos) == 2
    assert {Path(str(repo["repo_path"])).name for repo in repos} == {"first", "second"}


def test_index_repo_cleans_legacy_repo_id_rows_for_same_path(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.sqlite"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")
    legacy_id = "https://github.com/example/repo.git"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE repos (
                repo_id TEXT PRIMARY KEY,
                repo_path TEXT NOT NULL,
                last_commit_sha TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE chunks (
                chunk_id TEXT PRIMARY KEY,
                repo_id TEXT NOT NULL,
                repo_path TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT NOT NULL,
                symbol_name TEXT,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                commit_sha TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT NOT NULL,
                embedding_model TEXT NOT NULL,
                indexed_at TEXT NOT NULL
            );
            CREATE TABLE indexed_files (
                repo_id TEXT NOT NULL,
                path TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                commit_sha TEXT NOT NULL,
                indexed_at TEXT NOT NULL,
                chunk_count INTEGER NOT NULL,
                embedding_model TEXT NOT NULL,
                PRIMARY KEY(repo_id, path)
            );
            """
        )
        conn.execute(
            """
            INSERT INTO repos(repo_id, repo_path, last_commit_sha, indexed_at)
            VALUES (?, ?, ?, ?)
            """,
            (legacy_id, str(repo), "old", "now"),
        )
        conn.execute(
            """
            INSERT INTO chunks(
                chunk_id, repo_id, repo_path, path, language, symbol_name, start_line, end_line,
                commit_sha, content, embedding, embedding_model, indexed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "old-chunk",
                legacy_id,
                str(repo),
                "old.py",
                "python",
                None,
                1,
                1,
                "old",
                "print('old')",
                "[]",
                "hash-v1:dims=256",
                "now",
            ),
        )

    engine = RepoIndex(db_path=db_path)
    result = engine.index_repo(repo)
    repos = engine.list_repos()
    old_results = engine.query("old", k=5)
    current_results = engine.query("one", k=1)

    assert result.error_count == 0
    assert result.chunks_indexed == 1
    assert [item["repo_id"] for item in repos] == [str(repo)]
    assert all(search_result.path != "old.py" for search_result in old_results)
    assert current_results[0].path == "app.py"


def test_remote_url_in_status_strips_credentials(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://ghp_secret@github.com/acme/repo.git"],
        cwd=repo,
        check=True,
    )
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)

    assert engine.list_repos()[0]["remote_url"] == "https://github.com/acme/repo.git"


def test_scp_like_remote_url_in_status_strips_non_git_user(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    subprocess.run(
        ["git", "remote", "add", "origin", "TOKEN@gitlab.com:group/repo.git"],
        cwd=repo,
        check=True,
    )
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)

    assert engine.list_repos()[0]["remote_url"] == "gitlab.com:group/repo.git"


def test_status_marks_dirty_tracked_files(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    (repo / "app.py").write_text("print('dirty')\n", encoding="utf-8")

    repos = engine.list_repos()
    results = engine.query("one", k=1)

    assert repos[0]["is_stale"] is False
    assert repos[0]["has_dirty_tracked_files"] is True
    assert results[0].has_dirty_tracked_files is True
    assert "dirty" not in results[0].snippet


def test_index_repo_failure_keeps_repo_stale(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("print('one')\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite", chunker=FailingChunker())
    result = engine.index_repo(repo)
    repos = engine.list_repos()

    assert result.error_count == 1
    assert result.last_error is not None
    assert repos[0]["is_stale"] is True
    assert repos[0]["error_count"] == 1


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


class VersionedChunker:
    def __init__(self, version: str) -> None:
        self.version = version

    def chunk_file(self, **kwargs):  # type: ignore[no-untyped-def]
        from repo_index_mcp.chunking import LineChunker

        return LineChunker().chunk_file(**kwargs)


class FailingChunker:
    version = "failing-v1"

    def chunk_file(self, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("chunk failed")


def github_token(prefix: str) -> str:
    return prefix + "_" + "abcdefghijklmnopqrstuvwxyz"


def pem_header(kind: str) -> str:
    return "-----BEGIN " + kind + " KEY-----\nsecret\n-----END " + kind + " KEY-----"


def init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)


def commit_all(repo: Path, message: str) -> None:
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    git_commit(repo, message)


def git_commit(repo: Path, message: str) -> None:
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
