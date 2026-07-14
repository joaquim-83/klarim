# KL-42 — Score social: widget + card + ranking + selo + posição

**Status:** implementado, testes KL-42 (16) + regressão (21) verdes local; build/full-suite
validados no CI. **Data:** 2026-07-14

Cinco mecânicas de viralidade (fase 8 da arquitetura de experiência) que transformam cada
usuário/site num canal de aquisição. Tudo **público** (fora dos prefixos protegidos) e
derivado do score que o site já tem — **sem campo novo no banco**. Regra de linguagem
preservada: o Klarim avalia a segurança do **SITE**, não do negócio.

---

## Selo/badge (base compartilhada)
`_score_badge(score)` (backend) + `web/src/lib/badge.js` (front) espelham a mesma regra:
**≥90 Klarim Verified ⭐**, **≥80 Klarim Approved ✅**, **<80 sem selo**. Sem coluna nova —
derivado do score. Aparece no widget, card, perfil, ranking e dashboard.

## 1. Widget embeddable "Verificado por Klarim"
- **`GET /widget/{dominio}.js`** (`application/javascript`, cache 1h): JS leve, self-contained,
  CSS inline, domínio embutido; o estilo (`inline`/`card`/`minimal`) vem do `?style=` do
  próprio `<script>`. Busca o score em **`GET /score/{dominio}`** (JSON, cache 24h, **CORS
  `*`** — dado público sem cookie) e injeta o selo antes da tag. Link → `/site/{dominio}
  ?utm_source=widget`.
- **`GET /widget/event?e=&d=&s=`** (204): beacon de impressão/clique via **pixel GET**
  (cross-origin sem CORS) → `log_event` (`widget_loaded`/`widget_clicked`).
- **Página `/dashboard/widget`** (`WidgetGenerator.jsx`): seleção de site + estilo + preview
  fiel + snippet `<script async …>` + copiar (evento `widget_copied`).
- Leve, **async/defer**, não impacta a performance do site externo.

## 2. Card compartilhável
- **`GET /card/{dominio}.png?format=square|landscape`** (SVG→PNG via cairosvg, reusa a infra
  do og:image; cache 24h; fail-open → favicon): **square 1080×1080** (Instagram), **landscape
  1200×630** (LinkedIn/Twitter), com o CTA "Nosso site tem score X… E o seu?".
- **`ShareScore.jsx`** (no `SiteDetail` e no resultado do `ScanFlow`): selo + posição no
  ranking + preview + download (square/landscape) + copiar link + WhatsApp/LinkedIn/Twitter
  (share URLs nativas). Eventos `card_downloaded`/`share_clicked`.

## 3. Rankings por setor (SEO)
- **`GET /ranking`** (setores com ≥5 sites: contagem, média, top site) e **`GET
  /ranking/{setor}`** (top 20 por score). Só sites com scan público (`scanned`/`alerted`) **e
  landing ligada** (`public_visible`, KL-56); usa `targets.sector` (48, KL-54).
- **Astro SSR** `pages/ranking/index.astro` + `ranking/[sector].astro` (indexável só com ≥5
  sites; JSON-LD `ItemList`/`CollectionPage`; CTA de scan). Adicionados ao **`sitemap.xml`**
  (1 URL/setor ≥5). `track.js` dispara `ranking_viewed`. Link "Rankings" no footer.

## 4. Posição no ranking (dashboard)
- **`GET /account/sites/{id}`** ganhou `badge` + `ranking` (`store.get_sector_position`:
  `ROW_NUMBER()` no setor, ranqueia entre TODOS os sites com score — a posição é do dono).
  `SiteDetail.jsx` mostra "#N de M sites de {setor} · acima de X%" + selo + `ShareScore`.
  `Dashboard.jsx` mostra o selo no `SiteCard` + links Compartilhar/Widget.

## 5. Notificação de mudança de posição
- Preparado no fluxo (a posição já aparece no dashboard); a linha de ranking no e-mail de
  evolução mensal fica como **futuro** (o card marca isso como opcional).

---

## Store (KL-42)
`list_sector_ranking(sector, limit)`, `ranking_sectors_summary(min_count)` (com top domínio
por setor), `get_sector_position(sector, target_id)`.

## Nginx
`ranking` entrou no bloco cacheável do Astro (`^/(site|score|ranking|sitemap\.xml)`, 300s) no
`https.conf.template`. `/api/widget|score|card|ranking` caem no `/api/` existente;
`/dashboard/widget` já cai no `dashboard` da regex Astro. Validado pelo `nginx-check` do CI.

## Eventos (KL-21/57)
`widget_loaded`, `widget_clicked`, `widget_copied`, `card_downloaded`, `share_clicked`,
`ranking_viewed` — engenharia de dados: instalações, impressões, cliques, downloads,
share por rede, pageviews de ranking.

## Testes
`tests/test_kl42_social.py` (16, offline): selo (verified/approved/none), `/score` (JSON +
CORS + cache + oculto/descartado → null), widget JS (content-type + domínio + texto) +
beacon 204, `_card_svg` (dimensões 1080/1200) + endpoint png/fallback, `/ranking` +
`/ranking/{setor}` (posições + selo por score), `/account/sites/{id}` (badge + ranking +
percentil), eventos registrados. Regressão (`test_kl51_f4_profiles`, `test_events`,
`test_system`): 21 passed. Full-suite + build-web validados no CI.

## Regra inviolável
Widget/card/score/ranking são **100% dados públicos** (score + domínio, nunca
e-mail/CNPJ/WhatsApp) e respeitam a visibilidade (`public_visible`/descartado) — `/score` de
site oculto devolve `score: null`. O widget é leve/async/CSS-inline (não pode impactar o site
externo). O card é best-effort/fail-open. O Klarim avalia a segurança do **SITE**, não do
negócio.
