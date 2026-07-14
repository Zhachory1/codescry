from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from repo_index_mcp.doctor import run_doctor
from repo_index_mcp.engine import DEFAULT_DB_PATH, RepoIndex
from repo_index_mcp.eval import (
    format_report,
    load_golden_cases,
    run_recall_diagnostics,
    run_recall_eval,
)
from repo_index_mcp.hooks import install_hooks
from repo_index_mcp.repo import discover_repos, discover_repos_with_skipped
from repo_index_mcp.secrets import looks_like_secret
from repo_index_mcp.storage import rrf_ranking_enabled
from repo_index_mcp.usage import (
    build_report,
    end_task,
    log_search_event,
    record_activation,
    record_miss,
    record_retention,
    start_task,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "index":
        engine = RepoIndex(db_path=args.db)
        result = engine.index_repo(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 1 if result.error_count else 0

    if args.command == "index-root":
        return handle_index_root(args)

    if args.command == "query":
        engine = RepoIndex(db_path=args.db)
        start = time.monotonic()
        results = engine.query(
            args.query,
            repo=args.repo,
            path_prefix=args.path_prefix,
            language=args.language,
            k=args.k,
        )
        latency_ms = int((time.monotonic() - start) * 1000)
        log_search_event(
            tool="query",
            query=args.query,
            source="cli",
            latency_ms=latency_ms,
            results=results,
            repo=args.repo,
            path_prefix=args.path_prefix,
            language=args.language,
            k=args.k,
        )
        print(json.dumps([asdict(result) for result in results], indent=2))
        if not results:
            print(
                "No results. Run `codescry status`; if no repos are indexed, run "
                "`codescry index /path/to/repo` or `codescry index-root ~/code`.",
                file=sys.stderr,
            )
        return 0

    if args.command == "get-symbol":
        engine = RepoIndex(db_path=args.db)
        start = time.monotonic()
        result = engine.get_symbol(args.name, repo=args.repo)
        latency_ms = int((time.monotonic() - start) * 1000)
        if result is None:
            print("null")
            print(
                "No symbol found. Try `codescry query <name>` or reindex the repo.",
                file=sys.stderr,
            )
            log_search_event(
                tool="get-symbol",
                query=args.name,
                source="cli",
                latency_ms=latency_ms,
                results=[],
                repo=args.repo,
                k=1,
            )
            return 1
        log_search_event(
            tool="get-symbol",
            query=args.name,
            source="cli",
            latency_ms=latency_ms,
            results=[result],
            repo=args.repo,
            k=1,
        )
        print(json.dumps(asdict(result), indent=2))
        return 0

    if args.command == "status":
        engine = RepoIndex(db_path=args.db)
        print(json.dumps(engine.list_repos(), indent=2))
        return 0

    if args.command == "backfill-vectors":
        engine = RepoIndex(db_path=args.db)
        start = time.monotonic()
        count = engine.storage.backfill_vectors()
        print(
            json.dumps(
                {
                    "vectors_indexed": count,
                    "duration_ms": int((time.monotonic() - start) * 1000),
                },
                indent=2,
            )
        )
        return 0

    if args.command == "doctor":
        result, exit_code = run_doctor(args.db)
        print(json.dumps(result, indent=2))
        return exit_code

    if args.command == "reindex":
        engine = RepoIndex(db_path=args.db)
        result = engine.reindex(args.repo_path)
        print(json.dumps(asdict(result), indent=2))
        return 1 if result.error_count else 0

    if args.command == "prune":
        engine = RepoIndex(db_path=args.db)
        removed = engine.storage.prune_missing_repos()
        print(json.dumps({"removed": removed, "removed_count": len(removed)}, indent=2))
        return 0

    if args.command == "remove-repo":
        engine = RepoIndex(db_path=args.db)
        removed = engine.storage.remove_repo(str(args.repo))
        print(json.dumps({"removed": removed, "removed_count": int(removed is not None)}, indent=2))
        return 0 if removed is not None else 1

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

    if args.command == "pilot":
        return handle_pilot(args)

    if args.command == "eval-add":
        append_eval_case(args)
        return 0

    if args.command == "eval":
        engine = RepoIndex(db_path=args.db)
        index_result = engine.index_repo(args.repo_path)
        if index_result.error_count:
            print(json.dumps(asdict(index_result), indent=2))
            return 1
        if rrf_ranking_enabled():
            engine.storage.backfill_vectors()
        cases = load_golden_cases(args.golden_file)
        if args.debug:
            diagnostics = run_recall_diagnostics(
                engine,
                cases,
                k=args.k,
                repo=index_result.repo_id,
            )
            print(json.dumps(diagnostics, indent=2))
            if rrf_ranking_enabled() and diagnostics.get("rrf_active_count", 0) == 0:
                return 1
            if args.fail_under is not None and diagnostics["recall_at_k"] < args.fail_under:
                return 1
            return 0
        report = run_recall_eval(engine, cases, k=args.k, repo=index_result.repo_id)
        if args.json:
            print(json.dumps(report.to_dict(), indent=2))
        else:
            print(format_report(report))
        return 1 if args.fail_under is not None and report.recall_at_k < args.fail_under else 0

    if args.command == "serve":
        from repo_index_mcp.mcp_server import run_server

        run_server(db_path=args.db)
        return 0

    if args.command == "serve-http":
        from repo_index_mcp.http_server import run_http_server

        run_http_server(db_path=args.db, host=args.host, port=args.port)
        return 0

    parser.print_help()
    return 1


def handle_index_root(args: argparse.Namespace) -> int:
    engine = RepoIndex(db_path=args.db)
    repo_paths, skipped_worktrees = discover_repos_with_skipped(
        args.root_path,
        include_worktrees=args.include_worktrees,
    )
    total_discovered = len(repo_paths)
    if args.limit is not None:
        repo_paths = repo_paths[: args.limit]
    total_selected = len(repo_paths)
    if args.progress and skipped_worktrees:
        print(f"skipped {skipped_worktrees} linked worktree(s)", file=sys.stderr, flush=True)
    started_at = time.monotonic()
    results = []
    stopped_early = False

    for index, repo_path in enumerate(repo_paths, start=1):
        elapsed = time.monotonic() - started_at
        if args.max_duration is not None and elapsed >= args.max_duration:
            stopped_early = True
            break
        if args.progress:
            print(
                f"[{index}/{total_selected}] indexing {repo_path}",
                file=sys.stderr,
                flush=True,
            )
        try:
            result = engine.index_repo(repo_path)
        except Exception as exc:
            repo_path_str = str(Path(repo_path).expanduser().resolve())
            from repo_index_mcp.models import IndexResult

            result = IndexResult(
                repo_id=repo_path_str,
                repo_path=repo_path_str,
                commit_sha="",
                files_indexed=0,
                chunks_indexed=0,
                duration_ms=0,
                error_count=1,
                last_error=str(exc),
            )
        results.append(result)
        if args.jsonl:
            print(json.dumps(asdict(result)), flush=True)
        if args.progress:
            print(
                f"[{index}/{total_selected}] done {repo_path} "
                f"files_changed={result.files_changed} files_ignored={result.files_ignored} "
                f"chunks_indexed={result.chunks_indexed} chunks_skipped={result.chunks_skipped} "
                f"duration_ms={result.duration_ms} errors={result.error_count}",
                file=sys.stderr,
                flush=True,
            )

    elapsed_ms = int((time.monotonic() - started_at) * 1000)
    summary = {
        "event": "summary",
        "root_path": str(Path(args.root_path).expanduser()),
        "repos_discovered": total_discovered,
        "repos_selected": total_selected,
        "worktrees_skipped": skipped_worktrees,
        "repos_indexed": len(results),
        "stopped_early": stopped_early,
        "duration_ms": elapsed_ms,
        "error_count": sum(result.error_count for result in results),
    }
    if args.jsonl:
        print(json.dumps(summary), flush=True)
    else:
        print(json.dumps([asdict(result) for result in results], indent=2))
        show_summary = any(
            (
                args.progress,
                stopped_early,
                args.limit is not None,
                args.max_duration is not None,
            )
        )
        if show_summary:
            print(json.dumps(summary, indent=2), file=sys.stderr)
    return 1 if any(result.error_count for result in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codescry")
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
    index_root.add_argument(
        "--progress",
        action="store_true",
        help="print per-repo progress to stderr",
    )
    index_root.add_argument("--jsonl", action="store_true", help="stream one JSON result per line")
    index_root.add_argument(
        "--include-worktrees",
        action="store_true",
        help="include linked git worktrees discovered under root",
    )
    index_root.add_argument("--limit", type=positive_int, help="index at most N discovered repos")
    index_root.add_argument(
        "--max-duration",
        type=positive_int,
        help="stop before starting the next repo after this many seconds",
    )

    query = subparsers.add_parser("query", help="query indexed code")
    query.add_argument("query")
    add_query_args(query)

    get_symbol = subparsers.add_parser("get-symbol", help="lookup a symbol definition")
    get_symbol.add_argument("name")
    get_symbol.add_argument("--repo")

    status = subparsers.add_parser("status", help="list indexed repos")
    status.set_defaults(_status=True)

    backfill_vectors_parser = subparsers.add_parser(
        "backfill-vectors",
        help="build local sqlite-vec candidate index for existing chunks",
    )
    backfill_vectors_parser.set_defaults(_backfill_vectors=True)

    doctor = subparsers.add_parser("doctor", help="check local setup and MCP readiness")
    doctor.set_defaults(_doctor=True)

    reindex = subparsers.add_parser("reindex", help="reindex repo")
    reindex.add_argument("repo_path", nargs="?", type=Path)

    prune = subparsers.add_parser("prune", help="remove missing repos from the index")
    prune.set_defaults(_prune=True)

    remove_repo = subparsers.add_parser("remove-repo", help="remove one repo from the index")
    remove_repo.add_argument("repo")

    hooks = subparsers.add_parser("install-hooks", help="install freshness git hooks")
    hooks.add_argument("path", type=Path)
    hooks.add_argument("--recursive", action="store_true")
    hooks.add_argument("--force", action="store_true")
    hooks.add_argument("--command-name", default="codescry")

    pilot = subparsers.add_parser("pilot", help="record/report local pilot proof")
    add_pilot_args(pilot)

    eval_add = subparsers.add_parser("eval-add", help="append a golden eval JSONL case")
    eval_add.add_argument("golden_file", type=Path)
    eval_add.add_argument("--id", required=True)
    eval_add.add_argument("--query", required=True)
    eval_add.add_argument("--expected-path", required=True)
    eval_add.add_argument("--expected-text")
    eval_add.add_argument("--expected-symbol")
    eval_add.add_argument("--notes")

    eval_parser = subparsers.add_parser("eval", help="run Recall@K over a golden JSONL set")
    eval_parser.add_argument("golden_file", type=Path)
    eval_parser.add_argument("repo_path", type=Path)
    eval_parser.add_argument("-k", type=positive_int, default=10)
    eval_parser.add_argument("--fail-under", type=float)
    eval_parser.add_argument("--json", action="store_true")
    eval_parser.add_argument("--debug", action="store_true")

    serve = subparsers.add_parser("serve", help="run MCP server over stdio")
    serve.set_defaults(_serve=True)

    serve_http = subparsers.add_parser("serve-http", help="run local HTTP API server")
    serve_http.add_argument("--host", default="127.0.0.1")
    serve_http.add_argument("--port", type=positive_int, default=8765)

    return parser


def add_query_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-k", type=positive_int, default=10)
    parser.add_argument("--repo")
    parser.add_argument("--path-prefix")
    parser.add_argument("--language")


def add_pilot_args(parser: argparse.ArgumentParser) -> None:
    subparsers = parser.add_subparsers(dest="pilot_command", required=True)

    start = subparsers.add_parser("start-task", help="start timing a pilot task")
    start.add_argument("--engineer", required=True)
    start.add_argument("--task", required=True)
    start.add_argument("--task-class")
    start.add_argument("--repo", action="append", default=[])

    end = subparsers.add_parser("end-task", help="finish a pilot task")
    end.add_argument("task_id")
    end.add_argument("--engineer", required=True)
    end.add_argument("--baseline-source", required=True)
    end.add_argument("--baseline-minutes", type=float)
    end.add_argument("--tool-minutes", type=float)
    end.add_argument("--baseline-files-pasted", type=int)
    end.add_argument("--tool-files-pasted", type=int)
    end.add_argument("--mcp-queries", type=int)
    end.add_argument("--useful", choices=["yes", "no"])
    end.add_argument("--decision-grade", action="store_true")
    end.add_argument("--notes")

    activate = subparsers.add_parser("activate", help="record successful pilot activation")
    activate.add_argument("--engineer", required=True)
    activate.add_argument("--client", required=True)
    activate.add_argument("--repo")
    activate.add_argument("--doctor-ok", action="store_true")
    activate.add_argument("--repo-ready", dest="repo_indexed", action="store_true")
    activate.add_argument("--tools-visible", action="store_true")
    activate.add_argument("--list-repos-ok", action="store_true")
    activate.add_argument("--search-code-ok", action="store_true")
    activate.add_argument("--relevant-result", action="store_true")
    activate.add_argument("--notes")

    retain = subparsers.add_parser("retain", help="record whether pilot user kept MCP enabled")
    retain.add_argument("--engineer", required=True)
    retain.add_argument("--enabled", choices=["yes", "no"], required=True)
    retain.add_argument("--week2", action="store_true")
    retain.add_argument("--notes")

    miss = subparsers.add_parser("miss", help="record a missed or weak query")
    miss.add_argument("--raw-query")
    miss.add_argument("--expected-path")
    miss.add_argument("--expected-text")
    miss.add_argument("--scrubbed-query", required=True)
    miss.add_argument("--scrubbed-expected-text")
    miss.add_argument("--notes")

    report = subparsers.add_parser("report", help="summarize pilot usage and proof metrics")
    report.add_argument("--usage-log", type=Path, action="append", default=[])


def handle_pilot(args: argparse.Namespace) -> int:
    if args.pilot_command == "start-task":
        event = start_task(
            engineer=args.engineer,
            task=args.task,
            task_class=args.task_class,
            repos=args.repo,
        )
    elif args.pilot_command == "end-task":
        if args.decision_grade:
            if args.tool_minutes is None:
                args.tool_minutes = infer_tool_minutes(args.task_id)
            validate_decision_grade_args(args)
        event = end_task(
            task_id=args.task_id,
            engineer=args.engineer,
            baseline_source=args.baseline_source,
            baseline_minutes=args.baseline_minutes,
            tool_minutes=args.tool_minutes,
            baseline_files_pasted=args.baseline_files_pasted,
            tool_files_pasted=args.tool_files_pasted,
            mcp_queries=args.mcp_queries,
            useful=parse_optional_bool(args.useful),
            decision_grade=args.decision_grade,
            notes=args.notes,
        )
    elif args.pilot_command == "activate":
        event = record_activation(
            engineer=args.engineer,
            client=args.client,
            repo=args.repo,
            doctor_ok=args.doctor_ok,
            repo_indexed=args.repo_indexed,
            tools_visible=args.tools_visible,
            list_repos_ok=args.list_repos_ok,
            search_code_ok=args.search_code_ok,
            relevant_result=args.relevant_result,
            notes=args.notes,
        )
    elif args.pilot_command == "retain":
        event = record_retention(
            engineer=args.engineer,
            enabled=args.enabled == "yes",
            week2=args.week2,
            notes=args.notes,
        )
    elif args.pilot_command == "miss":
        event = record_miss(
            raw_query=args.raw_query,
            expected_path=args.expected_path,
            expected_text=args.expected_text,
            scrubbed_query=args.scrubbed_query,
            scrubbed_expected_text=args.scrubbed_expected_text,
            notes=args.notes,
        )
    elif args.pilot_command == "report":
        event = build_report(args.usage_log or None)
    else:
        raise ValueError(f"unknown pilot command: {args.pilot_command}")
    print(json.dumps(event, indent=2))
    return 0


def infer_tool_minutes(task_id: str) -> float | None:
    from repo_index_mcp.usage import read_events

    start = next(
        (event for event in reversed(read_events()) if event.get("task_id") == task_id),
        None,
    )
    if start and start.get("event") == "task_start":
        return round((time.time() - float(start["ts"])) / 60.0, 3)
    return None


def validate_decision_grade_args(args: argparse.Namespace) -> None:
    if args.baseline_source not in {"observed_paired_task", "prior_comparable"}:
        raise SystemExit(
            "decision-grade baseline source must be observed_paired_task or prior_comparable"
        )
    if args.baseline_minutes is None or args.tool_minutes is None:
        raise SystemExit("decision-grade tasks require --baseline-minutes and --tool-minutes")
    if args.baseline_minutes <= 0 or args.tool_minutes < 0:
        raise SystemExit("decision-grade task minutes must be non-negative and baseline > 0")
    for name in ("baseline_files_pasted", "tool_files_pasted", "mcp_queries"):
        value = getattr(args, name)
        if value is not None and value < 0:
            raise SystemExit(f"{name} cannot be negative")
    if args.useful is None or args.mcp_queries is None:
        raise SystemExit("decision-grade tasks require --useful and --mcp-queries")


def append_eval_case(args: argparse.Namespace) -> None:
    for field in (args.query, args.expected_text, args.expected_symbol, args.notes):
        if field and looks_like_secret(field):
            raise SystemExit("eval-add text looks like a secret; scrub before committing")
    payload = {
        "id": args.id,
        "query": args.query,
        "expected_path": args.expected_path,
    }
    if args.expected_text:
        payload["expected_text"] = args.expected_text
    if args.expected_symbol:
        payload["expected_symbol"] = args.expected_symbol
    if args.notes:
        payload["notes"] = args.notes
    args.golden_file.parent.mkdir(parents=True, exist_ok=True)
    with args.golden_file.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, sort_keys=True) + "\n")


def parse_optional_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "yes"


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return parsed


def result_to_dict(result: Any) -> dict[str, Any]:
    return asdict(result)


if __name__ == "__main__":
    raise SystemExit(main())
