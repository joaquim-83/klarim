# KL-100 — Página pública `/metodologia`

**Card:** KL-100 (High) · **Status:** ✅

## Objetivo
Publicar a transparência da varredura passiva da Klarim: o que faz, o que NÃO faz, a base
legal e os direitos do dono do site. Reforça a conformidade (empresas de cibersegurança
sondam a plataforma — precisamos ser explícitos e defensáveis).

## Entregue
- **`web/src/pages/metodologia.astro`** (Astro **SSG**, `prerender=true`, layout `Page.astro`) com as
  **6 seções**: (1) o que a Klarim faz · (2) o que NÃO faz · (3) base legal (Art. 154-A CP
  Lei 12.737, Marco Civil Lei 12.965, LGPD Art. 7º IX Lei 13.709 + precedentes Shodan/Censys/
  Shadowserver; **links planalto.gov.br em nova aba** `target="_blank" rel="noopener noreferrer"`) ·
  (4) dados consultados (headers/DNS/SSL-CT/HTML público) · (5) direitos (opt-out/consulta/
  contestação) · (6) identificação do scanner (`Klarim Scanner`, 1 req/s, só GET/HEAD). Linguagem
  acessível, não jurídica.
- **Footer** (`web/src/components/Footer.astro`, componente compartilhado): link "Metodologia" →
  aparece em **todas as páginas** que usam o `<Footer />`.
- **Sitemap** (`web/src/pages/sitemap.xml.js`): `/metodologia` nos `staticUrls`.
- **Nginx allowlist**: `metodologia` adicionado às regras de rota Astro (`http.conf` +
  `https.conf.template`) — senão cairia no fallback SPA (KL-90 gotcha).
- **Templates cold**: linha de referência (TEXTO, não link) "Saiba mais sobre nossa metodologia:
  klarim.net/metodologia" no rodapé das 3 variantes de alerta (`cold_alert._SIGNATURE_*`) + do
  profile_view (`build_profile_view_text`). Continua plain text sem links clicáveis.

## Backend
Nenhum endpoint novo (100% estático). Página gera pageview trackável (access_log server-side
existente, KL-92) — quem consulta `/metodologia` (leads pós-alerta? técnicos?) fica no funil.

## Validação
`npm run build` OK → `dist/client/metodologia/index.html` (14 KB) com as 6 seções, links das leis
e "Klarim Scanner". Footer com `/metodologia` presente nas páginas construídas. Sitemap atualizado.
Pós-deploy: `curl https://klarim.net/metodologia` → 200; footer de `/` e `/scan` com o link.
