# KL-64 — Analytics correto: filtro de bots + fix do funil de e-mails + export CSV

**Card:** KL-64 · **Prioridade:** Highest · **Data:** 2026-07-20

## Contexto

Analytics inflado levava a decisões erradas. Investigação em produção (VM, `email_log` +
`site_events` + MCP) revelou que **os 3 sintomas têm a MESMA raiz: tráfego não-humano** (pre-fetch
de servidores de e-mail crawleando os links dos alertas e os perfis públicos).

---

## Parte 1 — P0: o funil de e-mails (7.394 "hoje")

### Investigação (rodada na VM — a query do funil JÁ estava correta)

O card supunha "a query conta o mês como se fosse o período". **Não é o caso** — `aa_funnel_raw`
já filtra `sent_at >= start AND sent_at < end` (janela fechada). O `email_log` do dia:

```
 email_type      | status  | count |        first        |        last
-----------------+---------+-------+---------------------+---------------------
 profile_view    | sent    |  7095 | 2026-07-20 00:10:51 | 2026-07-20 17:04:20
 alert           | sent    |   298 | ...
 alert           | bounced |    29
 profile_view    | bounced |    17
 profile_view    | blocked |     6
 ...
counts: today=7456  month=24355  all_time=24355
profile_view por dia: 20/07=7118 · 19/07=901 · 18/07=899 · 17/07=3106 · 16/07=2479 · 14/07=6
```

**Causa real:** os **7.095 e-mails `profile_view`/dia** eram REAIS. `/site/[domain].astro` disparava
`POST /notify/profile-view` **no SSR, a cada render** — então cada BOT/crawler/pre-fetch que abria
um perfil gerava um e-mail ao dono (rate limit 1/domínio/24h). Com bots varrendo ~7.200 perfis/dia →
~7.000 e-mails/dia (risco de reputação + inflava o funil). O funil não estava "errado"; o VOLUME de
`profile_view` (bot) estava.

### Fix

- O gatilho do e-mail saiu do SSR. Agora o aviso "perfil consultado" nasce do **evento
  `profile_view` HUMANO-verificado** (`track.js` → `POST /api/events` com `verified_human`), tratado
  em `api_track_event` → `_profile_view_notify`. **Bots não interagem → não geram evento humano →
  não geram e-mail.** O SSR do perfil não chama mais `/notify/profile-view`.
- `aa_funnel_raw`: confirmado o bound superior (`sent_at < end`) na etapa `emails_sent` (+ teste).

**Efeito esperado:** `profile_view` cai de ~7.000/dia para as visitas humanas reais (centenas) →
`emails_sent` do funil passa a refletir a realidade (~alertas + profile_view humano).

---

## Parte 2 — P1: filtro de tráfego não-humano

### Tracker (`public/track.js`) — detecção de humano

Reescrito: **não dispara `page_view` no load.** Espera sinal humano (`scroll`/`click`/`mousemove`/
`touchstart`/`keydown`) **ou 5s com a aba visível**; só então drena os eventos passivos
(`page_view`/`profile_view`/`ranking_viewed`) com `verified_human:true`. Pre-fetches saem em < 1s sem
interagir → nunca contados. Eventos de AÇÃO (`scan_started`, `account_created`, …) disparam na hora,
carregando `verified_human` (o backend filtra). Continua privacy-first (sem cookies, session id por visita).

### Coluna + filtro

- `site_events.is_human BOOLEAN DEFAULT NULL` + índice parcial `idx_events_human WHERE is_human=true`
  (migration idempotente no `ensure_schema`). Eventos antigos ficam `NULL` (preservados).
- `verified_human` do tracker → `log_event(is_human=…)`.
- **Filtro padrão `(is_human = TRUE OR is_human IS NULL)` em TODAS as queries de `site_events`** dos
  8 endpoints (`aa_metrics_raw`, `aa_funnel_raw` etapas 2+, `aa_events`, `aa_sessions`, `aa_pages_raw`,
  `aa_journeys_raw`, `aa_funnel_by_sector`, `aa_events_export`). `users`/`alert_log`/`email_log` NÃO
  levam o filtro (não têm o campo). `IS NULL` preserva o histórico (retrocompatível).
- Toggle **"Incluir bots/pre-fetch"** no admin (default OFF) + `include_bots=true` nos endpoints e nas
  2 MCP tools (`get_analytics_metrics`, `get_analytics_funnel`).

**Efeito esperado:** visitantes únicos caem de ~4.221 para as centenas reais; pageviews/sessão sobe
de 1,01 (bots de 1 view) para > 1,5.

---

## Parte 3 — P2: export CSV server-side

`GET /admin/analytics/events/export` — `StreamingResponse` (`text/csv`), mesmos filtros da aba
Eventos + `is_human` (default só humanos), **cursor `fetchmany(1000)`**, teto **10.000** (busca
`limit+1`; se exceder → header `X-Truncated: true` + linha `# Exportacao limitada a 10000 registros`).
Colunas: `timestamp,event_type,page,domain,campaign,session_id,is_human,referrer`. Anti CSV-injection
(prefixa `= + - @`). Admin-only (prefixo `/admin` → JWT). Frontend: o botão usa `adminDownload`
(fetch com Bearer + blob + `Content-Disposition`) — não pagina client-side (antes travava com 5k+).

---

## Segurança
- Export admin-only (JWT Bearer); `is_human` é flag boolean (não PII).
- Anti CSV-injection nas células; streaming com teto de 10k (não `SELECT *` sem limite).
- Tracker continua sem cookies de tracking; `verified_human` é validado só como sinal (o e-mail
  profile_view mantém rate limit 1/domínio/24h + anti-loop `utm=alerta*` + skip de e-mail já com conta).

## Testes (26 novos)
- **Backend (19):** `test_kl64_analytics.py` (14 — clause `is_human` em cada `aa_*` via cursor que
  grava o SQL; bound do e-mail; `_human_and`; `/events` grava `is_human`; `profile_view` humano
  dispara o e-mail / bot não dispara; `_domain_from_site_path`) + `test_kl83_analytics.py` (+5 —
  `include_bots` passthrough; export 401/headers/conteúdo/truncamento/is_human default).
- **Frontend (7):** `web/src/lib/track.test.js` (`page_view` não no load; dispara após scroll/click;
  fallback 5s; ação dispara na hora; sem duplicar; `/painel` fora).
- **Total: 1307 backend + 74 frontend**, build Astro verde.

## Validação de sucesso
| # | Critério | Status |
|---|---|---|
| 1 | Funil coerente após cair o volume de profile_view (bot) | ✅ (gatilho humano) |
| 2 | Visitantes caem para só-humanos | ✅ (page_view humano-gated) |
| 3 | Pageviews/sessão sobe | ✅ (bots de 1 view eliminados) |
| 4 | Eventos novos com is_human true/false | ✅ |
| 5 | Histórico (is_human NULL) preservado | ✅ (filtro `OR IS NULL`) |
| 6 | Toggle "incluir bots" | ✅ |
| 7 | MCP filtra por is_human por padrão | ✅ (+`include_bots`) |
| 8 | CSV respeita filtros ativos | ✅ (server-side) |
| 9 | CSV 10k + aviso de truncamento | ✅ |
| 10 | ≥ 20 testes novos | ✅ (26) |
