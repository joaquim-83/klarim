// @ts-check
import { defineConfig } from 'astro/config';
import node from '@astrojs/node';
import react from '@astrojs/react';
import tailwindcss from '@tailwindcss/vite';

// KL-51 — plataforma pública do Klarim.
// `output: 'server'` + adapter Node standalone gera `dist/server/entry.mjs`, que
// serve tanto as páginas pré-renderizadas (as desta fase, com `prerender = true`)
// quanto as SSR das próximas fases (que omitem o prerender). Astro removeu o
// modo 'hybrid'; este é o equivalente moderno (SSG por página, SSR opt-in).
export default defineConfig({
  output: 'server',
  adapter: node({ mode: 'standalone' }),
  integrations: [react()], // React islands para as próximas fases (dashboard/scan)
  server: { port: 4321, host: true },
  vite: {
    plugins: [tailwindcss()], // Tailwind v4 (CSS-first, via plugin Vite — igual ao frontend/)
    server: { allowedHosts: true }, // atrás do Nginx no Docker
  },
});
