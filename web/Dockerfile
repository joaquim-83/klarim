# Klarim web (Astro) — SSR standalone servido por Node (KL-51).
# O adapter @astrojs/node em modo standalone gera dist/server/entry.mjs, que
# serve as páginas pré-renderizadas + assets (e as SSR das próximas fases).

# --- build ---
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

# --- runtime ---
FROM node:22-slim AS web
WORKDIR /app
ENV NODE_ENV=production \
    HOST=0.0.0.0 \
    PORT=4321
# Só o necessário para rodar o server standalone.
COPY --from=build /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 4321
CMD ["node", "./dist/server/entry.mjs"]
