from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from dataclasses import dataclass

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

SYMBOL_RE = re.compile(
    r"^\s*(?:"
    r"def\s+(?P<pydef>[A-Za-z_][A-Za-z0-9_]*)\s*\(|"
    r"class\s+(?P<class>[A-Za-z_][A-Za-z0-9_]*)\b|"
    r"function\s+(?P<function>[A-Za-z_][A-Za-z0-9_]*)\s*\(|"
    r"func\s+(?P<func>[A-Za-z_][A-Za-z0-9_]*)\s*\(|"
    r"type\s+(?P<type>[A-Za-z_][A-Za-z0-9_]*)\b|"
    r"interface\s+(?P<interface>[A-Za-z_][A-Za-z0-9_]*)\b"
    r")"
)


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    line: int
    confidence: str


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

    @property
    def version(self) -> str:
        return f"symbol-line-v1:max={self.max_lines}:overlap={self.overlap_lines}"

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

        language = detect_language(path)
        if language == "python":
            chunks = self._chunk_python_symbols(
                repo_id=repo_id,
                repo_path=repo_path,
                path=path,
                language=language,
                lines=lines,
                content=content,
            )
            if chunks:
                regex_symbols = find_regex_symbols(lines)
                chunks.extend(
                    self._chunk_line_windows(
                        repo_id=repo_id,
                        repo_path=repo_path,
                        path=path,
                        language=language,
                        lines=lines,
                        symbols=regex_symbols,
                    )
                )
                chunks.extend(
                    self._chunk_regex_symbols(
                        repo_id=repo_id,
                        repo_path=repo_path,
                        path=path,
                        language=language,
                        lines=lines,
                        symbols=regex_symbols,
                    )
                )
                return dedupe_chunks(chunks)

        regex_symbols = find_regex_symbols(lines)
        chunks = self._chunk_line_windows(
            repo_id=repo_id,
            repo_path=repo_path,
            path=path,
            language=language,
            lines=lines,
            symbols=regex_symbols,
        )
        chunks.extend(
            self._chunk_regex_symbols(
                repo_id=repo_id,
                repo_path=repo_path,
                path=path,
                language=language,
                lines=lines,
                symbols=regex_symbols,
            )
        )
        return dedupe_chunks(chunks)

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

    def _chunk_python_symbols(
        self,
        *,
        repo_id: str,
        repo_path: str,
        path: str,
        language: str,
        lines: list[str],
        content: str,
    ) -> list[Chunk]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        chunks: list[Chunk] = []
        for node in python_symbol_nodes(tree):
            end_line = getattr(node, "end_lineno", node.lineno)
            if end_line - node.lineno + 1 > self.max_lines:
                continue
            kind = python_symbol_kind(node)
            start_line = python_symbol_start_line(node)
            chunks.append(
                Chunk(
                    repo_id=repo_id,
                    repo_path=repo_path,
                    path=path,
                    language=language,
                    symbol_name=node.name,
                    symbol_kind=kind,
                    symbol_line=node.lineno,
                    symbol_confidence="parser",
                    start_line=start_line,
                    end_line=end_line,
                    content="\n".join(lines[start_line - 1 : end_line]),
                )
            )
        return chunks

    def _chunk_line_windows(
        self,
        *,
        repo_id: str,
        repo_path: str,
        path: str,
        language: str,
        lines: list[str],
        symbols: list[Symbol],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        step = self.max_lines - self.overlap_lines
        start = 0

        while start < len(lines):
            end = min(start + self.max_lines, len(lines))
            chunk_lines = lines[start:end]
            symbol = nearest_symbol(symbols, start + 1, end)
            chunks.append(
                Chunk(
                    repo_id=repo_id,
                    repo_path=repo_path,
                    path=path,
                    language=language,
                    symbol_name=None if symbol is None else symbol.name,
                    symbol_kind=None if symbol is None else symbol.kind,
                    symbol_line=None if symbol is None else symbol.line,
                    symbol_confidence=None if symbol is None else symbol.confidence,
                    start_line=start + 1,
                    end_line=end,
                    content="\n".join(chunk_lines),
                )
            )
            if end == len(lines):
                break
            start += step

        return chunks

    def _chunk_regex_symbols(
        self,
        *,
        repo_id: str,
        repo_path: str,
        path: str,
        language: str,
        lines: list[str],
        symbols: list[Symbol],
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        for symbol in symbols:
            end_line = min(symbol.line + self.max_lines - 1, len(lines))
            chunks.append(
                Chunk(
                    repo_id=repo_id,
                    repo_path=repo_path,
                    path=path,
                    language=language,
                    symbol_name=symbol.name,
                    symbol_kind=symbol.kind,
                    symbol_line=symbol.line,
                    symbol_confidence=symbol.confidence,
                    start_line=symbol.line,
                    end_line=end_line,
                    content="\n".join(lines[symbol.line - 1 : end_line]),
                )
            )
        return chunks


def python_symbol_nodes(
    tree: ast.Module,
) -> list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    nodes: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            nodes.append(node)
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    nodes.append(child)
    nodes.sort(key=lambda item: item.lineno)
    return nodes


def python_symbol_kind(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    if isinstance(node, ast.ClassDef):
        return "class"
    return "function"


def python_symbol_start_line(node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    decorator_lines = [decorator.lineno for decorator in getattr(node, "decorator_list", [])]
    return min([node.lineno, *decorator_lines]) if decorator_lines else node.lineno


def dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
    deduped: dict[tuple[str, int, int, str], Chunk] = {}
    for chunk in chunks:
        key = (chunk.path, chunk.start_line, chunk.end_line, chunk.content)
        existing = deduped.get(key)
        if existing is None or symbol_priority(chunk) > symbol_priority(existing):
            deduped[key] = chunk
    return list(deduped.values())


def symbol_priority(chunk: Chunk) -> int:
    if chunk.symbol_confidence == "parser":
        return 2
    if chunk.symbol_confidence == "regex":
        return 1
    return 0


def find_regex_symbols(lines: list[str]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for line_number, line in enumerate(lines, start=1):
        clean = strip_comments_and_strings(line)
        match = SYMBOL_RE.match(clean)
        if match is None:
            continue
        name = next(value for value in match.groupdict().values() if value)
        kind = regex_symbol_kind(match.lastgroup)
        symbols.append(Symbol(name=name, kind=kind, line=line_number, confidence="regex"))
    return symbols


def regex_symbol_kind(group_name: str | None) -> str:
    if group_name in {"pydef", "function", "func"}:
        return "function"
    if group_name == "class":
        return "class"
    return group_name or "symbol"


def nearest_symbol(symbols: list[Symbol], start_line: int, end_line: int) -> Symbol | None:
    inside = [symbol for symbol in symbols if start_line <= symbol.line <= end_line]
    if inside:
        return inside[0]
    prior = [symbol for symbol in symbols if symbol.line <= start_line]
    return prior[-1] if prior else None


def strip_comments_and_strings(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith(('#', '//', '*')):
        return ""
    quote_positions = [position for quote in ('"', "'") if (position := line.find(quote)) >= 0]
    comment_positions = [position for marker in ('#', '//') if (position := line.find(marker)) >= 0]
    cut_positions = quote_positions + comment_positions
    if not cut_positions:
        return line
    return line[: min(cut_positions)]
