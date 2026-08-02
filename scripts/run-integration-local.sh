#!/usr/bin/env bash
# Local-only: runs integration tests against a live OpenGauss/Postgres DB.
# NOT run in CI. Developer must run this before merging changes that touch
# generated integration test code or Service logic.
#
# Prerequisites:
#   - DB_PASSWORD env var set (export DB_PASSWORD=your_password)
#   - Live OpenGauss/Postgres at the URL configured in fluxgauss_py.yaml / fluxgauss_ru.yaml
#   - Generated dest_py/ and/or dest_ru/ directories (run converter first)
#
# Usage:
#   ./scripts/run-integration-local.sh           # both engines
#   ./scripts/run-integration-local.sh dest_py   # specific engine only
set -euo pipefail

DESTS=("$@")
if [ ${#DESTS[@]} -eq 0 ]; then
  DESTS=(dest_py dest_ru)
fi

if [ -z "${DB_PASSWORD:-}" ]; then
  echo "ERROR: DB_PASSWORD env var not set." >&2
  echo "  export DB_PASSWORD=your_password" >&2
  exit 1
fi

FAIL=0
for dest in "${DESTS[@]}"; do
  if [ ! -d "$dest" ]; then
    echo "SKIP: $dest does not exist (run converter first)" >&2
    continue
  fi
  echo "=== Integration tests for $dest ==="
  if ! (cd "$dest" && mvn verify -Pintegration -DfailIfNoTests=false); then
    echo "FAIL: integration tests failed for $dest" >&2
    FAIL=1
  fi
done

exit $FAIL
