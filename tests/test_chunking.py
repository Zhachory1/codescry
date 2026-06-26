from repo_index_mcp.chunking import LineChunker, detect_language


def test_detect_language() -> None:
    assert detect_language("src/app.py") == "python"
    assert detect_language("README.md") == "markdown"
    assert detect_language("unknown.xyz") == "text"


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
