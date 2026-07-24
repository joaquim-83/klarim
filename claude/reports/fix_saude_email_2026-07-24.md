# Fix operacional — Saúde de e-mail (24/07/2026)

**Tipo:** fix operacional (sem card KL) · **Status:** ✅

Diagnóstico de produção (cruzando o CSV do Resend com o `email_log`) apontou 4 problemas na
infraestrutura de e-mail. Todos corrigidos, testados e o SQL validado no Postgres 16 da VM.

## 1. Health check probe (401 nos logs do Resend)
`check_resend` chamava `GET https://api.resend.com/domains` com a key **send-only** → 401 repetido
a cada ciclo (ruído nos logs do Resend, não break). **Fix:** `api/health_checks.py::check_resend`
faz um **HEAD ao host `https://api.resend.com` SEM Authorization** — prova a conectividade TLS/rede
sem consumir permissão nem gerar auth-failure. Qualquer resposta < 500 = no ar.

## 2. Contadores do admin escondiam bounces
"Alertas enviados HOJE" mostrava 289 (só `status='sent'`), enquanto o DB tinha 311 (289 sent + 22
bounced). **Fix:** `discovery/store.py::_email_stats_fn` agora conta **tentativas** que chegaram ao
Resend (sent+bounced+`soft_bounced`+complained; `blocked` não conta — nunca saiu) e devolve breakdown
`{key}_sent`/`{key}_bounced` por janela. `AlertasPage` mostra o total com sub-indicador
"**22 bounced ⚠️**" no `StatCard` (alertas e consultas de perfil). Validado na VM: hoje = 311/289/22.

## 3. Webhook perdia bounces transitórios
O handler `/webhooks/resend` marcava só bounce PERMANENTE (`bounced` + descarta + blocklist) e
**ignorava** os transitórios (`transient`/`soft`/`temporary`/`delivery_delayed`) com um mero `print`
→ o evento sumia (gap 78 Resend vs 25 DB). **Fix:** transitório agora é **`soft_bounced`** no
`email_log` (o operador vê; o circuit breaker conta) **sem descartar o alvo nem blocklist** (pode ser
caixa cheia temporária). `email_log.status` é texto livre (sem CHECK) → sem migração. Ambos os ramos
**logam quando o `email_id` do Resend não casa** com `email_log.email_id` (diagnostica o update
silencioso — a outra causa provável do gap).

## 4. Circuit breaker agressivo demais no warmup
`aviso.klarim.net` pausado com só **34 envios** (6 bounces = 17,6%): com `min_sample=20`, qualquer
remetente em warmup é pausado por poucos bounces antes de ter amostra significativa. **Fix:**
- `notifier/cold_alert.py::DEFAULT_BOUNCE_MIN_SAMPLE` **20 → 100**;
- amostra própria do circuit breaker por remetente (`ALERT_SENDER_BOUNCE_MIN_SAMPLE`=100),
  **separada** do safety net global do KL-24 (`ALERT_BOUNCE_MIN_SAMPLE`=20, all-time, 8%) — o global
  não foi afrouxado;
- `email_health_by_domain(days=7)` — **janela móvel de 7 dias** (o worker passa `days=7`); o
  `get_email_health` do admin segue all-time (`days=None`). Bounces antigos saem do cálculo → o
  remetente se recupera após corrigir a lista.

**Validado na VM (janela de 7d):** `aviso.klarim.net` = 34 envios (< 100) → **NÃO é mais pausado**
(amostra insuficiente) → volta a rotacionar. `alertas.klarim.net` = 353 envios / 22 bounces (6,2%,
amostra ≥100) segue pausado corretamente (bounce genuinamente acima de 5% — precisa limpar a lista;
com a janela, se recupera à medida que bounces > 7d saem e sends limpos entram).

## Segurança
Webhook continua validando a assinatura Svix (401 se inválida). Health check não expõe dado sensível
(HEAD sem auth). Contadores só agregam do `email_log` (sem PII nova).

## Testes (+4 líquido)
- `test_system.py`: `check_resend` não bate em `/domains` e não manda Authorization.
- `test_kl91_cold_alert.py`: `DEFAULT_BOUNCE_MIN_SAMPLE==100`; warmup (50 envios/5 bounces) NÃO pausa;
  amostra cheia (100/6) pausa.
- `test_bounce.py`: transitório → `soft_bounced` no `email_log`, sem descartar nem blocklist.
- `test_kl96_counters.py`: `alert_stats`/`profile_view_stats` devolvem tentativas + `_sent`/`_bounced`.
- `test_alert_worker.py` / `test_alert_sender_migration.py`: FakeStore aceita `email_health_by_domain(days=)`
  + `sender_bounce_min_sample`.
- **Suite: 1670 backend passed** + 108 `node --test`; build OK. SQL validado no Postgres 16 da VM.

## Validação pós-deploy
1. Logs Resend: `GET /domains → 401` some (após ~1h).
2. `get_system_status` → Resend ok sem "HTTP 401".
3. Admin "Alertas enviados HOJE" → total com "N bounced ⚠️".
4. Bounce transitório (se ocorrer) → `email_log.status='soft_bounced'`.
5. Próximo ciclo do alert worker → `aviso.klarim.net` volta a `active` e a rotação round-robin envia por ele.
