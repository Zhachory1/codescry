#!/usr/bin/env node
import { spawnSync } from "node:child_process";

const uvx = process.env.REPO_INDEX_MCP_UVX || "uvx";
const args = ["repo-index-mcp", ...process.argv.slice(2)];
const result = spawnSync(uvx, args, { stdio: "inherit" });

if (result.error) {
  if (result.error.code === "ENOENT") {
    console.error([
      "repo-index-mcp npm wrapper requires uv, but uvx was not found on PATH.",
      "Install uv: https://docs.astral.sh/uv/getting-started/installation/",
      "Then retry: npx repo-index-mcp doctor"
    ].join("\n"));
    process.exit(127);
  }

  console.error(`repo-index-mcp npm wrapper failed: ${result.error.message}`);
  process.exit(1);
}

process.exit(result.status ?? 1);
