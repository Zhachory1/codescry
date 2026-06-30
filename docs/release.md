# Release flow

`repo-index-mcp` is a Python package first. The npm package is a small wrapper that lets npm/npx users run the same CLI through `uvx`.

## Local state boundary

Release artifacts ship code only. Do not publish local index data.

- Default DB: `~/.repo-index-mcp/index.sqlite`
- Override: `repo-index --db /path/to/index.sqlite ...`
- Contents: derived code index data that can be deleted and rebuilt
- Rebuild: `repo-index index-root ~/code`

## PyPI release

1. Update version in:
   - `pyproject.toml`
   - `src/repo_index_mcp/__init__.py`
2. Run checks:

   ```bash
   uv run pytest
   uv build
   ```

3. Publish:

   ```bash
   uv publish
   ```

4. Verify:

   ```bash
   uvx repo-index-mcp doctor
   uvx repo-index-mcp --db /tmp/repo-index-smoke.sqlite status
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

4. Publish:

   ```bash
   npm publish --access public
   ```

5. Verify:

   ```bash
   npx repo-index-mcp@latest doctor
   npx repo-index-mcp@latest --db /tmp/repo-index-smoke.sqlite status
   ```

## MCP smoke test

Use npm/npx config when validating npm wrapper install:

```json
{
  "mcpServers": {
    "repo-index": {
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "repo-index-mcp", "--db", "/Users/YOU/.repo-index-mcp/index.sqlite", "serve"],
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
