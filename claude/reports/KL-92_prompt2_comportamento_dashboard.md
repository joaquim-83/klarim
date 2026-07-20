# KL-92 — Tracking Server-Side por IP (Prompt 2 de 2): Comportamento + Dashboard

**Card:** KL-92 · **Prioridade:** Highest · **Status:** Implementado (aguardando deploy verde)
**Data:** 2026-07-20 · **Depende de:** Prompt 1 ✅ (infra `access_log` em produção)

---

## 1. Objetivo

O Prompt 1 entregou a infraestrutura (`access_log` + middleware + classificação de bot + 3
endpoints). Este Prompt 2 extrai **inteligência de comportamento** do access_log e **migra o
dashboard Analytics** (aba Visão Geral) para usar o access_log como **fonte primária** das
métricas de visitante — em vez do `site_events`/tracker.js (inflado ~5x por pre-fetch de e-mail).

---

## 2. Parte 1 — Queries de comportamento (backend)

### 2.1 Novos métodos de store (`discovery/store.py`)

| Método | O que devolve |
|---|---|
| `al_server_funnel` | funil server-side (IPs distintos): visitante BR → viu perfil → iniciou scan → concluiu scan → criou conta → baixou PDF |
| `al_top_domains` | domínios mais consultados (domain, views, unique_ips, scans), teto 20 |
| `al_daily_series` | série diária (visitantes BR / scans / contas) para o gráfico de tendência |
| `al_hourly_heatmap` | volume humano por (dia-da-semana, hora) para o mapa de calor 7×24 |
| `al_pre_signup_journeys` | atividade de -24h a +7d em torno de cada signup |
| `al_retention` | retenção D1/D3/D7 pós-signup |

> ⚠️ **Decisão-chave (jornada + retenção chaveadas por IP, não por user_id):** no momento do
> `POST /account/signup` a conta ainda **não tem cookie** → o `user_id` do access_log é NULL. Por
> isso as CTEs de signup agrupam por **`ip_address`** (via `MIN(created_at)`), e o `user_id` é
> recolhido das requests **pós-signup** (que já carregam o cookie). O SQL do card (que agrupava por
> `user_id`) retornaria vazio — corrigido aqui.

Os endpoints de scan são **GET** (`/scan/result`, `/scan/summary`) — as queries **não** filtram por
POST (constantes `_AL_SCAN_LIKE`/`_AL_SCAN_DONE`).

### 2.2 Endpoints enriquecidos (`api/admin_analytics.py`)

- `GET /admin/analytics/server-metrics` (+ `server_funnel`, `top_domains`, `daily_series`,
  `hourly_heatmap`). Cache 5 min.
- `GET /admin/analytics/ip-behavior` (+ `pre_signup_journey`, `typical_journey`,
  `post_signup_retention`). Cache **10 min** (self-JOIN é mais pesado — `_cached` ganhou o param
  `ttl`).

Toda a **derivação é PURA** e unit-testada: `assemble_server_funnel` (conversões inter-etapa,
div/0 → None), `assemble_daily_series` (densifica dias sem dado → 0), `assemble_retention`
(pct por janela), `assemble_pre_signup_journeys` (agrupa por IP + jornada típica),
`assemble_hourly_heatmap` (grade 7×24 + máximo).

### 2.3 MCP tools

`get_server_metrics` agora omite `hourly_distribution` + `daily_series` + `hourly_heatmap`
(arrays grandes); `get_ip_behavior` omite a lista detalhada de jornadas (mantém `typical_journey`
+ retenção). Economia de tokens; o detalhe fica no painel.

---

## 3. Parte 2 — Migração do dashboard (`web/`)

### 3.1 Aba "Visão geral" — access_log como fonte primária

- **KPIs server-side:** Visitantes BR, Scans, Contas criadas, Bots filtrados, Conversão (do
  `server_funnel.overall`) — todos do `server-metrics`. "Clique em alertas" segue do tracker
  (`aaMetrics`). Cada card tem um **badge de fonte** `📡 server` / `📱 tracker`.
- **Fontes independentes:** `server-metrics`, `metrics` (tracker) e `funnel` (email) em `useAsync`
  **separados** — se uma falhar, as outras renderizam (cada card/bloco tem seu loading/erro).
