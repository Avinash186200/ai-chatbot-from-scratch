#!/usr/bin/env sh
# The Docker stack without make.
#
#   scripts/stack.sh up      # certs (if missing) + build + start, waits for health
#   scripts/stack.sh down    # stop and remove containers (volumes kept)
#   scripts/stack.sh seed    # vendor feed + documents into the running stack
#   scripts/stack.sh logs    # follow logs
#   scripts/stack.sh test    # every offline check (scripts/test-all.sh)
#   scripts/stack.sh config  # validate docker-compose.yml with your .env
set -eu

root=$(cd "$(dirname "$0")/.." && pwd)
cd "$root"

need_env() {
  [ -f .env ] || { echo "no .env: cp .env.example .env and set AA_GATEWAY_TOKEN, POSTGRES_PASSWORD" >&2; exit 1; }
}

case "${1:-}" in
  up)
    need_env
    sh scripts/gen-certs.sh
    docker compose up -d --build --wait
    port=$(grep -E '^AA_HTTPS_PORT=' .env | cut -d= -f2)
    echo "stack is up: https://localhost:${port:-8443}"
    ;;
  down)
    docker compose down
    ;;
  seed)
    need_env
    docker compose exec api python -m analytics_assistant seed
    ;;
  logs)
    docker compose logs -f
    ;;
  test)
    sh scripts/test-all.sh
    ;;
  config)
    need_env
    docker compose config --quiet && echo "docker-compose.yml is valid"
    ;;
  *)
    echo "usage: $0 {up|down|seed|logs|test|config}" >&2
    exit 2
    ;;
esac
