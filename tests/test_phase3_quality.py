import subprocess
from pathlib import Path

import pytest

from repo_index_mcp.chunking import LineChunker, find_regex_symbols
from repo_index_mcp.engine import RepoIndex
from repo_index_mcp.models import Chunk, SearchResult
from repo_index_mcp.storage import (
    SQLiteStorage,
    diversify_results,
    hybrid_score,
    path_rank_multiplier,
    search_sort_key,
)


def test_python_ast_function_chunk_has_parser_symbol() -> None:
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="def retry_request():\n    return True\n",
    )

    assert chunks[0].symbol_name == "retry_request"
    assert chunks[0].symbol_kind == "function"
    assert chunks[0].symbol_confidence == "parser"


def test_python_ast_method_chunk_has_parser_symbol() -> None:
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="class Service:\n    def handle_request(self):\n        return True\n",
    )

    parser_symbols = [chunk for chunk in chunks if chunk.symbol_confidence == "parser"]

    assert [chunk.symbol_name for chunk in parser_symbols] == ["Service", "handle_request"]
    assert parser_symbols[1].symbol_kind == "function"


def test_python_ast_class_chunk_has_parser_symbol() -> None:
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="class RetryPolicy:\n    pass\n",
    )

    assert chunks[0].symbol_name == "RetryPolicy"
    assert chunks[0].symbol_kind == "class"
    assert chunks[0].symbol_confidence == "parser"


def test_python_symbol_chunking_keeps_module_level_window() -> None:
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="CONSTANT = 1\n\ndef handle():\n    return CONSTANT\n",
    )

    assert any("CONSTANT = 1" in chunk.content for chunk in chunks)
    assert any(chunk.symbol_name == "handle" for chunk in chunks)


def test_decorated_python_function_includes_decorator() -> None:
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="@app.route('/retry')\ndef retry_request():\n    return True\n",
    )

    parser_chunk = next(chunk for chunk in chunks if chunk.symbol_confidence == "parser")
    assert parser_chunk.start_line == 1
    assert parser_chunk.symbol_line == 2
    assert "@app.route" in parser_chunk.content


def test_regex_symbols_ignore_comments_and_strings() -> None:
    symbols = find_regex_symbols(
        [
            "# def fake_comment(): pass",
            "'function fakeString()'",
            "function realHandler() {",
        ]
    )

    assert [symbol.name for symbol in symbols] == ["realHandler"]
    assert symbols[0].kind == "function"


def test_parser_symbol_chunks_include_multiple_javascript_declarations() -> None:
    pytest.importorskip("tree_sitter_language_pack")
    chunks = LineChunker().chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.js",
        content="function first() {}\nfunction second() {}\n",
    )

    parser_symbols = [chunk.symbol_name for chunk in chunks if chunk.symbol_confidence == "parser"]
    assert "first" in parser_symbols
    assert "second" in parser_symbols


def test_hybrid_score_lifts_exact_identifier_match() -> None:
    base = hybrid_score(
        query_text="retry_request",
        vector_score=0.0,
        path="app.py",
        content="unrelated",
        symbol_name=None,
    )
    boosted = hybrid_score(
        query_text="retry_request",
        vector_score=0.0,
        path="app.py",
        content="def retry_request(): pass",
        symbol_name="retry_request",
    )

    assert boosted > base


def test_hybrid_score_lifts_path_match() -> None:
    base = hybrid_score(
        query_text="storage provider",
        vector_score=0.0,
        path="src/app.py",
        content="unrelated",
        symbol_name=None,
    )
    boosted = hybrid_score(
        query_text="storage provider",
        vector_score=0.0,
        path="src/storage_provider.py",
        content="unrelated",
        symbol_name=None,
    )

    assert boosted > base


def test_search_sort_key_prefers_parser_shorter_path_and_earlier_line() -> None:
    regex = SearchResult("repo", "very/long/path.py", 10, 10, "", 1.0, "python", "x")
    parser = SearchResult(
        "repo",
        "x.py",
        1,
        1,
        "",
        1.0,
        "python",
        "x",
        symbol_confidence="parser",
    )

    assert search_sort_key(parser) > search_sort_key(regex)


def test_docs_paths_downrank_for_implementation_queries() -> None:
    assert path_rank_multiplier("where is retry implemented", "docs/phase-3-plan.md") < 1.0
    assert path_rank_multiplier("readme install mcp config", "docs/mcp-clients.md") == 1.0


def test_diversify_results_limits_per_file() -> None:
    results = [
        SearchResult("repo", "a.py", 1, 1, "", 1.0, "python"),
        SearchResult("repo", "a.py", 2, 2, "", 0.9, "python"),
        SearchResult("repo", "a.py", 3, 3, "", 0.8, "python"),
        SearchResult("repo", "b.py", 1, 1, "", 0.7, "python"),
    ]

    diversified = diversify_results(results, k=3, max_per_file=2)

    assert [result.path for result in diversified] == ["a.py", "a.py", "b.py"]


def test_get_symbol_uses_symbol_metadata(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "def retry_request():\n    return True\n\ndef other():\n    return False\n",
        encoding="utf-8",
    )
    init_repo(repo)
    commit_all(repo, "init")

    engine = RepoIndex(db_path=tmp_path / "index.sqlite")
    engine.index_repo(repo)
    result = engine.get_symbol("retry_request")

    assert result is not None
    assert result.path == "app.py"
    assert result.symbol_name == "retry_request"
    assert result.symbol_confidence == "parser"
    assert "def retry_request" in result.snippet


def test_storage_migrates_symbol_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "index.sqlite"
    storage = SQLiteStorage(db_path)

    with storage._connect() as conn:  # noqa: SLF001
        columns = {row[1] for row in conn.execute("PRAGMA table_info(chunks)")}

    assert {"symbol_kind", "symbol_line", "symbol_confidence"} <= columns


def test_storage_symbol_lookup_prefers_parser_exact_match(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "index.sqlite")
    parser_chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="parser.py",
        language="python",
        symbol_name="target",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="parser",
        start_line=1,
        end_line=1,
        content="def target(): pass",
    )
    regex_chunk = Chunk(
        repo_id="repo",
        repo_path="/repo",
        path="regex.js",
        language="javascript",
        symbol_name="target",
        symbol_kind="function",
        symbol_line=1,
        symbol_confidence="regex",
        start_line=1,
        end_line=1,
        content="function target() {}",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="parser.py",
        content_hash="one",
        chunks=[parser_chunk],
        embeddings=[[0.0]],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )
    storage.replace_file_chunks(
        repo_id="repo",
        path="regex.js",
        content_hash="two",
        chunks=[regex_chunk],
        embeddings=[[0.0]],
        commit_sha="commit",
        embedding_model="model",
        chunker_version="chunker",
    )

    result = storage.find_symbol(name="target", embedding_model="model")

    assert result is not None
    assert result.path == "parser.py"


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
