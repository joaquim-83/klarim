# KL-103 — Landing: social proof (números ao vivo) + pills de setores

**Card:** KL-103 (High) · **Status:** ✅

## Nota de diagnóstico
O "estado atual" do card (H1 "Seu site é seguro?", texto "13.000+", seções "Como funciona / 6
camadas / Benchmark / Para quem") era um **snapshot antigo/cacheado**. A landing real já é a
versão minimalista do **KL-81**: H1 "Pesquise qualquer site.", **zero "13.000"**, **sem** as
seções abaixo do fold. Verificado no repo (`index.astro`) e ao vivo (`curl` → 0 ocorrências de
"13.000"/"Como funciona"/"Para quem"/"camadas"). Portanto **Parte 4 (remover seções) e a parte
"remover 13.000" da Parte 5 já estavam feitas**. Fiz Partes 1–3 + a atualização da meta.

## Entregue

### 1. `GET /public/stats` estendido (não um endpoint novo)
Já existia (`/public/stats`, KL-74, para `/estatisticas`) com cache Redis `public:stats` 1h + rate
limit 30/min (`_public_content_guard`). Estendido com 3 contadores agregados
(`store.public_landing_counts`, **só números, sem PII**), no MESMO cache:
- `sites_analyzed` = `COUNT(*) FROM targets WHERE status <> 'discovered'` (prod: ~49.951)
- `sectors` = `COUNT(DISTINCT sector) WHERE sector IS NOT NULL AND sector <> 'outro'` (~50)
- `public_profiles` = `COUNT(*) FROM site_profile WHERE public_visible = TRUE` (~27.525)

Consumido via `/api/public/stats` (o nginx `/api/` já proxia ao FastAPI — sem rota nova).

### 2. Stats bar (acima do fold)
Abaixo da tagline: `50.800+ sites analisados · 49 setores · 27.000+ perfis públicos`. **Fallback
ESTÁTICO no HTML** (SSG → número aparece antes do JS, bom p/ SEO/no-JS/1ª pintura);
`web/public/landing-stats.js` faz `fetch('/api/public/stats')` e atualiza os `<span data-stat>` com
o valor ao vivo; se falhar, o fallback permanece (sem erro). Formata `Intl.NumberFormat('pt-BR')`;
>1000 → centena inferior + "+"; setores exato. Cores theme-aware (token slate, KL-87).

### 3. Pills de setor
6 chips clicáveis (`/setor/{slug}`: tecnologia, ecommerce, consultoria, clinica, petshop,
imobiliaria) + "+43 setores →" (`/setores`). Chips `rounded-full` com borda sutil, hover laranja.
Hardcoded (mudam raramente). Clique rastreado: **`sector_pill_click`** (adicionado ao
`_KNOWN_EVENTS`) com o `sector` — via `window.klarimTrack` (evento de ação, `keepalive` sobrevive à
navegação). KL-57: alimenta a priorização de conteúdo/outreach por curiosidade de setor.

### 4. Meta-description
`meta`/`og`/`twitter` (prop única no `Base.astro`): "Pesquise qualquer site brasileiro. 50.000+
sites analisados em 49 setores. Relatório de segurança completo e gratuito em 30 segundos."

## Decisão técnica — vanilla vs React island
O card sugeria "React island (client:load)". Optei por **script vanilla externo** (`landing-stats.js`,
padrão dos `track.js`/`theme.js`/`header.js`): (a) **SSG-friendly** — o fallback já no HTML rende
instantâneo (island só renderiza após hidratar); (b) **CSP-safe** (`script-src 'self'`, sem hash);
(c) **~1KB** vs ~40KB do runtime React (a landing não tinha bundle JS — manter leve p/ o "< 2s").
Resultado funcional idêntico (stats ao vivo + fallback + tracking dos pills). `landing-stats.js` na
allowlist do nginx (http.conf + https.conf.template) + `?v=1` (gotcha KL-90).

## Segurança
`/public/stats` já é público/rate-limited; os 3 novos campos são **contadores agregados** — nenhum
`contact_email`/cnpj/whatsapp/detalhe de alvo (testado). Fetch same-origin (`/api/...`), sem CORS
aberto. O script vanilla é fail-safe (nunca quebra a landing).

## Testes
`test_kl103_landing.py` (+4): SQL do `public_landing_counts`, agregados-sem-PII, POST/PUT/DELETE
→ 405, `sector_pill_click` em `_KNOWN_EVENTS`. `test_kl74_content.py`: +assert dos 3 campos no
`/public/stats`. **Suite: 1631 passed.** Build Astro OK (landing com stats bar/pills, 0 "13.000",
nova meta). nginx validado no CI (`nginx -t`).

## Validação pós-deploy
`curl /api/public/stats | jq` → 3 campos > 0; `curl /` → 0× "13.000"; stats bar/pills sem scroll no
desktop; pill "Tecnologia" → `/setor/tecnologia`; "+43 setores" → `/setores`; seções antigas ausentes.
