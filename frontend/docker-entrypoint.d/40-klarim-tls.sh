#!/bin/sh
# Escolhe a configuração do Nginx conforme a presença do certificado.
# Roda dentro do entrypoint da imagem nginx (antes do nginx iniciar).
#
#   - Sem DOMAIN definido, ou sem o certificado no disco  -> serve HTTP (porta 80).
#   - Com DOMAIN e certificado presente                    -> serve HTTPS (443) + redirect 80.
#
# Isso torna o container self-healing: o primeiro deploy (sem cert) sobe em HTTP;
# depois que o setup-https.sh emite o certificado e o container é reiniciado,
# ele passa a servir HTTPS automaticamente. Zero downtime, sem quebrar o site.
set -e

DOMAIN="${DOMAIN:-}"
OUT="/etc/nginx/conf.d/default.conf"
CERT=""
[ -n "$DOMAIN" ] && CERT="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"

if [ -n "$DOMAIN" ] && [ -f "$CERT" ]; then
    echo "[klarim-tls] certificado encontrado para '${DOMAIN}' — servindo HTTPS"
    export DOMAIN
    # Substitui apenas ${DOMAIN} (preserva variáveis do Nginx como $host).
    envsubst '${DOMAIN}' < /klarim/https.conf.template > "$OUT"
else
    echo "[klarim-tls] sem certificado (DOMAIN='${DOMAIN}') — servindo HTTP"
    cp /klarim/http.conf "$OUT"
fi
