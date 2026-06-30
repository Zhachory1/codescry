from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for npm wrapper tests")


def test_npm_wrapper_forwards_args_to_uvx(tmp_path: Path) -> None:
    uvx = tmp_path / ("uvx.cmd" if os.name == "nt" else "uvx")
    output = tmp_path / "args.txt"
    if os.name == "nt":
        uvx.write_text(
            "@echo off\r\n"
            "setlocal enabledelayedexpansion\r\n"
            "break > %REPO_INDEX_MCP_TEST_ARGS%\r\n"
            ":loop\r\n"
            "if \"%~1\"==\"\" exit /b 17\r\n"
            "echo %~1>> %REPO_INDEX_MCP_TEST_ARGS%\r\n"
            "shift\r\n"
            "goto loop\r\n",
            encoding="utf-8",
        )
    else:
        uvx.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$@\" > \"$REPO_INDEX_MCP_TEST_ARGS\"\n"
            "exit 17\n",
            encoding="utf-8",
        )
        uvx.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}{os.pathsep}{env.get('PATH', '')}"
    env["REPO_INDEX_MCP_TEST_ARGS"] = str(output)
    env.pop("REPO_INDEX_MCP_UVX", None)

    result = subprocess.run(
        [NODE, "bin/repo-index.js", "--db", "/tmp/index.sqlite", "doctor"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
    )

    assert result.returncode == 17
    assert output.read_text(encoding="utf-8").splitlines() == [
        "repo-index-mcp",
        "--db",
        "/tmp/index.sqlite",
        "doctor",
    ]


def test_npm_wrapper_explains_missing_uvx(tmp_path: Path) -> None:
    empty_path = tmp_path / "empty-path"
    empty_path.mkdir()
    env = os.environ.copy()
    env["PATH"] = str(empty_path)
    env.pop("REPO_INDEX_MCP_UVX", None)

    result = subprocess.run(
        [NODE, "bin/repo-index.js", "doctor"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 127
    assert "requires uv" in result.stderr
    assert "npx repo-index-mcp doctor" in result.stderr
