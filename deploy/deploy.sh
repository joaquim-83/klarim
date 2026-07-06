#!/bin/bash
#
# Klarim deploy script — runs ON THE VM (/opt/klarim/deploy/deploy.sh).
#
# Invoked by the GitHub Actions `deploy` job over SSH after tests pass, and
# usable by hand for a manual redeploy. It pulls the latest main, rebuilds the
# containers, and verifies the stack is up.
#
# Prerequisites (done once during provisioning — see claude.md / KL-3 report):
#   * Docker + docker compose plugin installed
#   * repo cloned into /opt/klarim
#   * /opt/klarim/.env present (NOT in git) with production values
#
set -euo pipefail

APP_DIR="/opt/klarim"
cd "$APP_DIR"

echo "==> Klarim deploy iniciado: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

# 1) Fetch the latest code (fast-forward to origin/main).
echo "==> git pull origin main"
git pull --ff-only origin main

# 2) Guard: production env file must exist and never be overwritten by deploy.
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "ERRO: $APP_DIR/.env não encontrado. Crie-o a partir de .env.example antes do deploy." >&2
  exit 1
fi

# 3) Rebuild and restart the stack.
echo "==> docker compose down"
docker compose down --remove-orphans

echo "==> docker compose up -d --build"
docker compose up -d --build

# 4) Show container state.
echo "==> docker compose ps"
docker compose ps

# 5) Light health check against the API (retry a few times while it boots).
echo "==> health check http://localhost:8000/health"
health_ok=0
for i in $(seq 1 10); do
  if curl -fsS "http://localhost:8000/health" >/dev/null 2>&1; then
    health_ok=1
    echo "    API respondeu OK na tentativa $i."
    break
  fi
  sleep 3
done

if [[ "$health_ok" -ne 1 ]]; then
  echo "AVISO: API não respondeu em /health após múltiplas tentativas. Logs recentes:" >&2
  docker compose logs --tail=40 api >&2 || true
  exit 1
fi

echo "==> Deploy concluído: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
