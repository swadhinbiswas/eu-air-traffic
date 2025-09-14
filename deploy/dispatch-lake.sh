#!/usr/bin/env bash
# Dispatch the lake workflow from the VPS, so the cadence does not depend on
# GitHub's best-effort scheduler. Skips when a run is already queued/running.
set -euo pipefail

: "${GITHUB_DISPATCH_TOKEN:?set GITHUB_DISPATCH_TOKEN in /opt/eu-air-traffic/.env (fine-grained PAT, Actions: read and write)}"
REPO="${GITHUB_REPO:-swadhinbiswas/eu-air-traffic}"
API="https://api.github.com/repos/${REPO}/actions/workflows/lake.yml"
AUTH=(-H "Authorization: Bearer ${GITHUB_DISPATCH_TOKEN}" -H "Accept: application/vnd.github+json")

busy=$(curl -fsS "${AUTH[@]}" "${API}/runs?per_page=10" \
  | python3 -c "import json,sys; print(any(r['status'] in ('in_progress','queued') for r in json.load(sys.stdin)['workflow_runs']))")

if [ "${busy}" = "True" ]; then
  echo "[dispatch] a lake run is already queued or running — skipping"
  exit 0
fi

curl -fsS -X POST "${AUTH[@]}" -H "Content-Type: application/json" \
  -d '{"ref":"main"}' "${API}/dispatches"
echo "[dispatch] lake run requested"
