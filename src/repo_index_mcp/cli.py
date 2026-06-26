from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from repo_index_mcp.engine import DEFAULT_DB_PATH, RepoIndex
from repo_index_mcp.eval import format_report, load_golden_cases, run_recall_eval
from repo_index_mcp.hooks import install_hooks
from repo_index_mcp.repo import discover_repos


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "index":
        engine = RepoIndex(db_path=args.db)
        result = engine.index_repo(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 1 if result.error_count else 0

    if args.command == "index-root":
        engine = RepoIndex(db_path=args.db)
        results = engine.index_root(args.root_path)
        print(json.dumps([asdict(result) for result in results], indent=2))
        return 1 if any(result.error_count for result in results) else 0

    if args.command == "query":
        engine = RepoIndex(db_path=args.db)
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
        engine = RepoIndex(db_path=args.db)
        print(json.dumps(engine.list_repos(), indent=2))
        return 0

    if args.command == "reindex":
        engine = RepoIndex(db_path=args.db)
        result = engine.reindex(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 1 if result.error_count else 0

    if args.command == "install-hooks":
        repo_paths = discover_repos(args.path) if args.recursive else [args.path]
        installed = []
        for repo_path in repo_paths:
            installed.extend(
                install_hooks(
                    repo_path,
                    command=args.command_name,
                    db_path=args.db,
                    force=args.force,
                )
            )
        print(json.dumps([str(path) for path in installed], indent=2))
        return 0

    if args.command == "eval":
        engine = RepoIndex(db_path=args.db)
        index_result = engine.index_repo(args.repo_path)
        if index_result.error_count:
            print(json.dumps(asdict(index_result), indent=2))
            return 1
        report = run_recall_eval(engine, load_golden_cases(args.golden_file), k=args.k)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(format_report(report))
        return 1 if args.fail_under is not None and report.recall_at_k < args.fail_under else 0

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

    index_root = subparsers.add_parser("index-root", help="discover and index git repos under root")
    index_root.add_argument("root_path", type=Path)

    query = subparsers.add_parser("query", help="query indexed code")
    query.add_argument("query")
    add_query_args(query)

    status = subparsers.add_parser("status", help="list indexed repos")
    status.set_defaults(_status=True)

    reindex = subparsers.add_parser("reindex", help="reindex repo")
    reindex.add_argument("repo_path", nargs="?", type=Path)

    hooks = subparsers.add_parser("install-hooks", help="install freshness git hooks")
    hooks.add_argument("path", type=Path)
    hooks.add_argument("--recursive", action="store_true")
    hooks.add_argument("--force", action="store_true")
    hooks.add_argument("--command-name", default="repo-index")

    eval_parser = subparsers.add_parser("eval", help="run Recall@K over a golden JSONL set")
    eval_parser.add_argument("golden_file", type=Path)
    eval_parser.add_argument("repo_path", type=Path)
    eval_parser.add_argument("-k", type=positive_int, default=10)
    eval_parser.add_argument("--fail-under", type=float)
    eval_parser.add_argument("--json", action="store_true")

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
