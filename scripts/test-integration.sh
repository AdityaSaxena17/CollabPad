#!/usr/bin/env bash
set -euo pipefail

project="collabpad-tests-$$"
export COMPOSE_PROGRESS=quiet
export POSTGRES_DB=collab_docs
export POSTGRES_USER=collab_docs
export POSTGRES_PASSWORD=collab_docs_dev
export COLLAB_DOCS_POSTGRES_PORT=55433
export GATEWAY_PORT=18081
export FRONTEND_ORIGIN=http://localhost:3001
export CLERK_SECRET_KEY=sk_test_integration_placeholder
export DATABASE_URL=postgresql://collab_docs:collab_docs_dev@localhost:55433/collab_docs

compose=(docker compose -p "$project" -f docker-compose.yml -f docker-compose.test.yml)

cleanup() {
  exit_code=$?
  if (( exit_code != 0 )); then
    "${compose[@]}" logs --tail=80 postgres migrate collaboration redis llm gateway || true
  fi
  "${compose[@]}" down -v --remove-orphans >/dev/null || true
}
trap cleanup EXIT

"${compose[@]}" up --build -d postgres migrate collaboration redis llm gateway
"${compose[@]}" --profile test run --build --rm test-runner
npm run test:webhook:integration
