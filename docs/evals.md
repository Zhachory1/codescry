# Evals

Golden evals are JSONL rows:

```json
{"id":"case-001","query":"retry request","expected_path":"src/retry.py","expected_text":"def retry_request"}
```

Fields:

- `id`: stable case id.
- `query`: user/agent search query.
- `expected_path`: repo-relative file expected in top K.
- `expected_text`: optional text that must appear in the returned snippet.
- `expected_symbol`: optional future field for symbol cases.
- `notes`: optional context.

Run:

```bash
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --json
repo-index eval evals/golden.repo-index-mcp.jsonl . -k 10 --fail-under 0.85
```

Rules:

- Add misses from real pilot tasks.
- Do not delete hard cases just to raise Recall@10.
- Keep query wording close to real user/agent language.
