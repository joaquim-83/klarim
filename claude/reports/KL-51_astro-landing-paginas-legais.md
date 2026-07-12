# KL-51 (fase 1) — Estrutura Astro + landing profissional + páginas legais

**Card:** KL-51 (fase 1 de ~4) · **Prioridade:** CRÍTICA (é a vitrine do negócio).
**Contexto:** o site público era um React SPA (Vite) sem SEO nem páginas legais. Esta
fase entrega a fundação **Astro** + a landing + Termos/Privacidade/Sobre, sem quebrar o
painel admin nem o fluxo de scan existente.

---

## Decisões (com o dono)

1. **Deploy só depois do batch** `enrich_all` (que roda no container `api`; um deploy
   recria os containers e o mataria). Tudo foi construído e testado localmente; o deploy
   fica para quando o batch fechar 500/500.
2. **Arquitetura de menor risco:** em vez de substituir o `frontend/` (Opção A do card),
   **mantive o Nginx** (serviço `web`) como front de TLS/segurança e **adicionei** um
   serviço `astro`. O Nginx faz proxy das rotas públicas novas → Astro e **continua
   servindo o build Vite** em `/painel` + o fluxo de scan existente. **O painel não muda.**

---

## O que foi entregue

### Projeto Astro (`web/`, novo)
- **Astro 7** (`output: 'server'` + `@astrojs/node` standalone → `dist/server/entry.mjs`;
  páginas desta fase com `prerender = true` = SSG). *Nota: o card pedia "Astro 6" e
  `output: 'hybrid'`; ambos estão desatualizados — a versão atual é a 7 e o modo hybrid
  foi removido. Usei o equivalente moderno.*
- **Tailwind v4** via `@tailwindcss/vite` (CSS-first, igual ao `frontend/`). *O card
  sugeria `@astrojs/tailwind`, que é para a v3 — usei o plugin v4 correto.*
- `@astrojs/react` já incluso para as *islands* das próximas fases.
- **Landing** (`index.astro`) com 6 blocos: hero (headline + `ScanInput` + contador
  "13.000+"), como funciona (3 passos), o que verificamos (48 checks em 6 camadas +
  nota OWASP/LGPD), benchmark (score médio 74 + CTA repetido), para quem, footer.
- **Legais/institucional:** `termos.astro`, `privacidade.astro` (LGPD), `sobre.astro` —
  via `Page.astro` (layout de conteúdo). Conteúdo fiel ao card.
- **Base.astro:** SEO completo (title, description, canonical, Open Graph, Twitter,
  theme-color, favicon). Dark-mode default, `lang="pt-BR"`, viewport. Fonte de sistema
  (sem Google Fonts). `public/`: `favicon.svg` (beacon) + `robots.txt`.
- **`ScanInput`** é um form progressivo `GET /scan` (funciona sem JS). O fluxo completo
  de scan (e-mail → código → progresso) é da fase 2 — por ora entrega a URL à rota
  existente, como o card autoriza.

### Docker / Nginx / CI
- **`docker-compose.yml`:** serviço `astro` (`build: ./web`, Node `:4321`, publicado só
  em `127.0.0.1:4321`); `web` (Nginx) ganhou `depends_on: astro`.
- **Nginx** (`http.conf` + `https.conf.template`, **só** no server block principal):
  `location = /`, `~ ^/(termos|privacidade|sobre|favicon\.svg|robots\.txt)` e `^~ /_astro/`
  → proxy ao `astro` com **resolver dinâmico** (mesmo padrão do `/api/`). **Preservados:**
  `location /` (SPA Vite), `/assets/`, `/api/`, `/mcp/`, os bloqueios de paths sensíveis,
  o subdomínio `painel.` e **todos os security headers** (repetidos no `^~ /_astro/`
  porque um `add_header` próprio quebra a herança).
- **`web/Dockerfile`** (multi-stage node:20-slim → `node dist/server/entry.mjs`),
  `web/.dockerignore`, `web/.gitignore` e um **`.dockerignore` na raiz** (exclui
  `frontend/`, `web/`, node_modules… do contexto da imagem Python — enxuta e não recriada
  quando só o Astro muda).
- **CI (`deploy.yml`):** 2 jobs novos que **bloqueiam o deploy** (`needs: [test,
  build-web, nginx-check]`): **`build-web`** (`npm ci` + `npm run build` do Astro) e
  **`nginx-check`** (`nginx -t` no `http.conf` e no `https.conf.template` renderizado com
  cert dummy). Isso é crítico: uma config Nginx inválida **derruba o site**, e os health
  checks do deploy batem em `api`/`astro` DIRETO (não pegariam o Nginx). `deploy.sh` ganhou
  um health check do Astro (`curl localhost:4321/`).

---

## Testes / validação

- **Build Astro:** `npm run build` OK → `dist/server/entry.mjs` + as 4 páginas
  pré-renderizadas (`index`, `termos`, `privacidade`, `sobre`) + CSS em `_astro/`.
- **Runtime:** o server standalone serve `/`, `/termos`, `/privacidade`, `/sobre`,
  `/robots.txt`, `/favicon.svg` (200), com `<title>`, viewport, OG e o dark bg.
- **Nginx:** validado no CI (`nginx -t`) — não pude rodar localmente (sem Docker na
  máquina); o job `nginx-check` é o gate antes do deploy.
- **Suíte Python:** inalterada (nada de backend mudou).

---

## Notas / pendências

- **E-mail nos textos legais:** o card citava `klarimscan@gmail.com` como controlador na
  Privacidade; usei `seguranca@klarim.net` (consistente com o projeto e mais profissional
  numa página pública). Recomendo consolidar um endereço @klarim.net oficial.
- **"Entrar"** no header aponta a `/painel/login` (único login existente) até a fase 5
  (contas públicas).
- **Fases seguintes:** fluxo de scan em Astro, contas/dashboard, perfis públicos + og:image,
  widget/card viral, rankings, e a migração do painel (ver o doc de arquitetura).

## Arquivos

- **Novos:** `web/` (projeto Astro completo), `.dockerignore` (raiz),
  `claude/reports/KL-51_astro-landing-paginas-legais.md`.
- **Editados:** `docker-compose.yml`, `frontend/nginx/http.conf`,
  `frontend/nginx/https.conf.template`, `deploy/deploy.sh`,
  `.github/workflows/deploy.yml`, `claude.md` (§39), `README.md`.
