#!/usr/bin/env bash
# Deploy the full platform with Docker Compose.
#
#   ./deploy.sh            build and start collector + lake (+ TLS with --tls)
#   ./deploy.sh --logs     follow logs
#   ./deploy.sh --down     stop and remove containers
#   ./deploy.sh --ps       status
set -euo pipefail

cd "$(dirname "$0")"
COMPOSE=(docker compose)
PROFILES=()

case "${1:-}" in
  --logs) exec "${COMPOSE[@]}" logs -f --tail=100 ;;
  --down) exec "${COMPOSE[@]}" down ;;
  --ps|--status) exec "${COMPOSE[@]}" ps ;;
  --tls) PROFILES=(--profile tls) ;;
  --help|-h)
    sed -n '2,8p' "$0"
    exit 0
    ;;
  "") ;;
  *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
esac

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "docker daemon is not running" >&2; exit 1; }

if [ ! -f .env ]; then
  echo "creating .env from .env.example — fill in your credentials, then re-run" >&2
  cp .env.example .env
  exit 1
fi

if [ ! -f secrets/aiven-ca.pem ]; then
  echo "warning: secrets/aiven-ca.pem is missing; mount it or set KAFKA_SECURITY_PROTOCOL=PLAINTEXT" >&2
fi

echo "building images…"
"${COMPOSE[@]}" "${PROFILES[@]}" build

echo "starting containers…"
"${COMPOSE[@]}" "${PROFILES[@]}" up -d

echo "waiting for the live API…"
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8090/health >/dev/null 2>&1; then
    echo
    echo "collector is healthy:  http://127.0.0.1:8090/health"
    [ ${#PROFILES[@]} -gt 0 ] && echo "TLS front:             https://${SITE_ADDRESS:-localhost}"
    echo "lake loop runs every ${LAKE_INTERVAL_SECONDS:-900}s; ./deploy.sh --logs to watch it"
    exit 0
  fi
  sleep 2
done

echo "collector did not become healthy in 60s — check ./deploy.sh --logs" >&2
exit 1
