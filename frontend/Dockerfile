# Klarim frontend — build estático (Vite) servido pelo Nginx (HTTP ou HTTPS).

# --- Build stage ---
FROM node:20-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- Production stage ---
FROM nginx:alpine
# envsubst (gettext) para gerar a config a partir do template.
RUN apk add --no-cache gettext

COPY --from=build /app/dist /usr/share/nginx/html

# Templates de config e o hook que escolhe HTTP x HTTPS em runtime.
COPY nginx/http.conf /klarim/http.conf
COPY nginx/https.conf.template /klarim/https.conf.template
# KL-92 P3: log_format `klarim` + access_log parseável em /var/log/klarim (contexto http, via
# conf.d — coexiste com o default.conf escrito pelo entrypoint). O dir precisa existir p/ o
# nginx abrir o log; o volume `klarim-nginx-logs` do compose o compartilha com o container api.
COPY nginx/log_format.conf /etc/nginx/conf.d/00-klarim-log.conf
RUN mkdir -p /var/log/klarim
# Security headers compartilhados (incluídos pelo server block e pelos locations).
COPY nginx/security_headers.conf /etc/nginx/klarim_security_headers.conf
# Policy MTA-STS (servida em mta-sts.<domínio>/.well-known/mta-sts.txt).
COPY nginx/mta-sts.txt /etc/nginx/klarim_mta-sts.txt
# security.txt (RFC 9116) — servido em /.well-known/security.txt via alias (KL-65).
COPY nginx/security.txt /etc/nginx/klarim_security.txt
COPY docker-entrypoint.d/40-klarim-tls.sh /docker-entrypoint.d/40-klarim-tls.sh
RUN chmod +x /docker-entrypoint.d/40-klarim-tls.sh \
    && rm -f /etc/nginx/conf.d/default.conf

EXPOSE 80 443
