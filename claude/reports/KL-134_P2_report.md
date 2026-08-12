# KL-134 (Prompt 2/2) — Frontend das micro-ferramentas SEO

**Data:** 2026-08-12 · **Escopo:** 5 landing pages + índice + navegação + FAQ Schema.org + CTA.
**Sem deploy** — validado no `docker-compose.dev.yml`. **PRONTO PARA REVISÃO VISUAL.**

## Arquitetura
Cada ferramenta é uma landing page pública (SSG, `prerender=true`) que reusa **uma casca única**
(`layouts/ToolLayout.astro`) e monta **uma ilha React** (`client:load`) que chama o endpoint do P1,
renderiza o resultado inline (sem redirect) e o CTA para o scanner completo. Lógica pura isolada em
`web/src/lib/tools.js` (testável por `node --test`); os componentes a consomem.

## Arquivos criados
- **`web/src/lib/tools.js`** — `TOOLS` (metadados das 5), `buildToolUrl`, `parseToolError` (400/429/504/502),
  `formatScore` (cor verde/amarelo/vermelho), `gradeColor`/`lgpdGradeColor`/`statusMeta`,
  `groupTechByCategory`, `fullScanHref`, **`FAQS`** (3–5 perguntas por tool) + `toolBySlug`/`faqFor`.
- **`web/src/lib/tools.test.js`** — 8 testes (25 asserts) node --test.
- **`web/src/components/tools/ToolPage.jsx`** — ilha: input + "Verificar" + loading (spinner "Analisando…")
  + erro amigável + resultado + CTA. Mobile-first (empilha em 375px).
- **`web/src/components/tools/Results.jsx`** — 5 renderizadores (`SslResult`/`HeadersResult`/`LgpdResult`/
  `TechResult`/`EmailResult`) + dispatcher `ToolResult`.
- **`web/src/components/tools/ToolCta.jsx`** — CTA final ("86 verificações" → `/scan?url={domínio}` +
  "Outras ferramentas →").
- **`web/src/layouts/ToolLayout.astro`** — Base(SEO+JSON-LD) + Header + H1 + nav-entre-ferramentas
  (atual em destaque) + ilha + **FAQ** (accordion `<details>` CSP-safe) + **FAQPage JSON-LD** + Footer.
- **`web/src/pages/ferramentas/index.astro`** — índice com 5 cards (2 col desktop / 1 mobile) + ItemList JSON-LD.
- **5 páginas:** `verificar-ssl`, `verificar-headers`, `teste-lgpd`, `detectar-tecnologias`, `verificar-email`
  (cada uma só passa props à `ToolLayout`; SEO title/description conforme a spec).

## Arquivos alterados
- **`web/src/lib/nav.js`** — "Ferramentas" → `/ferramentas` em `EMPRESA_LINKS` (auto-propaga no Header, 3 pontos).
- **`web/src/lib/nav.test.js`** — assert de EMPRESA_LINKS atualizado (4→5) + teste de "Ferramentas".
- **`web/src/components/Footer.astro`** — "Ferramentas gratuitas" no grupo Produto.
- **`web/package.json`** — `tools.test.js` no `test:unit`.
- **`frontend/nginx/http.conf`** + **`frontend/nginx/https.conf.template`** — `ferramentas` na allowlist de
  conteúdo (senão cai no SPA fallback = 404 em produção; gotcha do claude.md).

## Decisões
- **Uma ilha por página, não uma por resultado:** props de Astro→ilha são serializáveis, então
  `ToolPage` recebe o `tool` (slug) e despacha internamente o renderizador certo (`ToolResult`).
- **CTA → `/scan?url={domínio}`** (não `/?url=`): a home submete a `/scan`, que é a rota que roda a
  análise COMPLETA. `/?url=` não pré-preenche nada. Documentado como desvio consciente da spec.
