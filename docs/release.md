# Release flow

`codescry` is a Python package first. The npm package is a small wrapper that lets npm/npx users run the same CLI through `uvx`.

## Local state boundary

Release artifacts ship code only. Do not publish local index data.

- Default DB: `~/.codescry/index.sqlite`
- Override: `codescry --db /path/to/index.sqlite ...`
- Contents: derived code index data that can be deleted and rebuilt
- Rebuild: `codescry index-root ~/code`

## PyPI release

1. Update version in:
   - `pyproject.toml`
   - `src/repo_index_mcp/__init__.py`
2. Run checks:

   ```bash
   uv run ruff check .
   uv run pytest
   codescry eval evals/golden.codescry.jsonl . -k 10 --fail-under 0.85
   uv build
   ```

3. For ranking or embedding-provider changes, run relevant public/provider evals and update `docs/ranking-experiment-findings.md`.

4. For data-boundary-affecting changes, review:
   - `README.md`
   - `SECURITY.md`
   - `docs/embedding-providers.md`

5. Publish:

   ```bash
   uv publish
   ```

6. Verify:

   ```bash
   uvx codescry doctor
   uvx codescry --db /tmp/codescry-smoke.sqlite status
   ```

## npm wrapper release

1. Match `package.json` version to the Python package version.
2. Run wrapper tests:

   ```bash
   npm test
   ```

3. Check package contents:

   ```bash
   npm pack --dry-run
   ```

   Expected docs include `docs/embedding-providers.md`, `docs/evals.md`, and `docs/ranking-experiment-findings.md`.

4. Publish:

   ```bash
   npm publish --access public
   ```

5. Verify:

   ```bash
   npx codescry@latest doctor
   npx codescry@latest --db /tmp/codescry-smoke.sqlite status
   ```

## MCP smoke test

Use npm/npx config when validating npm wrapper install:

```json
{
  "mcpServers": {
    "codescry": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "codescry", "--db", "/Users/YOU/.codescry/index.sqlite", "serve"],
      "env": {}
    }
  }
}
```

Then restart the MCP client and call `list_repos`.

## Known risks

- npm wrapper requires `uv`/`uvx` on `PATH`.
- Python native dependencies may be unavailable on some platforms.
- Standalone binaries would remove the Python/uv requirement but need separate platform builds.