- **Tendência** vem do `daily_series` do access_log (`dailySeriesToTrend`).
- **Toggle de funil email/server** (estado no hash `#overview?funnel=server`): "📱 email" (funil
  atual do `site_events`+`email_log`) vs "📡 server" (funil do `server_funnel`). Validação cruzada
  durante a transição.

### 3.2 Nova aba "Comportamento"

5 blocos (lazy — só busca quando a aba está ativa, com loading/erro independentes):

1. **Top domínios consultados** — tabela (domínio, views, IPs únicos, scans).
2. **Visitantes multi-site** — tabela com IP **mascarado**, país, sites, domínios.
3. **Jornada pré-signup** — jornada típica (1ª ação, passos, minutos até signup, % via alerta) +
   até 8 exemplos com os passos antes→signup→depois em chips.
4. **Retenção pós-signup** — barras horizontais D1/D3/D7 (%).
5. **Mapa de calor por hora** — grade 7×24 (dia-da-semana × hora), cor ∝ volume (`heatColor`).

### 3.3 Lógica pura testável (`web/src/lib/admin/analyticsUtils.js`)

`DATA_SOURCE`, `dailySeriesToTrend`, `sparkFromDaily`, `serverFunnelStages`, `retentionBars`,
`heatColor`, `DOW_LABELS` — sem React/DOM, testadas com `node --test`.

---

## 4. Testes

- **Backend:** +11 (`tests/test_kl92_access_log.py`) — funil/retenção/série/jornada/heatmap (derivações
  puras + shape dos endpoints + contrato dos 6 métodos de store). SQL validado na VM.
- **Frontend:** +11 (`web/src/lib/admin/analyticsUtils.test.js`, `node --test`) — DATA_SOURCE,
  trend, sparkline, funil server, retenção, heatColor, DOW.
- **Total novo: 22** (mínimo do card: 20).

```
tests/test_kl92_access_log.py ... 88 passed
web test:unit ................... 85 passed (node --test)
astro build ..................... Complete
```

---

## 5. Regras respeitadas

- **access_log = fonte primária** dos visitantes; `site_events`/tracker = complemento das
  interações frontend. As duas **coexistem** no dashboard.
- **IP mascarado** em todo componente (multi-site 1º octeto; ip-detail 2 octetos). LGPD.
- **Fetch paralelo** dos endpoints (server-metrics + metrics + funnel) com **loading independente**.
- **Toggle funil** com estado no hash da URL.
- Queries de comportamento (self-JOIN) com **cache 10 min**; `daily_series` limitada ao período
  (≤90d); `top_domains` limitado a 20.

---

## 6. Validação na VM (pós-deploy)

Como o access_log ainda está acumulando (populando desde o Prompt 1), os números crescem com o
tempo. Checklist:

1. Aba **Visão geral** carrega KPIs do `server-metrics` com badge 📡, Visitantes BR realista
   (não 4.000+).
2. **Toggle** email↔server no funil funciona (hash muda; os dois renderizam).
3. Aba **Comportamento** mostra top domínios, multi-site, jornada, retenção e heatmap
   (parcial no começo — o access_log é jovem).
4. Um endpoint falhar não zera os outros (loading independente).

---

## 7. Arquivos

**Alterados:** `discovery/store.py` (6 métodos), `api/admin_analytics.py` (5 derivações puras +
`_cached` ttl + 2 endpoints enriquecidos), `mcp_server/tools/analytics.py` (trim de tokens),
`web/src/lib/admin/adminApi.js` (2 métodos), `web/src/lib/admin/analyticsUtils.js` (7 utils),
`web/src/lib/admin/analyticsUtils.test.js` (+11), `web/src/components/admin/AdminAnalytics.jsx`
(Visão geral server-primary + aba Comportamento), `tests/test_kl92_access_log.py` (+11),
`CLAUDE.md`, `docs/API.md`.

---

## 8. Fecha o KL-92

Prompt 1 (infra) + Prompt 2 (comportamento + dashboard) completos. O tracking server-side por IP é
agora a fonte de verdade das métricas de visitante do Klarim.
