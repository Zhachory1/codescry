from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from repo_index_mcp.engine import DEFAULT_DB_PATH, RepoIndex


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    engine = RepoIndex(db_path=args.db)

    if args.command == "index":
        result = engine.index_repo(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.command == "query":
        results = engine.query(
            args.query,
            repo=args.repo,
            path_prefix=args.path_prefix,
            language=args.language,
            k=args.k,
        )
        print(json.dumps([asdict(result) for result in results], indent=2))
        return 0

    if args.command == "status":
        print(json.dumps(engine.list_repos(), indent=2))
        return 0

    if args.command == "reindex":
        result = engine.reindex(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.command == "serve":
        from repo_index_mcp.mcp_server import run_server

        run_server(db_path=args.db)
        return 0

    parser.print_help()
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="repo-index")
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB_PATH),
        type=Path,
        help=f"SQLite index path (default: {DEFAULT_DB_PATH})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    index = subparsers.add_parser("index", help="index one git repo")
    index.add_argument("repo_path", type=Path)

    query = subparsers.add_parser("query", help="query indexed code")
    query.add_argument("query")
    add_query_args(query)

    status = subparsers.add_parser("status", help="list indexed repos")
    status.set_defaults(_status=True)

    reindex = subparsers.add_parser("reindex", help="reindex repo")
    reindex.add_argument("repo_path", nargs="?", type=Path)

    serve = subparsers.add_parser("serve", help="run MCP server over stdio")
    serve.set_defaults(_serve=True)

    return parser


def add_query_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-k", type=positive_int, default=10)
    parser.add_argument("--repo")
    parser.add_argument("--path-prefix")
    parser.add_argument("--language")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def result_to_dict(result: Any) -> dict[str, Any]:
    return asdict(result)


if __name__ == "__main__":
    raise SystemExit(main())
