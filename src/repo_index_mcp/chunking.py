from __future__ import annotations

from collections.abc import Iterable

from repo_index_mcp.models import Chunk

LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".cs": "csharp",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".md": "markdown",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".sql": "sql",
}


def detect_language(path: str) -> str:
    for suffix, language in LANGUAGE_BY_SUFFIX.items():
        if path.endswith(suffix):
            return language
    return "text"


class LineChunker:
    def __init__(self, max_lines: int = 80, overlap_lines: int = 10) -> None:
        if max_lines <= 0:
            raise ValueError("max_lines must be positive")
        if overlap_lines < 0 or overlap_lines >= max_lines:
            raise ValueError("overlap_lines must be >= 0 and < max_lines")
        self.max_lines = max_lines
        self.overlap_lines = overlap_lines

    def chunk_file(
        self,
        *,
        repo_id: str,
        repo_path: str,
        path: str,
        content: str,
    ) -> list[Chunk]:
        lines = content.splitlines()
        if not lines:
            return []

        chunks: list[Chunk] = []
        step = self.max_lines - self.overlap_lines
        start = 0
        language = detect_language(path)

        while start < len(lines):
            end = min(start + self.max_lines, len(lines))
            chunk_lines = lines[start:end]
            chunks.append(
                Chunk(
                    repo_id=repo_id,
                    repo_path=repo_path,
                    path=path,
                    language=language,
                    symbol_name=None,
                    start_line=start + 1,
                    end_line=end,
                    content="\n".join(chunk_lines),
                )
            )
            if end == len(lines):
                break
            start += step

        return chunks

    def chunk_files(
        self,
        *,
        repo_id: str,
        repo_path: str,
        files: Iterable[tuple[str, str]],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for path, content in files:
            chunks.extend(
                self.chunk_file(repo_id=repo_id, repo_path=repo_path, path=path, content=content)
            )
        return chunks
