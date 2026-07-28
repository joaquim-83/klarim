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

# 0b) Guarda o commit ATUALMENTE em execução ANTES do pull — alvo do rollback (KL-124)
#     se o health check falhar depois do deploy. Capturado antes de qualquer checkout/pull
#     para apontar sempre para o estado bom que está no ar.
PREV_COMMIT="$(git rev-parse HEAD)"
echo "==> commit atual (rollback target): $(git rev-parse --short HEAD)"

# Rollback automático (KL-124): volta ao PREV_COMMIT, reconstrói e recria os containers.
# Chamado quando o health check da API ou do Astro falha após o deploy novo.
rollback() {
  local svc="$1"
  echo "ERRO: $svc não respondeu ao health check. Fazendo rollback para $PREV_COMMIT" >&2
  docker compose logs --tail=40 "$svc" >&2 || true
  # Volta ao código bom (HEAD destacado no PREV_COMMIT). O próximo deploy de CI
  # (git pull --ff-only) reavança a partir daqui quando um fix chegar em origin/main.
  git checkout "$PREV_COMMIT"
  docker compose build
  # Recreate escopado aos apps (mesma estratégia do deploy — não reinicia db/redis).
  docker compose up -d --remove-orphans
  docker compose up -d --force-recreate --no-deps api astro web worker discovery
  echo "Rollback concluído: rodando $(git rev-parse --short HEAD). Investigue os logs antes do próximo deploy." >&2
  exit 1
}

# 1) Fetch the latest code (fast-forward to origin/main).
echo "==> git pull origin main"
git pull --ff-only origin main

# 2) Guard: production env file must exist and never be overwritten by deploy.
if [[ ! -f "$APP_DIR/.env" ]]; then
  echo "ERRO: $APP_DIR/.env não encontrado. Crie-o a partir de .env.example antes do deploy." >&2
  exit 1
fi

# 3) Rebuild + recreate. Antes o `docker compose down` derrubava TUDO antes do build,
#    então o site ficava fora por todo o build (~2-5 min). Agora: (a) `build` gera as
#    imagens novas com os containers antigos AINDA NO AR; (b) recria só os containers de
#    APLICAÇÃO com o código novo.
#    **KL-124:** sem `--force-recreate`, o `up -d` só recria containers cuja IMAGEM mudou —
#    mas o layer cache do Docker (COPY . . com checksums iguais) pode não detectar mudança
#    em .py e manter o container antigo rodando código velho (incidente do KL-123: código
#    novo na VM, containers antigos). O `--force-recreate` **escopado aos 5 serviços de app**
#    (`--no-deps` + nomes) garante que api/worker/discovery/astro/web recriem SEMPRE, sem
#    tocar em db/redis — assim o banco NÃO reinicia a cada deploy (zero downtime na camada
#    de dados; dados nos volumes pgdata/redisdata). O `up -d --remove-orphans` antes garante
#    que db/redis estejam no ar (sem recriá-los) e limpa containers órfãos.
#    Downtime ~10-30s só no recreate dos apps.
echo "==> docker compose build (site continua no ar durante o build)"
docker compose build

echo "==> docker compose up -d --remove-orphans (garante db/redis no ar; remove órfãos)"
docker compose up -d --remove-orphans

echo "==> docker compose up -d --force-recreate (recria só os apps: api/astro/web/worker/discovery)"
docker compose up -d --force-recreate --no-deps api astro web worker discovery

# 3b) Limpeza de disco — CRÍTICO. Cada `--build` acumula build cache (GBs) e deixa
#     a imagem anterior como dangling; sem podar, o disco da VM (9.7G) enche e QUEBRA
#     tudo (batch, scans, banco — incidente de disco 100% após 3 deploys seguidos).
#     `builder prune` limpa o cache; `image prune -f` remove só imagens não usadas
#     (os containers em execução mantêm as suas). Nunca toca em volumes/dados.
echo "==> limpando build cache + imagens antigas (evita disco cheio)"
# `-af`: sem o `-a`, o prune deixa o cache do build recém-feito (~1.7GB) — só o -a
# limpa tudo (regenera no próximo build, ~1min a mais). Sem isto o disco enche.
docker builder prune -af || true
docker image prune -f || true

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
  rollback api   # KL-124 — API não subiu com o código novo: volta ao PREV_COMMIT
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
  rollback astro   # KL-124 — Astro não subiu com o código novo: volta ao PREV_COMMIT
fi

# 6) Renova o certificado Let's Encrypt se estiver perto de expirar (no-op se
#    ainda não é hora ou se o Certbot não está instalado). O deploy-hook recria
#    o container web para carregar o novo certificado.
if command -v certbot >/dev/null 2>&1; then
  echo "==> certbot renew (silencioso)"
  certbot renew --quiet \
    --deploy-hook "docker compose -f $APP_DIR/docker-compose.yml up -d --force-recreate web" || true
fi

# 7) Registra o commit que ficou no ar (KL-124 — rastreabilidade: qual código está rodando).
echo "==> Deploy OK: commit $(git rev-parse --short HEAD) em $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
