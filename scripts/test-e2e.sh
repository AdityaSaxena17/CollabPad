#!/usr/bin/env bash
set -euo pipefail

project="collabpad-e2e-$$"
export COMPOSE_PROGRESS=quiet
export POSTGRES_DB=collab_docs
export POSTGRES_USER=collab_docs
export POSTGRES_PASSWORD=collab_docs_dev
export COLLAB_DOCS_POSTGRES_PORT=55433
export GATEWAY_PORT=18081
export FRONTEND_ORIGIN=http://localhost:3001
export DATABASE_URL=postgresql://collab_docs:collab_docs_dev@localhost:55433/collab_docs
export NEXT_PUBLIC_GATEWAY_HTTP_URL=http://localhost:18081
export NEXT_PUBLIC_GATEWAY_WS_URL=ws://localhost:18081

node -e 'require("@next/env").loadEnvConfig(process.cwd()); for (const name of ["NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "CLERK_SECRET_KEY", "E2E_OWNER_EMAIL", "E2E_GUEST_EMAIL"]) { if (!process.env[name]) { console.error(`${name} is required for the live Clerk browser suite.`); process.exitCode = 1; } }'

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
npx playwright test
