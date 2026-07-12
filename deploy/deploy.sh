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

# 0) Trust the repo dir. Under CI this script runs as root (sudo) while
#    /opt/klarim is owned by the provisioning user, which trips git's
#    "dubious ownership" guard. Mark it safe for whoever runs the script
#    (idempotent — only added once).
if ! git config --global --get-all safe.directory 2>/dev/null | grep -qx "$APP_DIR"; then
  git config --global --add safe.directory "$APP_DIR"
fi

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

# 5b) Health check da plataforma pública (Astro, KL-51) em localhost:4321.
echo "==> health check http://localhost:4321/ (Astro)"
web_ok=0
for i in $(seq 1 10); do
  if curl -fsS "http://localhost:4321/" >/dev/null 2>&1; then
    web_ok=1
    echo "    Astro respondeu OK na tentativa $i."
    break
  fi
  sleep 3
done

if [[ "$web_ok" -ne 1 ]]; then
  echo "AVISO: Astro não respondeu em / após múltiplas tentativas. Logs recentes:" >&2
  docker compose logs --tail=40 astro >&2 || true
  exit 1
fi

# 6) Renova o certificado Let's Encrypt se estiver perto de expirar (no-op se
#    ainda não é hora ou se o Certbot não está instalado). O deploy-hook recria
#    o container web para carregar o novo certificado.
if command -v certbot >/dev/null 2>&1; then
  echo "==> certbot renew (silencioso)"
  certbot renew --quiet \
    --deploy-hook "docker compose -f $APP_DIR/docker-compose.yml up -d --force-recreate web" || true
fi

echo "==> Deploy concluído: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
