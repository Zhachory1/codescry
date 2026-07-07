from repo_index_mcp.chunking import LineChunker, detect_language


def test_detect_language() -> None:
    assert detect_language("src/app.py") == "python"
    assert detect_language("README.md") == "markdown"
    assert detect_language("unknown.xyz") == "text"


def test_python_parse_failure_falls_back_to_line_chunks() -> None:
    chunker = LineChunker(max_lines=3, overlap_lines=1)
    chunks = chunker.chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="bad.py",
        content="def broken(:\n    pass\nprint('still index me')\n",
    )

    assert chunks
    assert chunks[0].path == "bad.py"
    assert "still index me" in chunks[-1].content


def test_tree_sitter_parse_failure_falls_back_to_line_chunks(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def fail_parse_symbols(**_kwargs):  # type: ignore[no-untyped-def]
        raise ValueError("parser exploded")

    monkeypatch.setattr("repo_index_mcp.chunking.parse_symbols", fail_parse_symbols)
    chunker = LineChunker(max_lines=3, overlap_lines=1)

    chunks = chunker.chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.ts",
        content="export const value = 1;\nconsole.log(value);\n",
    )

    assert chunks
    assert chunks[0].path == "app.ts"
    assert "console.log" in chunks[0].content


def test_line_chunker_overlaps_lines() -> None:
    chunker = LineChunker(max_lines=3, overlap_lines=1)
    chunks = chunker.chunk_file(
        repo_id="repo",
        repo_path="/repo",
        path="app.py",
        content="\n".join(["one", "two", "three", "four", "five"]),
    )

    assert [(chunk.start_line, chunk.end_line, chunk.content) for chunk in chunks] == [
        (1, 3, "one\ntwo\nthree"),
        (3, 5, "three\nfour\nfive"),
    ]
