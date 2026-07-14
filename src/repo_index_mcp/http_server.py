from __future__ import annotations

import json
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from repo_index_mcp.engine import DEFAULT_DB_PATH, RepoIndex
from repo_index_mcp.models import SearchResult
from repo_index_mcp.usage import log_search_event


def run_http_server(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    engine = RepoIndex(db_path=db_path)
    server = ThreadingHTTPServer((host, port), make_handler(engine))
    server.serve_forever()


def make_handler(engine: RepoIndex) -> type[BaseHTTPRequestHandler]:
    class CodeScryHTTPHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlparse(self.path).path
            if route == "/health":
                self.write_json({"ok": True})
                return
            if route == "/repos":
                self.write_json(engine.list_repos())
                return
            self.write_error(HTTPStatus.NOT_FOUND, "unknown endpoint")

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            try:
                if route == "/search":
                    self.write_json(handle_search(engine, self.read_json()))
                    return
                if route == "/symbol":
                    self.write_json(handle_symbol(engine, self.read_json()))
                    return
                if route == "/reindex":
                    self.write_json(asdict(engine.reindex(self.read_json().get("repo_path"))))
                    return
                self.write_error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            except ValueError as exc:
                self.write_error(HTTPStatus.BAD_REQUEST, str(exc))
            except Exception as exc:
                self.write_error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

        def read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            if length == 0:
                return {}
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError("invalid JSON body") from exc
            if not isinstance(payload, dict):
                raise ValueError("JSON body must be an object")
            return payload

        def write_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def write_error(self, status: HTTPStatus, message: str) -> None:
            self.write_json({"error": message}, status)

        def log_message(self, format: str, *args: Any) -> None:
            return

    return CodeScryHTTPHandler


def handle_search(engine: RepoIndex, payload: dict[str, Any]) -> list[dict[str, Any]]:
    query = require_string(payload, "query")
    repo = optional_string(payload, "repo")
    path_prefix = optional_string(payload, "path_prefix")
    language = optional_string(payload, "language")
    k = positive_int(payload.get("k", 10), "k")
    start = time.monotonic()
    results = engine.query(
        query,
        repo=repo,
        path_prefix=path_prefix,
        language=language,
        k=k,
    )
    log_search_event(
        tool="search_code",
        query=query,
        source="http",
        latency_ms=int((time.monotonic() - start) * 1000),
        results=results,
        repo=repo,
        path_prefix=path_prefix,
        language=language,
        k=k,
    )
    return [asdict(result) for result in results]


def handle_symbol(engine: RepoIndex, payload: dict[str, Any]) -> dict[str, Any] | None:
    name = require_string(payload, "name")
    repo = optional_string(payload, "repo")
    start = time.monotonic()
    result = engine.get_symbol(name, repo=repo)
    results = [] if result is None else [result]
    log_search_event(
        tool="get_symbol",
        query=name,
        source="http",
        latency_ms=int((time.monotonic() - start) * 1000),
        results=results,
        repo=repo,
        k=1,
    )
    return symbol_payload(result) if result is not None else None


def symbol_payload(result: SearchResult) -> dict[str, Any]:
    return {
        "repo": result.repo,
        "path": result.path,
        "start_line": result.start_line,
        "end_line": result.end_line,
        "definition": result.snippet,
        "score": result.score,
        "symbol_name": result.symbol_name,
        "symbol_kind": result.symbol_kind,
        "symbol_confidence": result.symbol_confidence,
        "is_stale": result.is_stale,
        "has_dirty_tracked_files": result.has_dirty_tracked_files,
    }


def require_string(payload: dict[str, Any], name: str) -> str:
    value = payload.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value


def optional_string(payload: dict[str, Any], name: str) -> str | None:
    value = payload.get(name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    return value


def positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return parsed
