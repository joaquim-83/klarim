# Migração do remetente dos alertas para klarimscan.com (reputação + warmup)

**Prioridade:** URGENTE · **Data:** 2026-07-15
**Objetivo:** isolar a reputação de e-mail — os **alertas proativos (cold)** passam a sair
de `alerta@klarimscan.com` (domínio novo, verificado no Resend); os **transacionais**
(códigos, boas-vindas, vigílias, relatórios, recuperação) continuam em
`seguranca@klarim.net`. **Só o alert worker (+ a notificação de perfil consultado) usa o
domínio novo.**

## Parte 1 — Variáveis de ambiente (fail-safe)

`ALERT_FROM_EMAIL` / `ALERT_FROM_NAME` (+ `ALERT_DAILY_LIMIT`) no `.env.example` e no `.env`
da VM. **Resolução tolerante:** sem `ALERT_FROM_EMAIL`, o remetente proativo **cai para o
`RESEND_FROM` normal** — nada quebra.

## Parte 2 — Remetente separado no `KlarimMailer`

- **`_proactive_from()`** (novo): `"{ALERT_FROM_NAME} <{ALERT_FROM_EMAIL}>"`, com fallback
  para `self.from_address`. **Lido do env a cada envio** → trocar o remetente vale sem
  reiniciar.
- **PROATIVOS** (usam o novo domínio): `_alert_params` (usado por **`send_alert`** e
  **`send_alert_batch`**) e **`send_profile_view`** passaram a usar `_proactive_from()`.
- **TRANSACIONAIS** (inalterados, `self.from_address`): `send_verification_code`,
  `send_signup_verification_code`, `send_vigilia_alert`, `send_report`, `send_recovery_link`,
  `send_password_reset_code`, boas-vindas, etc.

## Parte 3 — Warmup: limite diário

- **`ALERT_DAILY_LIMIT`** (int, editável ao vivo no painel Config — KL-44). Default alto
  (`5000`) = sem limite prático; no `.env` da VM começa em **30** (semana 1).
- **Alert worker** (`run_cycle`): após a cota mensal, lê `daily_limit` (via `get_setting`) e
  `count_alerts_sent_today()`. Se `sent_today >= daily_limit` → **pula o ciclo**
  (`stats["daily_limit_reached"]`). O `want` do ciclo e o `room` de cada batch também são
  **limitados pelo restante diário** (`daily_limit - sent_today`), então nunca ultrapassa o
  teto — nem dentro de um único ciclo.
- **`store.count_alerts_sent_today()`** (novo): conta `email_log` `status='sent'` dos tipos
  `alert`/`alert_score100` no dia corrente.
- **Plano de warmup** (ajuste o valor no painel a cada semana): 30 → 100 → 300 → 1000 →
  3000. O painel Config já permite editar sem redeploy.

## Parte 4 — Templates

Rodapé de `alert.html` e `alert_score100.html`: `Klarim Scanner — klarimscan.com` (texto,
não link — o domínio é só de e-mail). Os CTAs e o **unsubscribe continuam em klarim.net**
(o sistema principal), como pede o card.

## Parte 5 — Webhook de bounce

**Nenhuma mudança necessária.** O `/webhooks/resend` identifica o e-mail pelo **`email_id`**
(id único do Resend, agnóstico ao domínio de envio) e marca `alert_log` + `email_log`. O
Resend envia webhooks para bounces/complaints de **qualquer** domínio verificado, então já
cobre klarimscan.com **e** klarim.net.

## Parte 6 — Email log

- `email_log` ganhou a coluna **`from_domain`** (`ALTER … ADD COLUMN IF NOT EXISTS`,
  idempotente — vale para a tabela já criada em prod).
- `KlarimMailer._send` e `_send_batch` extraem o domínio do campo `from`
  (`_domain_of_from`) e o gravam em **todo** log (sent/blocked/failed). Permite filtrar no
  painel por domínio de envio (klarimscan.com vs klarim.net).

## Parte 7 — Testes (`tests/test_alert_sender_migration.py`)

`_domain_of_from`; `_proactive_from` (env + fallback); alerta/perfil usam o remetente
**proativo**; código/cadastro/vigília usam o **transacional** (mesmo com o warmup ativo);
`from_domain` gravado no log; o limite diário **pula o ciclo** quando atingido e **limita o
fetch** ao restante diário; `ALERT_DAILY_LIMIT` está no catálogo editável.

## Parte 8 — Deploy (na VM)

```
ALERT_FROM_EMAIL=alerta@klarimscan.com
ALERT_FROM_NAME=Klarim Scanner
ALERT_DAILY_LIMIT=30
```
(adicionar ao `/opt/klarim/.env`) + `git push`. Após o deploy: `docker compose exec api
printenv ALERT_FROM_EMAIL` → `alerta@klarimscan.com`. **Não pausar o alert worker** — o
limite de 30/dia controla o volume automaticamente.

## Parte 9 — E-mail de teste

Disparar um alerta manual (ex.: target 8172 / `igoove.com` → `jscidinei@gmail.com`) e
conferir: remetente `alerta@klarimscan.com`, chegada na inbox (não spam), `email_id`
reportado.

## Regra inviolável

O **alert worker** (+ a notificação de perfil consultado) é o **único** componente no novo
domínio; todo o resto (transacionais) fica em `klarim.net`. O remetente proativo é
**fail-safe** (sem a var, usa o normal). O limite diário é editável ao vivo e **nunca**
estoura (limita fetch + batch). O unsubscribe permanece em `klarim.net`.
