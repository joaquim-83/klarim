# KL-16 — Dashboard operacional + limpeza de dados de teste

- **Card Jira:** KL-16
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-14 (dashboard/JWT), KL-15 (Discovery/CT poller), KL-12/13 (Alert/Re-scan)
- **Commit:** `feat(KL-16): add operational dashboard with worker status, health checks, and activity log`

---

## Parte 0 — Limpeza de dados de teste (feita primeiro)

**Investigação (SELECT antes de deletar).** 11 cobranças, todas R$29. As 5 PAID:

| Critério | Simuladas (sandbox) | Real |
|----------|--------------------|------|
| `paid_at` | ~1s após `created_at` (instantâneo = `simulate_payment`) | **36s** (16:27:19→16:27:55) |
| URL | verdegreen, klarim.net×3 (self/teste) | pousadacostera.com.br |
| Comprador | vazio / operador | **cidinei@igoove.com** (humano real) |

**Removidas** (9): as 4 PAID simuladas (ids 1,2,6,7) + 5 PENDING de teste (klarim.net,
igoove.com/admin, example.com, example.org). **Preservado:** o par pousadacostera
(id 10 PAID real + id 8 PENDING). Resultado: `GET /api/payments/stats` →
**`R$ 29,00`, `paid_count: 1`**.

**`alert_log` preservado:** as 10 linhas são **alertas reais** enviados pelo funil
(scmengenharia, exataid, viboralabs…) — 0 alertas de teste (`klarimscan`/`jscidinei`).
`rescan_log` estava vazio. A limpeza foi **só de pagamentos**.

## Parte 1/3 — Heartbeat dos workers (`discovery/heartbeat.py`)

Cada worker publica `worker:<name>:status` no Redis com **TTL 600s**. Se o worker
morre, a chave expira → painel 🔴. Como os ciclos são de horas, cada worker roda um
**loop de heartbeat a cada 60s** (Alert, Re-scan) independente do ciclo; o Scan
Worker faz `blpop` com timeout 30s para bater o heartbeat mesmo com a fila vazia; o
Discovery reusa o `discovery:status` do KL-15 (TTL baixado 3600→600s).

## Parte 2 — Endpoints de status (JWT)

- **`GET /api/system/status`**: 4 workers (`alive` + últimos/próximos ciclos +
  stats: discovery source/descobertos hoje; alert enviados hoje/semana + teto;
  rescan hoje + elegíveis; scan fila/completados hoje/score médio) +
  `dependencies` + `email_metrics`.
- **`GET /api/system/activity?limit=`**: timeline intercalada (alertas, re-scans,
  scans, pagamentos) ordenada por data desc.

## Parte 4 — Health checks (`api/health_checks.py`)

`postgres` (SELECT 1 via `store.ping()`), `redis` (ping), `ct_logs` (lê
`discovery:status`), `resend` (GET /domains), `abacatepay` (GET /billing/list).
Cada um `{status, latency_ms, detail}`, timeout 5s, nunca levanta; `run_all` roda
os 5 em paralelo (`asyncio.gather`). Store novo: `ping`, `scan_today_stats`,
`count_rescan_eligible`, `email_metrics`; `rescan_stats` ganhou `today`.

## Parte 5 — Frontend (`/painel/sistema`)

Cards 🟢/🔴 por worker, grid de health das dependências, 4 cards de métricas de
e-mail e um log de atividade com badge por tipo. **Auto-refresh a cada 30s**
(`setInterval` no `useEffect`, limpo no unmount). Sidebar: item **Sistema** entre
Re-scans e Configurações. Code-split (lazy).

## Validação

- **Testes** (`tests/test_system.py`, 7 casos): health checks (resend/abacatepay
  `unknown` sem chave; redis ok/none; ct_logs streaming/disconnected/sem-heartbeat)
  + `publish_heartbeat` (chave `worker:<name>:status`, TTL, `alive`). **Suíte total:
  98 passed, 1 skipped.** Build do frontend OK (Sistema code-split).
- **Limpeza:** confirmada em produção — `payments/stats` = R$ 29,00 / 1 pago.
- **Produção (VM):** _status dos workers + tela — ver seção abaixo._

## Validação em produção (pós-deploy)

- [x] Limpeza: `payments/stats` → R$ 29,00, 1 pago.
- [ ] `GET /api/system/status`: 4 workers `alive:true`, dependências, e-mail.
- [ ] Worker morto (parar container discovery) → workers ficam 🔴 (TTL expira).
- [ ] `GET /api/system/activity` → últimas ações reais.

## Critérios de aceite

- [x] Dados de teste do sandbox limpos (só pagamento real).
- [x] `GET /api/system/status` (workers + dependências + e-mail).
- [x] Heartbeat no Redis (TTL 10min) nos 4 workers.
- [x] Health checks (PostgreSQL, Redis, CT logs, Resend, AbacatePay).
- [x] Tela "Sistema" (cards de status, health, log, métricas).
- [x] Worker morto → card vermelho (TTL).
- [x] Log de atividade (últimas 50).
- [x] Auto-refresh 30s.
- [x] Sidebar com "Sistema".
- [x] Documentação (`claude.md` §20, `README.md`).
- [x] Relatório em PT-BR.
- [ ] Deploy + validação em produção + commit/push.

## Follow-ups

- Health checks batem no Resend/AbacatePay a cada request de `/system/status`
  (polling 30s no painel) — se virar custo/latência, cachear o resultado por ~30s.
- Dívida do KL-3 (stores por `POSTGRES_*`) segue de pé.
