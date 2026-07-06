import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from repo_index_mcp.cli import main, positive_int
from repo_index_mcp.doctor import run_doctor


def test_positive_int() -> None:
    assert positive_int("3") == 3


def test_doctor_returns_healthy_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(module: str):  # type: ignore[no-untyped-def]
        if module == "mcp":
            return object()
        return real_find_spec(module)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    result, exit_code = run_doctor(tmp_path / "index.sqlite")

    assert exit_code == 0
    assert result["ok"] is True
    assert result["checks"]["git"]["ok"] is True
    assert result["checks"]["db_writable"]["ok"] is True
    assert result["checks"]["mcp_dependency"]["ok"] is True


def test_doctor_returns_nonzero_for_unwritable_db_path(tmp_path: Path) -> None:
    directory = tmp_path / "not-a-db"
    directory.mkdir()

    result, exit_code = run_doctor(directory)

    assert exit_code == 1
    assert result["ok"] is False
    assert result["checks"]["db_writable"]["ok"] is False


def test_get_symbol_cli_returns_symbol(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    repo = tmp_path / "repo"
    db_path = tmp_path / "index.sqlite"
    repo.mkdir()
    (repo / "app.py").write_text("def hello_world():\n    return True\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    assert main(["--db", str(db_path), "index", str(repo)]) == 0
    assert main(["--db", str(db_path), "get-symbol", "hello_world"]) == 0

    output = capsys.readouterr().out
    assert "hello_world" in output
    assert "app.py" in output


def test_query_empty_result_prints_hint(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    db_path = tmp_path / "index.sqlite"

    assert main(["--db", str(db_path), "query", "nothing"]) == 0

    captured = capsys.readouterr()
    assert captured.out.strip() == "[]"
    assert "No results" in captured.err


def test_pilot_report_summarizes_decision_grade_task(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(tmp_path / "usage.jsonl"))

    assert main([
        "pilot",
        "activate",
        "--engineer",
        "Ada",
        "--client",
        "mewrite",
        "--doctor-ok",
        "--repo-ready",
        "--tools-visible",
        "--list-repos-ok",
        "--search-code-ok",
        "--relevant-result",
    ]) == 0
    assert main(["pilot", "retain", "--engineer", "Ada", "--enabled", "yes", "--week2"]) == 0
    capsys.readouterr()
    assert main([
        "pilot",
        "start-task",
        "--engineer",
        "Ada",
        "--task",
        "find retry implementation",
    ]) == 0
    task_output = capsys.readouterr().out
    task_id = __import__("json").loads(task_output)["task_id"]
    assert main([
        "pilot",
        "end-task",
        task_id,
        "--engineer",
        "Ada",
        "--baseline-source",
        "observed_paired_task",
        "--baseline-minutes",
        "10",
        "--tool-minutes",
        "4",
        "--baseline-files-pasted",
        "5",
        "--tool-files-pasted",
        "1",
        "--mcp-queries",
        "3",
        "--useful",
        "yes",
        "--decision-grade",
    ]) == 0
    assert main(["pilot", "report"]) == 0

    output = capsys.readouterr().out
    assert '"activation_count": 1' in output
    assert '"decision_grade_tasks": 1' in output
    assert '"valid_decision_grade_tasks": 1' in output
    assert '"context_minutes_reduction": 0.6' in output
    assert '"week2_retention_rate": 1.0' in output


def test_pilot_report_retention_denominator_is_activated_engineers(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    for engineer in ("Ada", "Grace", "Linus", "Margaret"):
        assert main([
            "pilot",
            "activate",
            "--engineer",
            engineer,
            "--client",
            "mewrite",
            "--doctor-ok",
            "--repo-ready",
            "--tools-visible",
            "--list-repos-ok",
            "--search-code-ok",
            "--relevant-result",
        ]) == 0
    assert main(["pilot", "retain", "--engineer", "Ada", "--enabled", "yes", "--week2"]) == 0
    assert main(["pilot", "report"]) == 0

    output = capsys.readouterr().out
    assert '"week2_retention_rate": 0.25' in output
    assert '"retention_70pct": false' in output


def test_search_usage_logging_is_opt_in(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    usage_log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(usage_log))

    assert main(["--db", str(tmp_path / "index.sqlite"), "query", "secret query text"]) == 0

    assert not usage_log.exists()


def test_decision_grade_rejects_estimates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(tmp_path / "usage.jsonl"))

    with pytest.raises(SystemExit):
        main([
            "pilot",
            "end-task",
            "pilot-1",
            "--engineer",
            "Ada",
            "--baseline-source",
            "estimate",
            "--baseline-minutes",
            "10",
            "--tool-minutes",
            "4",
            "--decision-grade",
        ])


def test_usage_log_redacts_raw_query_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage_log = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(usage_log))
    monkeypatch.setenv("CODESCRY_ENABLE_USAGE_LOG", "1")

    assert main(["--db", str(tmp_path / "index.sqlite"), "query", "secret query text"]) == 0

    content = usage_log.read_text(encoding="utf-8")
    assert "secret query text" not in content
    assert "query_length" in content
    assert "query_id" in content


def test_pilot_report_accepts_multiple_usage_logs(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    first = tmp_path / "first.jsonl"
    second = tmp_path / "second.jsonl"
    first.write_text(
        '{"event":"activation","engineer":"Ada","doctor_ok":true,'
        '"repo_indexed":true,"tools_visible":true,"list_repos_ok":true,'
        '"search_code_ok":true,"relevant_result":true}\n',
        encoding="utf-8",
    )
    second.write_text(
        '{"event":"activation","engineer":"Grace","doctor_ok":true,'
        '"repo_indexed":true,"tools_visible":true,"list_repos_ok":true,'
        '"search_code_ok":true,"relevant_result":true}\n',
        encoding="utf-8",
    )

    assert main(["pilot", "report", "--usage-log", str(first), "--usage-log", str(second)]) == 0

    assert '"activation_count": 2' in capsys.readouterr().out


def test_pilot_report_skips_malformed_jsonl(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage_log = tmp_path / "usage.jsonl"
    usage_log.write_text(
        'not-json\n[]\n{"event":"task_end","decision_grade":true,'
        '"baseline_minutes":"oops","tool_minutes":1}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CODESCRY_USAGE_LOG", str(usage_log))

    assert main(["pilot", "report"]) == 0

    output = capsys.readouterr().out
    assert '"bad_line_count": 2' in output
    assert '"valid_decision_grade_tasks": 0' in output


def test_backfill_vectors_cli_empty_db(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--db", str(tmp_path / "index.sqlite"), "backfill-vectors"]) == 0

    assert "vectors_indexed" in capsys.readouterr().out


def test_eval_add_rejects_secret_text(tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"

    with pytest.raises(SystemExit):
        main([
            "eval-add",
            str(golden),
            "--id",
            "case-1",
            "--query",
            "token " + "ghp" + "_abcdefghijklmnopqrstuvwxyz",
            "--expected-path",
            "src/retry.py",
        ])


def test_eval_add_appends_jsonl(tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"

    assert main([
        "eval-add",
        str(golden),
        "--id",
        "case-1",
        "--query",
        "retry request",
        "--expected-path",
        "src/retry.py",
        "--expected-text",
        "def retry",
    ]) == 0

    assert "retry request" in golden.read_text(encoding="utf-8")


def test_index_root_jsonl_streams_results_and_summary(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "app.py").write_text("def first_service(): pass\n", encoding="utf-8")
    (second / "app.py").write_text("def second_service(): pass\n", encoding="utf-8")
    init_repo(first)
    init_repo(second)
    commit_all(first, "init")
    commit_all(second, "init")

    result = main([
        "--db",
        str(tmp_path / "index.sqlite"),
        "index-root",
        str(tmp_path),
        "--jsonl",
        "--limit",
        "1",
    ])

    assert result == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 2
    assert lines[0]["repo_path"].endswith("first")
    assert lines[1]["event"] == "summary"
    assert lines[1]["repos_discovered"] == 2
    assert lines[1]["repos_selected"] == 1
    assert lines[1]["repos_indexed"] == 1


def test_index_root_skips_worktrees_by_default(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(
        "gitdir: /tmp/example.git/worktrees/worktree\n",
        encoding="utf-8",
    )

    result = main([
        "--db",
        str(tmp_path / "index.sqlite"),
        "index-root",
        str(tmp_path),
        "--jsonl",
    ])

    assert result == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines == [
        {
            "event": "summary",
            "root_path": str(tmp_path),
            "repos_discovered": 0,
            "repos_selected": 0,
            "worktrees_skipped": 1,
            "repos_indexed": 0,
            "stopped_early": False,
            "duration_ms": lines[0]["duration_ms"],
            "error_count": 0,
        }
    ]


def test_index_root_progress_prints_to_stderr(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def service(): pass\n", encoding="utf-8")
    init_repo(repo)
    commit_all(repo, "init")

    result = main([
        "--db",
        str(tmp_path / "index.sqlite"),
        "index-root",
        str(tmp_path),
        "--progress",
    ])

    assert result == 0
    captured = capsys.readouterr()
    assert "[1/1] indexing" in captured.err
    assert "[1/1] done" in captured.err


def test_index_root_max_duration_can_stop_before_indexing(
    tmp_path: Path,
    capsys,  # type: ignore[no-untyped-def]
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    times = iter([0.0, 2.0, 2.0])
    monkeypatch.setattr("repo_index_mcp.cli.time.monotonic", lambda: next(times))

    result = main([
        "--db",
        str(tmp_path / "index.sqlite"),
        "index-root",
        str(tmp_path),
        "--jsonl",
        "--max-duration",
        "1",
    ])

    assert result == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert lines[-1]["event"] == "summary"
    assert lines[-1]["stopped_early"] is True
    assert lines[-1]["repos_indexed"] == 0


def test_eval_returns_nonzero_when_indexing_fails(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    golden = tmp_path / "golden.jsonl"
    repo.mkdir()
    golden.write_text("", encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)

    result = main(["--db", str(tmp_path / "index.sqlite"), "eval", str(golden), str(repo)])

    assert result == 1


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
