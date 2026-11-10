from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from repo_index_mcp.http_server import make_handler
from repo_index_mcp.models import IndexResult, SearchResult


@dataclass
class HTTPResponse:
    status: int
    payload: object


class StubEngine:
    def __init__(self) -> None:
        self.reindexed: str | None = None
        self.search_kwargs: dict[str, object] | None = None

    def list_repos(self) -> list[dict[str, object]]:
        return [{"repo_path": "/tmp/repo", "is_stale": False}]

    def query(
        self,
        query: str,
        *,
        repo: str | None,
        path_prefix: str | None,
        language: str | None,
        k: int,
    ) -> list[SearchResult]:
        self.search_kwargs = {
            "query": query,
            "repo": repo,
            "path_prefix": path_prefix,
            "language": language,
            "k": k,
        }
        return [
            SearchResult(
                repo="repo-id",
                path="src/app.py",
                start_line=1,
                end_line=2,
                snippet="def hello():\n    return True\n",
                score=1.0,
                language="python",
                symbol_name="hello",
                symbol_kind="function",
                symbol_confidence="high",
            )
        ]

    def get_symbol(self, name: str, *, repo: str | None = None) -> SearchResult | None:
        if name == "missing":
            return None
        return SearchResult(
            repo=repo or "repo-id",
            path="src/app.py",
            start_line=1,
            end_line=2,
            snippet="def hello():\n    return True\n",
            score=1.0,
            language="python",
            symbol_name=name,
            symbol_kind="function",
            symbol_confidence="high",
        )

    def reindex(self, repo_path: str | None = None) -> IndexResult:
        self.reindexed = repo_path
        return IndexResult(
            repo_id="repo-id",
            repo_path=repo_path or "/tmp/repo",
            commit_sha="abc123",
            files_indexed=1,
            chunks_indexed=1,
            duration_ms=5,
        )


@pytest.fixture
def http_server() -> Iterator[tuple[str, StubEngine]]:
    engine = StubEngine()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(engine))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", engine
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request_json(
    base_url: str,
    method: str,
    path: str,
    payload: object | None = None,
) -> HTTPResponse:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=5) as response:
            return HTTPResponse(response.status, json.loads(response.read().decode("utf-8")))
    except HTTPError as exc:
        return HTTPResponse(exc.code, json.loads(exc.read().decode("utf-8")))


def test_health_and_repos(http_server: tuple[str, StubEngine]) -> None:
    base_url, _engine = http_server

    assert request_json(base_url, "GET", "/health") == HTTPResponse(200, {"ok": True})
    repos = request_json(base_url, "GET", "/repos")

    assert repos.status == 200
    assert repos.payload == [{"repo_path": "/tmp/repo", "is_stale": False}]


def test_search_endpoint_returns_results(http_server: tuple[str, StubEngine]) -> None:
    base_url, engine = http_server

    response = request_json(
        base_url,
        "POST",
        "/search",
        {"query": "hello", "repo": "repo-id", "language": "python", "k": 3},
    )

    assert response.status == 200
    assert isinstance(response.payload, list)
    assert response.payload[0]["path"] == "src/app.py"
    assert engine.search_kwargs == {
        "query": "hello",
        "repo": "repo-id",
        "path_prefix": None,
        "language": "python",
        "k": 3,
    }


def test_symbol_endpoint_uses_mcp_definition_shape(http_server: tuple[str, StubEngine]) -> None:
    base_url, _engine = http_server

    response = request_json(base_url, "POST", "/symbol", {"name": "hello", "repo": "repo-id"})
    missing = request_json(base_url, "POST", "/symbol", {"name": "missing"})

    assert response.status == 200
    assert response.payload["definition"].startswith("def hello")
    assert response.payload["symbol_name"] == "hello"
    assert missing == HTTPResponse(200, None)


def test_reindex_endpoint_returns_index_result(http_server: tuple[str, StubEngine]) -> None:
    base_url, engine = http_server

    response = request_json(base_url, "POST", "/reindex", {"repo_path": "/tmp/repo"})

    assert response.status == 200
    assert response.payload["repo_path"] == "/tmp/repo"
    assert engine.reindexed == "/tmp/repo"


def test_bad_request_and_unknown_route_return_json_errors(
    http_server: tuple[str, StubEngine],
) -> None:
    base_url, _engine = http_server

    bad_search = request_json(base_url, "POST", "/search", {"query": "hello", "k": 0})
    unknown = request_json(base_url, "GET", "/nope")

    assert bad_search == HTTPResponse(400, {"error": "k must be a positive integer"})
    assert unknown == HTTPResponse(404, {"error": "unknown endpoint"})


def test_cli_dispatches_serve_http(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_http_server(*, db_path: Path, host: str, port: int) -> None:
        calls.append({"db_path": db_path, "host": host, "port": port})

    import repo_index_mcp.http_server
    from repo_index_mcp.cli import main

    monkeypatch.setattr(repo_index_mcp.http_server, "run_http_server", fake_run_http_server)

    result = main(
        [
            "--db",
            str(tmp_path / "index.sqlite"),
            "serve-http",
            "--host",
            "127.0.0.1",
            "--port",
            "9876",
        ]
    )

    assert result == 0
    assert calls == [
        {"db_path": tmp_path / "index.sqlite", "host": "127.0.0.1", "port": 9876}
    ]
