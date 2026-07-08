# KL-21 — Tracking interno da jornada do lead (eventos + UTM + analytics no painel)

- **Card Jira:** KL-21
- **Data:** 2026-07-08
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-14 (dashboard), KL-17 (scans públicos), KL-8 (e-mail)
- **Commit:** `feat(KL-21): add internal lead journey tracking with funnel events, UTM, and analytics dashboard`

---

## Objetivo

Depois de 40 alertas, não sabíamos o que acontecia: quem clicou, escaneou, viu o
resultado, tentou pagar, desistiu. Agora rastreamos as 7 etapas do funil — **100%
interno** (sem GA4/terceiros), com tabela `site_events`, eventos do frontend, UTM
nos e-mails e uma tela de Analytics no painel.

## Parte 1/2 — Backend

- **Tabela `site_events`** (event_type, session_id, target_url/id, page_url,
  referrer, utm_*, metadata JSONB, created_at) + 5 índices.
- **`POST /api/events`** (público, sem JWT): valida o `event_type` (whitelist de 9),
  exige `session_id`, **rate limit 100/min por sessão** (janela deslizante
  in-memory), resolve `target_id` de `utm_content` (`target_<id>`), grava em
  **background** (`_spawn`) e responde `{ok:true}` imediato. Nunca bloqueia nem
  levanta.
- **Store:** `log_event`, `count_events_last_minute`, `analytics_funnel`,
  `analytics_abandoned`, `analytics_campaigns`, `analytics_pages`,
  `analytics_events`. Período (`today/7d/30d/total`) via whitelist de bounds
  (constantes seguras no SQL).
- **`GET /api/analytics/{funnel,abandoned,campaigns,pages,events}`** (JWT). Funil =
  `COUNT(DISTINCT session_id)` por etapa; topo (e-mails enviados) do `alert_log`.
  Carrinho abandonado = sessões com `payment_created` sem `payment_completed`
  (+ tempo no site = max-min dos eventos da sessão).

## Parte 3 — Frontend (`lib/tracker.js` + wiring)

- `session_id` (sessionStorage + `crypto.randomUUID`), UTM capturado na **1ª
  página** e persistido (some da URL ao navegar internamente), `trackEvent`
  fire-and-forget (`keepalive`, `.catch(()=>{})` — silencioso).
- **7 etapas:** `page_view` (App.jsx, cada rota pública, ignora `/painel`);
  `scan_started` (Landing), `scan_completed` (Scan), `result_viewed` + `cta_clicked`
  (Result), `payment_created` + `payment_completed` (Payment), `report_downloaded`
  (Report).

## Parte 4 — UTM nos e-mails (`notifier`)

`utm_result_link(url, campaign, target_id)`: alerta `utm_campaign=alerta`, evolução
`evolucao_<tipo>`, recuperação `recuperacao`; `utm_content=target_<id>` (ou o
domínio). `send_alert`/`send_evolution` ganharam `target_id`, threadado pelos
workers (`alert_worker`, `rescan_worker`) e pela API (`scan-and-report`).

## Parte 5/7 — Tela Analytics + sidebar

**`/painel/analytics`** (code-split): seletor de período; **funil de conversão**
(barras decrescentes + %); **carrinho abandonado** (sessão, site, valor, quando,
tempo no site); **atribuição por campanha** (cliques/scans/CTAs/pagos/conversão);
**páginas mais visitadas**; **timeline** dos últimos 50 eventos. Sidebar: item
**Analytics** entre Re-scans e Sistema.

## Validação

- **Testes** (`tests/test_events.py`, 6): `target_id` de `utm_content`; **rate
  limit** (100 aceitos, 101º bloqueado, por sessão) no helper e via endpoint;
  `POST /events` público, ignora tipo desconhecido/sem sessão; `/analytics/*`
  exige JWT. Fakes de e-mail dos workers ajustados ao `target_id`. **Suíte total:
  114 passed, 1 skipped.** Build do frontend OK (tela Analytics code-split).
- **Produção (VM):** _pós-deploy — ver abaixo._

## Validação em produção (pós-deploy — CI verde, deploy OK)

Simulei um funil (6 eventos, mesma sessão, UTM `alerta`/`target_1`) + rajada de
130 eventos numa sessão, consultei os endpoints com JWT e depois limpei os dados
sintéticos (`DELETE 106`).

- [x] `POST /api/events` grava em `site_events` (background, `{ok:true}` imediato).
- [x] Funil registra os eventos com o mesmo `session_id`. `/analytics/funnel`
      (hoje): `links_clicked:1, results_viewed:1, cta_clicked:1, payments_created:1`
      (topo `emails_sent:50` do `alert_log` real).
- [x] UTM gravado: `/analytics/campaigns` atribuiu à campanha `alerta`
      (`clicks:1, scans:1, ctas:1`); `target_id` resolvido de `utm_content`.
- [x] Carrinho abandonado: `/analytics/abandoned` listou a sessão (PIX sem
      pagamento) com `duration_seconds` calculado.
- [x] Rate limit: 130 eventos rápidos → **30 respostas `rate_limited`** e
      exatamente **100 linhas** gravadas na sessão (não 130).

## Critérios de aceite

- [x] Tabela `site_events` + índices.
- [x] `POST /api/events` público, assíncrono, rate limit 100/min/sessão.
- [x] Frontend dispara os 7 eventos do funil.
- [x] `tracker.js` (session_id, UTM capture/persist, fire-and-forget).
- [x] UTM nos e-mails (alerta, evolução, recuperação).
- [x] `target_id` resolvido de `utm_content`.
- [x] Tela Analytics (funil, abandonado, campanhas, páginas, timeline).
- [x] `/api/analytics/*` protegidos por JWT; período selecionável.
- [x] Sidebar com "Analytics".
- [x] Tracking silencioso (erro não quebra a UI).
- [x] Documentação (`claude.md` §22, `README.md`).
- [x] Relatório em PT-BR.
- [x] Deploy + validação + commit/push (CI verde, validado em produção).

## Follow-ups

- Rate limit é in-memory por processo (single uvicorn) — com múltiplos workers,
  vira 100/min por worker. Ok para anti-abuso; se precisar de precisão, mover para
  Redis.
- Follow-up de carrinho abandonado por e-mail (se houver contato) fica para depois.
