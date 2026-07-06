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
COPY docker-entrypoint.d/40-klarim-tls.sh /docker-entrypoint.d/40-klarim-tls.sh
RUN chmod +x /docker-entrypoint.d/40-klarim-tls.sh \
    && rm -f /etc/nginx/conf.d/default.conf

EXPOSE 80 443
