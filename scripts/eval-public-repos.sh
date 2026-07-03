#!/usr/bin/env bash
set -euo pipefail

ROOT="${CODESCRY_PUBLIC_EVAL_ROOT:-$HOME/.cache/codescry/eval-repos}"
DB_DIR="${CODESCRY_PUBLIC_EVAL_DB_DIR:-$HOME/.cache/codescry/eval-dbs}"
K="${CODESCRY_PUBLIC_EVAL_K:-10}"
mkdir -p "$ROOT" "$DB_DIR"

run_eval() {
  local name="$1"
  local url="$2"
  local ref="$3"
  local golden="$4"
  local repo="$ROOT/$name"
  local db="$DB_DIR/$name.sqlite"

  if [[ ! -d "$repo/.git" ]]; then
    git clone "$url" "$repo"
  fi
  git -C "$repo" fetch --tags --quiet
  git -C "$repo" checkout --quiet "$ref"

  echo "== $name @ $ref =="
  codescry --db "$db" eval "$golden" "$repo" -k "$K"
  echo
}

run_eval \
  requests \
  https://github.com/psf/requests.git \
  v2.32.3 \
  evals/public/requests.v2.32.3.agent-natural.jsonl

run_eval \
  flask \
  https://github.com/pallets/flask.git \
  3.0.3 \
  evals/public/flask.3.0.3.agent-natural.jsonl

run_eval \
  pytest \
  https://github.com/pytest-dev/pytest.git \
  8.2.2 \
  evals/public/pytest.8.2.2.agent-natural.jsonl
