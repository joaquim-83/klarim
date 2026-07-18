# KL-74 — Arquitetura de conteúdo navegável

**Card:** KL-74 · **Prioridade:** Highest · **Data:** 2026-07-18
**Dependências:** KL-55 ✅ · KL-65 ✅ · KL-44 P5 ✅ · KL-42 (rankings) ✅

---

## Objetivo

Transformar os 10.800+ perfis-ilha (`/site/{domain}`) num **ecossistema navegável e
mobile-first** que conduz ao scanner: índices de setor, ranking, vitrine dos melhores,
estatísticas e cross-linking entre perfis. Meta: pageviews/sessão 1,09 → ≥3;
visitante→lead ~1% → ≥3%.

**68% do tráfego é mobile** (Cloudflare Web Analytics) → tudo desenhado para 375px
primeiro, alvos de toque ≥44px, inputs ≥16px (evita zoom iOS), sem hover-only.

---

## Camada 1 — APIs públicas (`api/main.py` + `discovery/store.py`)

> **Nota de roteamento:** o Nginx faz `rewrite ^/api/(.*)$ /$1` — logo as rotas no
> FastAPI são **`/public/*`** (o Astro SSR chama `http://api:8000/public/...`; o browser
> chama `/api/public/...`).

| Endpoint | Cache | Store method | Retorno |
|---|---|---|---|
| `GET /public/sectors` | Redis 1h | `public_sector_index(min_count=10)` | setores ≥10 sites: `slug,name,count,avg_score,median_score,semaphore_distribution,score_100_count` |
| `GET /public/sector/{slug}?page&limit&sort` | Redis 1h | `public_sector_stats` + `public_sector_sites` + `public_sector_top_fails` + `public_score_100_sites` | benchmark + ranking paginado + top fails + vitrine score 100 + `pagination` |
| `GET /public/top-fails?sector&limit` | Redis 24h | `public_sector_top_fails` | `{check_name,fail_count,fail_pct,severity}` |
| `GET /public/related?domain&limit` | Redis 1h | `get_target_by_domain` + `public_related_sites` | sites do mesmo setor (exclui o domínio; completa com outros) |
| `GET /public/best` | Redis 1h | `public_score_100_sites` | sites score 100 agrupados por setor |
| `GET /public/stats` | Redis 1h | `public_platform_stats` + `all_sector_benchmarks` | total sites/scans/score 100, distribuição, setores mais seguros/oportunidade |

**Segurança/visibilidade** (regra inviolável): todas as queries usam a **mesma
visibilidade dos rankings KL-42** — `status IN ('scanned','alerted')`, `last_scan_score
IS NOT NULL`, `JOIN site_profile … COALESCE(public_visible, TRUE) = TRUE`. **Nunca**
expõem `contact_email`/CNPJ/WhatsApp. Badge ✓ só com `owner_verified` (via
`EXISTS(user_sites … is_owner)`). `sort` validado por allowlist antes de entrar na SQL.

**Rate limit** (`_public_content_guard`): **30/min por IP real**. Chamadas SSR internas
(container Astro → API, sem `X-Forwarded-For`) **não** contam — senão o IP único do
container estouraria o teto sob carga orgânica.

**Navegação contextual no perfil** (`/public/profile/{domain}`): adicionado o campo
`ranking = {position,total,sector,sector_label}` via `get_sector_position` (KL-42).

---

## Camada 2 — Páginas de exploração (Astro SSR, `prerender=false`)

| Página | Arquivo | SEO |
|---|---|---|
| `/setores` | `web/src/pages/setores.astro` | title + `ItemList` |
| `/setor/{slug}` | `web/src/pages/setor/[slug].astro` | `BreadcrumbList` + `ItemList`; `noindex` se <10 sites |
| `/melhores` | `web/src/pages/melhores.astro` | `ItemList` |
| `/estatisticas` | `web/src/pages/estatisticas.astro` | title/description ricos |

- **Mobile-first:** cards 1 coluna → `md:` 2 → `lg:` 3; ranking em **cards empilhados no
  mobile** e **tabela no `md:`**; barras de distribuição/progresso horizontais; paginação
  com botões ≥44px.
- **Sem ilhas React** — HTML puro no SSR: LCP baixo (alvo <2,5s em 4G) e zero risco de
  CSP. Os **contadores de `/estatisticas` são estáticos** (renderizados no servidor):
  a CSP pública proíbe `<script>` inline não-hasheado, então não há animação por JS — e
  os números já vêm prontos, o que ajuda o LCP. Isso **absorve o KL-72** (contadores em
  "tempo real") como números vivos por request, cacheados 1h.

---

## Camada 3 — Navegação contextual no perfil (`site/[domain].astro`)

- **Breadcrumb** (Início → Setores → {Setor} → {domínio}) + `BreadcrumbList` JSON-LD.
- **Posição no ranking** do setor exibida no card de benchmark ("Posição 3 de 40 …").
- **"Outros sites do setor"** — 8 cards de `/public/related` buscados **via SSR**
  (preferível a ilha React p/ SEO). Mobile: scroll horizontal (swipe, snap); `md:` grade
  2 col; `lg:` 4 col. Card inteiro clicável. Link "Ver todos → /setor/{slug}".