- **FAQ centralizada em `tools.js`** (`FAQS`): alimenta o accordion visual E o FAQPage JSON-LD (DRY) e
  torna testável "3–5 perguntas por tool" (spec test #8).
- **8 indicadores no LGPD** (a engine tem 8; a spec citava 7) + **disclaimer obrigatório** renderizado.

## Testes
- `npm run test:unit` → **246 passed** (inclui os 8 novos de `tools.test.js` + nav atualizado).
- `npm run build` → **OK**: as 6 páginas prerenderizam sem erro.

## Validação no browser (dev, Chrome) — todas passaram
| # | Verificação | Resultado |
|---|---|---|
| Index | `/ferramentas` 5 cards | ✅ grid SSL/Headers/LGPD/Tecnologias/Email |
| SSL | grade + checks + contexto + CTA | ✅ klarim.net → **A**, 59 dias, Google Trust Services, TLSv1.3; "Você sabia? 30,8% Cloudflare"; CTA "86 verificações" |
| Headers | N/7 | ✅ **6/7**, cada header com importância (ALTA/MÉDIA/baixa) + valor |
| LGPD | 8 indicadores + disclaimer | ✅ **6/8** "Parcialmente adequado"; 8 indicadores ✓/✕; disclaimer LGPD; 3 stats de contexto |
| Tech | tecnologias agrupadas | ✅ agrupado por SERVIDOR/CDN/SOCIAL/CMS/E-mail |
| Email | SPF/DKIM/DMARC/MX | ✅ **4/4**; SPF value real; "Seletor 'resend' encontrado" |
| CTA | link ao scanner completo | ✅ botão + "Outras ferramentas →" |
| Nav entre tools | atual em destaque | ✅ SSL·Headers·LGPD·Tecnologias·Email |
| Header | "Para empresas" inclui Ferramentas | ✅ (`ref_8` → /ferramentas) |
| Console | zero erro | ✅ nenhum erro/CSP em todas as páginas |
| SEO | FAQPage JSON-LD | ✅ presente no HTML (view-source) |

**Nota de método:** o `type` do automador ocasionalmente disparava antes da hidratação do React (input
controlado ainda vazio) — artefato de **timing de teste, não bug de produto**: `form_input` (que emite o
evento `input` que o React escuta) preencheu e submeteu de forma determinística em todas as tentativas, e
os 5 resultados renderizaram corretamente. Usuário real não encontra isso.

**Responsividade:** layout mobile-first por construção (`flex-col sm:flex-row`, `w-full sm:w-auto`,
`min-h-[44px]`, `grid-cols-1 sm:grid-cols-2`, inputs `text-base`/`h-12`) — mesmos tokens já validados
mobile no resto do site (KL-80/87). Não foi feito screenshot dedicado a 375px nesta sessão.

## Deploy (autorizado pelo dono) — 2026-08-12 ✅

Commit `6b49e51` → push `main` (`1e23363..6b49e51`). CI/CD **run #31652293384 — success**.
Gates locais pré-push: pytest **2378 passed**/1 skipped · test:unit **246** · build OK.

| Job (GitHub Actions) | Resultado |
|---|---|
| Test | ✓ |
| Build web (Astro) | ✓ |
| Nginx config check (`nginx -t` http + https render) | ✓ (allowlist `ferramentas` válida) |
| Deploy to GCP VM (SSH) | ✓ 3m47s |
| Security Gate (live, pós-deploy) | ✓ 25s |

**Verificações pós-deploy em https://klarim.net:**
- `GET /api/tools/stats` → **200** (`total_sites 116049`, `wordpress_pct 20.2`, `privacy.scanned 19846` — agregados ao vivo batem com os números de referência).
- `GET /api/tools/ssl?url=klarim.net` → **200**, `valid:true`, grade **A**, Google Trust Services, TLSv1.3.
- `GET /api/tools/headers?url=klarim.net` → **200**, `6/7`. · `GET /api/tools/email?domain=klarim.net` → **200**, `4/4` (SPF/DKIM/DMARC/MX pass).
- `GET /ferramentas/` → **200** · `GET /ferramentas/verificar-ssl` → **200** · `/ferramentas/teste-lgpd` renderiza o H1 "Teste de Conformidade LGPD" + **FAQPage** JSON-LD (allowlist nginx OK, sem SPA fallback).

Nenhum step falhou. Deploy concluído e verificado. Registro em `claude/DEPLOY_HISTORY.md`.
