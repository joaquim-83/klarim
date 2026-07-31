# KL-131 + KL-132 — Sitemap dinâmico (43k+ perfis) + SEO programático

**Data:** 2026-07-31 · **Status:** ✅ código + testes + docs; **1911 pytest / 132 node**, `nginx -t`
OK (http + https renderizado), build Astro OK. **Deploy:** via commit/push (CI/CD).

---

## Investigação (o que o card supunha vs. a realidade)

- O card dizia "sitemap.xml estático, ~32 URLs, sem perfis". **Falso:** o sitemap Astro era
  **dinâmico** e servia **33.232 URLs** (perfis + setores) com `Content-Type: application/xml`.
- Problemas REAIS encontrados: (a) **`Cache-Control` duplicado** (max-age 3600 do Astro + 300 do
  nginx); (b) `sitemap-index.xml`/`sitemap-0.xml` caem no **fallback SPA** (HTML) — provável causa do
  "não pode ser indexado" se foi essa a URL submetida; (c) **1 arquivo gigante** gerado por SSR a cada
  request (lento → risco de timeout no fetch do Google). `robots.txt` sem `/dashboard/`.
- Decisões do dono: **(1) sitemap → FastAPI (index + sub-sitemaps)**; **(2) NÃO re-adicionar o Review
  JSON-LD** (o Search Console reprovou WebSite+Review em 17/07).

## KL-131 — Sitemap dinâmico (FastAPI)

- **Endpoints** (`api/main.py`): `/sitemap.xml` (**sitemapindex**: static + sectors + N páginas de
  perfis, N=ceil(total/10k)), `/sitemap-static.xml`, `/sitemap-sectors.xml` (`/setor/{slug}`, exclui
  'outro'), `/sitemap-profiles-{page:int}.xml` (≤10k perfis; page 1..1000, senão 404). Todos
  `application/xml`, **cache Redis 1h** (não cacheia vazio → fail-open). Escape de `&/<>`.
- **Store** (`discovery/store.py`): `count_visible_profiles` + `get_visible_profiles_for_sitemap(offset,
  limit)` — mesma elegibilidade do `list_public_profile_domains` mas **`ORDER BY domain`** (estável p/
  OFFSET; `last_scan_at` mudaria e embaralharia as páginas).
- **Nginx:** `location ~ ^/sitemap[A-Za-z0-9._-]*\.xml$` → FastAPI **sem strip de prefixo** (como
  `/remover`) em `http.conf` + `https.conf.template`; **UM só** `Cache-Control` (add_header no nginx, o
  FastAPI não emite). `sitemap\.xml` **removido** da allowlist do Astro. `web/src/pages/sitemap.xml.js`
  **deletado**. `nginx -t` valida http.conf + https renderizado.
- **robots.txt** (`web/public/robots.txt`): + `/dashboard/`, `/api/account/`, `/webhooks/`, `/remover`.

## KL-132 — SEO programático

- **`web/src/lib/seo.js`** (puro/testável): `profileTitle` → "**{empresa} é seguro? Score {score}/100 |
  Klarim**" (≤60, trunca o nome com `…`; capta "{empresa} é seguro/confiável"); `profileDescription` →
  score + **semáforo em texto** (verde=Excelente/amarelo=Atenção/vermelho=Crítico) + "**48 pontos**"
  (≤155); `formatDomainName` (`lotusforme.com.br`→`Lotusforme`). Ligados em `site/[domain].astro`.
- **JSON-LD:** mantém Organization + WebSite (site-wide) + BreadcrumbList (perfil). **NÃO** re-adiciona
  o Review em WebSite (reprovado 17/07). Páginas de setor (`setor/[slug].astro`) ganham **`CollectionPage`**
  (tipo Schema.org válido: name/description/url/numberOfItems).
- **Internal linking** ("Outros sites do setor {label}", KL-74, até 8 sites por score) e **canonical**
  (`Base.astro`, via `path`, sem www, https) **já existiam** — verificados.

## KL-57 (item 5)
As pageviews de perfil já carregam a **fonte** via `referrer` no `site_events`/`access_log` (KL-64/92) —
orgânico (Google) vs direto vs alerta (UTM). Sem código novo; o SEO deve aumentar a fatia orgânica.

## Testes
- **Backend** (`tests/test_kl131_sitemap.py`, +7): index é `<sitemapindex>` c/ N páginas, ≥1 página
  quando vazio, sub-sitemap de perfis (`<loc>/site/…`, `<lastmod>`, `&` escapado), page fora do
  intervalo → 404, setores exclui 'outro', static, fail-open em erro de store.
- **Frontend** (`web/src/lib/seo.test.js`, +8): título ≤60 c/ "é seguro?"+score, fallback p/ domínio,
  truncagem de nome longo, semáforo→texto, description ≤155 c/ score/semáforo/"48 pontos".
- `nginx -t` OK (http + https renderizado com cert dummy). `npm run build` OK. `pytest`: **1911 passed**.

## Validação pós-deploy
1. `curl -sI https://klarim.net/sitemap.xml` → **um só** `Content-Type: application/xml` e **um só**
   `Cache-Control`.
2. `curl -s https://klarim.net/sitemap.xml | grep '<sitemapindex'` → presente.
3. `curl -s https://klarim.net/sitemap-profiles-1.xml | grep -c '<url>'` → ~10.000.
4. `curl -sI https://klarim.net/sitemap-index.xml` → não mais HTML (cai no FastAPI: urlset vazio ou 404).
5. `curl -s https://klarim.net/robots.txt | grep dashboard` → presente.
6. Título de um perfil contém "é seguro?" + score; view-source tem BreadcrumbList (perfil) /
   CollectionPage (setor).
7. **Google Rich Results Test** num perfil e num setor → sem ERRO (Review NÃO presente, de propósito).
8. Re-submeter `https://klarim.net/sitemap.xml` no Search Console.

## Arquivos
- Backend: `api/main.py` (4 endpoints + helpers), `discovery/store.py` (2 métodos), `tests/test_kl131_sitemap.py`.
- Nginx/robots: `frontend/nginx/https.conf.template`, `frontend/nginx/http.conf`, `web/public/robots.txt`.
- Frontend: `web/src/lib/seo.js` (+ `.test.js`), `web/src/pages/site/[domain].astro`,
  `web/src/pages/setor/[slug].astro`, `web/package.json`. **Deletado:** `web/src/pages/sitemap.xml.js`.
- Docs: `CLAUDE.md`, `docs/API.md`, este relatório.
