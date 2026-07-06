#!/bin/bash
#
# Klarim — setup HTTPS (Let's Encrypt) na VM. Rodar UMA VEZ, após o domínio
# apontar (registro A) para o IP público desta VM.
#
#   sudo bash /opt/klarim/deploy/setup-https.sh <dominio> [email]
#   ex.: sudo bash /opt/klarim/deploy/setup-https.sh klarim.com.br
#
# Usa o modo webroot: o Nginx (em HTTP) serve /.well-known/acme-challenge/ a
# partir de /var/www/certbot, então NÃO há downtime na emissão nem na renovação.
#
set -euo pipefail

DOMAIN="${1:?Uso: $0 <dominio> [email]}"
EMAIL="${2:-klarimscan@gmail.com}"
COMPOSE="/opt/klarim/docker-compose.yml"
ENV_FILE="/opt/klarim/.env"

echo "== 1/5 Instalando Certbot =="
apt-get update -y
apt-get install -y certbot

echo "== 2/5 Preparando webroot =="
mkdir -p /var/www/certbot

echo "== 3/5 Garantindo o Nginx no ar (modo HTTP, servindo o ACME challenge) =="
docker compose -f "$COMPOSE" up -d web

echo "== 4/5 Emitindo certificado para $DOMAIN (e www, se resolver) =="
if ! certbot certonly --webroot -w /var/www/certbot \
        -d "$DOMAIN" -d "www.$DOMAIN" \
        --non-interactive --agree-tos --email "$EMAIL" --keep-until-expiring; then
    echo "   www.$DOMAIN falhou (DNS ausente?); tentando apenas $DOMAIN..."
    certbot certonly --webroot -w /var/www/certbot \
        -d "$DOMAIN" \
        --non-interactive --agree-tos --email "$EMAIL" --keep-until-expiring
fi

echo "== 5/5 Ativando DOMAIN no .env e recriando o web em HTTPS =="
if grep -q '^DOMAIN=' "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^DOMAIN=.*|DOMAIN=$DOMAIN|" "$ENV_FILE"
else
    echo "DOMAIN=$DOMAIN" >> "$ENV_FILE"
fi
docker compose -f "$COMPOSE" up -d --force-recreate web

echo "== Renovação automática =="
systemctl list-timers 2>/dev/null | grep -i certbot \
    || echo "(o pacote certbot instala um timer/cron de renovação; deploy.sh também renova)"

echo
echo "== HTTPS configurado para $DOMAIN =="
echo "Teste:  curl -I https://$DOMAIN"
