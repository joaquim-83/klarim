# KL-83 — Redesign do Analytics Admin (Prompt 1 de 2)

**Card:** KL-83 · **Prioridade:** High · **Data:** 2026-07-19
**Escopo do Prompt 1:** backend completo (8 endpoints) + frontend das Abas 1 (Visão geral) e
2 (Eventos) + 2 MCP tools. Abas 3 (Páginas) e 4 (Jornadas) ficam "Em breve" no front (backend
já pronto). É **admin-only** (painel operator-only, noindex) → risco de produção baixo.

---

## Decisão de arquitetura (a chave para testar SQL offline)

O código offline usa `FakeStore` — não roda SQL. Para ter os cálculos **testáveis** e a SQL
**validada em prod** (como os `analytics_*` legados, que não têm teste unitário):
- **Agregações BRUTAS** (contagens, séries diárias, linhas filtradas, sequências de sessão) →
  `discovery/store.py`, métodos `aa_*` — SQL parametrizada (bounds `start`/`end` como params,
  **nunca** interpolação de input).
- **DERIVAÇÃO** (validação de período, %, sparkline, conversão inter-etapa, gargalo,
  normalização de jornada, bounce/next_page/conversion, paginação) → `api/admin_analytics.py`,
  funções **puras** → 34 testes unitários offline.

## Backend — `api/admin_analytics.py` (módulo dedicado, não toca o KL-21)

8 endpoints sob `/admin/analytics/*` (o prefixo `/admin` já é protegido pelo middleware admin
JWT). Cada um: valida período (`today/7d/30d/90d/custom`, ≤90d, sem futuro → 422), rate limit
**30/min/IP** (`_redis_allow` + `CF-Connecting-IP`), cache Redis **5 min** (`_cache_get/_set`;
**events/sessions não cacheiam** — paginação/tempo real):

1. **metrics** — 6 KPIs com `value/previous/change_pct/sparkline` (visitors, scans_manual,
   accounts, conversion_rate, pageviews_per_session, alert_click_rate). Previous = período
   anterior de mesmo tamanho.
2. **trend** — séries diárias (visitors/scans/accounts) para o gráfico.
3. **funnel** — 7 etapas (`emails_sent`→…→`payment_completed`), `by_campaign` (email_log por
   tipo na etapa 1; site_events por `utm_campaign` nas demais, DISTINCT session_id),
   `conversion_from_previous`, **gargalo** (menor conversão), e comparação com o período anterior.
4. **events** — paginado, filtros AND (`type` multi, `domain`, `campaign`, `path`) + contadores
   (events/sessions/domains/scans/accounts) dos resultados filtrados.
5. **sessions** — eventos agrupados por sessão (converted, duration).
6. **pages** — views/sessions/**bounce_rate**/**next_page**/**conversion**/**delta_views** +
   grupos (Perfis/Setores/Scans/…). Derivação a partir das sequências de sessão.
7. **journeys** — top caminhos normalizados (`/site/{domain}`, `/setor/{slug}`, prefixo
   `alerta`, sufixo `[saiu]`), agrupados por sequência.
8. **funnel-by-sector** — join `site_events.target_id → targets.sector`.

**3 índices novos** em `site_events` (na migration `ensure_schema`): `(event_type, created_at)`,
`(session_id, created_at) WHERE session_id NOT NULL`, `(page_url, created_at) WHERE
event_type='page_view'`.

**Coluna confirmada:** `page_url` (não `page`); `created_at` é `TIMESTAMP` naïve.

## Frontend — `AdminAnalytics.jsx` (substitui `AnalyticsPage.jsx`)

Ilha React `client:only="react"` em `painel/analytics.astro`. Abas por hash (#overview/#events;
#pages/#journeys = badge "Em breve"). Seletor de período global. Admin sempre dark (tokens
`klarim-*`). Recharts (^2.15.4, já no `web/package.json`).
- **Aba 1:** 6 cards KPI (valor + Δ% verde/vermelho + sparkline Recharts) em grid 3×2/2×3/1×6;
  gráfico de tendência (LineChart 3 séries, legenda); funil (barras horizontais segmentadas por
  campanha, largura ∝ total, label de conversão inter-etapa, borda vermelha no gargalo).
- **Aba 2:** filtros (tipo multi-select, domínio/campanha/página com debounce 300ms), contadores
  dinâmicos, toggle "Agrupar por sessão" (cards colapsáveis com timeline + badge Converteu/
  Abandonou), tabela paginada (25/50/100), **Exportar CSV** (busca todas as páginas filtradas,
  aviso se >5000, download client-side).

## MCP tools (2)

`get_analytics_metrics` (6 KPIs **sem sparkline** — economia de tokens) e `get_analytics_funnel`
(funil + by_campaign + gargalo). Chamam as funções do módulo com `request=None` (rate limit é
no-op sem request). Registradas em `mcp_server/tools/analytics.py`; `test_mcp_server` atualizado.

## Testes

- **`tests/test_kl83_analytics.py` (34):** validação de período (7d/30d/90d/today/custom;
  >90d/futuro/datas inválidas/end<start/missing → 422); `pct_change`/`day_list`/`normalize_path`/
  `_page_group`; `assemble_metrics` (conversion/pv-sessão/alert-rate/change/zero-safe);
  `assemble_funnel` (conversão + gargalo); `assemble_journeys` (agrupamento + alerta + [saiu]);
  `assemble_pages` (bounce/next/conversion/delta/grupos); endpoints (auth 401; shape das 6
  métricas; trend; funil; events paginação+contadores+filtros; sessions; journeys/pages/sector;
  limits/page inválidos → 422; custom no endpoint; **cache hit** — 2ª chamada não recomputa).
- `test_mcp_server`: +2 tools.
- **Suite:** `1103 passed, 1 skipped` (1069 → 1103). Build Astro **verde**.

## Segurança

Admin-only (middleware `/admin`); validação de período (≤90d, sem futuro); inputs de texto
sanitizados (`_clean_text`: alfanumérico + `-._/`) e passados como **params LIKE** (nunca
interpolados); rate limit 30/min/IP; `LIMIT`/`OFFSET` em todas as queries; `contact_email`
nunca retornado.

## Prompt 2 (deferido)

Frontend das abas **Páginas** e **Jornadas** (endpoints `/pages` e `/journeys` já prontos e
testados). Possíveis extras: seletor de período custom (date range) no front, drill-down.
