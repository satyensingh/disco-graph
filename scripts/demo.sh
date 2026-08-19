#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://localhost:8080}"
REPO_URL="${REPO_URL:-https://github.com/psf/requests.git}"
BRANCH="${BRANCH:-main}"

CLONE_RESPONSE="$(curl -sS -X POST "$BASE_URL/repos/clone" \
  -H 'Content-Type: application/json' \
  -d "{\"repo_url\":\"$REPO_URL\",\"branch\":\"$BRANCH\"}")"
printf '%s\n\n' "$CLONE_RESPONSE"
REPO_ID="$(printf '%s' "$CLONE_RESPONSE" | python3 -c 'import json, sys; print(json.load(sys.stdin)["repo_id"])')"

curl -sS -X POST "$BASE_URL/repos/index" \
  -H 'Content-Type: application/json' \
  -d "{\"repo_id\":\"$REPO_ID\",\"force\":false}"
printf '\n\n'

curl -sS -X POST "$BASE_URL/repos/graph/query" \
  -H 'Content-Type: application/json' \
  -d "{\"repo_id\":\"$REPO_ID\",\"query\":\"how request sessions send HTTP requests\",\"depth\":1,\"max_nodes\":50}"
printf '\n'