---

## Camada 4 — CTAs contextuais (`components/ScanCTA.astro`)

Componente reutilizável: input full-width + botão empilhados no mobile, **inline em
`sm:`**; alturas `h-12` (48px); `text-base` (16px); `active:scale-95`;
`autocapitalize/autocorrect off`. Funciona sem JS (`GET /scan?url=`, `form-action 'self'`).
Frases contextuais por página (setores/setor/melhores/estatísticas).

---

## Camada 5 — SEO / Nginx / sitemap / footer / testes / docs

- **Nginx** (`frontend/nginx/https.conf.template`): allowlist do Astro estendida para
  `^/(site|score|ranking|setores|setor|melhores|estatisticas|sitemap\.xml)(/|$)` — feito
  **antes** das páginas (lição do P6: sem isso, caem no fallback SPA Vite). Herança de
  headers de segurança preservada (`include` no location).
- **Sitemap** (`web/src/pages/sitemap.xml.js`): `+/setores`, `/melhores`, `/estatisticas`
  (estáticos) e `/setor/{slug}` por setor com ≥10 sites públicos (de `/public/sectors`).
- **Footer**: Setores · Melhores · Estatísticas (substitui o antigo "Rankings"; `/ranking`
  segue existindo e no sitemap).
- **Docs**: `docs/API.md` (endpoints), `docs/ARCHITECTURE.md` (camadas + cache), `CLAUDE.md`
  (card + estado).

---

## Testes

`tests/test_kl74_content.py` — **10 testes** (offline, `FakeStore`):

- índice de setores (forma + cache header), detalhe do setor (paginação, `privacy_score`
  int, `last_scan_date`, `description_short` truncado, top fails, vitrine 100),
  allowlist de `sort` (injeção → default, sem 500), top-fails (`sector` obrigatório → 422),
  related (exclui o próprio domínio), best (agrupado por setor), stats (mais
  seguros/oportunidade), perfil com `ranking` + sem `contact_email`, e **rate limit**
  (SSR interno isento; IP externo com `X-Forwarded-For` estoura 30/min).

Conftest: `_public_content_attempts` adicionado ao reset autouse.

**Resultado:** `pytest` → **985 passed, 1 skipped**. `npm run build` (Astro) → **verde**
(as 4 páginas SSR compilam sem erro).

---

## Decisões e desvios do rascunho do card

1. **SQL adaptado ao schema real:** o rascunho assumia `scans.created_at` e
   `targets.public_visible`; o código usa `targets.last_scan_score`/`last_scan_at`
   (denormalizados) + `site_profile.public_visible`, espelhando os rankings KL-42.
2. **Rotas `/public/*`** (não `/api/public/*`) no backend — o Nginx já strippa `/api/`.
3. **Rate limit isenta SSR interno** (sem `X-Forwarded-For`) — obrigatório, senão o IP
   único do container Astro derrubaria o serviço.
4. **Contadores estáticos** em `/estatisticas` (não animados por JS) — a CSP pública só
   admite 3 scripts inline hasheados; qualquer script novo seria bloqueado. Ganho de LCP.
5. **Endpoint extra `/public/best`** para alimentar `/melhores` de forma limpa.
6. **Cross-linking e related via SSR** (não ilha React) — melhor para SEO.
7. **Severidade dos top-fails** mantida em PT-BR nativa (`CRITICA/ALTA/MEDIA/BAIXA`), não
   o `"high"` do exemplo ilustrativo do card.
8. **Footer**: "Rankings → /ranking" trocado por Setores/Melhores/Estatísticas (intenção
   do card 5C "Rankings → /melhores"); `/ranking` permanece acessível e indexado.

---

## Analytics (KL-57 / follow-up KL-64)

Pageviews das novas páginas já são capturados pelo Cloudflare Web Analytics + `track.js`
(incluídos no `Base.astro`). Eventos dedicados (click-through nos cross-links, eficácia de
cada `ScanCTA` por página, funil setor→scan→conta) ficam para o **tracker do KL-64**
(pendente) — as páginas já têm âncoras estáveis para instrumentar.

---

## Como validar em produção (pós-deploy)

1. `GET https://klarim.net/api/public/sectors` → JSON com setores + `Cache-Control`.
2. Abrir `/setores`, `/setor/{slug-populoso}`, `/melhores`, `/estatisticas` em viewport
   375px → sem scroll horizontal; CTAs empilham.
3. Perfil `/site/{domain}` → breadcrumb, "Posição X de Y", "Outros sites do setor".
4. `flush scan:*`? **Não necessário** — nenhum check/scoring mudou; mas os caches
   `public:*` novos preenchem sozinhos (TTL 1–24h). Para forçar frescor imediato após o
   deploy: `redis-cli --scan --pattern 'public:*' | xargs redis-cli del`.
5. `sitemap.xml` deve listar `/setor/{slug}` + as 3 páginas novas.
