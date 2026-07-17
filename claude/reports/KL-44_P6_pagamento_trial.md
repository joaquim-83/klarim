# KL-44 P6 — Pagamento PIX + upgrade/downgrade + expiração de trial + UX

**Card:** KL-44 (fase P6 — **última**, fecha o Guardião Digital)
**Prioridade:** Highest · **Dependências:** P1–P5 ✅, fix gestão de planos ✅
**Status:** ✅ Concluído — 955 testes passando, deploy pendente de push.

Decisão de produto confirmada: **trial expira → downgrade silencioso para Free** (Opção A):
vigílias avançadas desativam, limite cai para 1 site, **dados preservados**, sem bloqueio.

---

## Bloco 1 — Checkout PIX self-service

**Reuso da infraestrutura existente (KL-27):** o `payments/abacatepay.py` já faz PIX
**transparente** (`create_pix_charge` → `{id, brCode, brCodeBase64}`), não checkout
hospedado. Reusei isso — o card pedia `checkout_url`/redirect, mas a infra é QR
transparente (melhor UX, código provado). **Desvio documentado.**

- **Tabela `subscription_payments`** (nova, em `discovery/store.py`): `user_id, plan,
  amount, provider_charge_id, br_code, br_code_base64, status, paid_at, expires_at`.
  **Separada** da `payments` (compra de relatório, modelo `charge_id`/`target_url`) — as
  semânticas são diferentes; conflar seria pior. **Nunca guarda dado de cartão/PIX.**
- **`POST /account/upgrade`** `{plan}`: valida que é upgrade (`_PLAN_RANK`), cria a cobrança
  PIX (Pro 1900 / Agency 4900), grava o `subscription_payment` (pending) e devolve
  `{charge_id, br_code, br_code_base64}`. Rate limit 10/h/IP.
- **`POST /account/downgrade`** `{plan}`: `change_plan` + `_sync_user_vigilias` (desativa as
  vigílias que o novo plano não inclui); preserva sites/scans. Imediato, sem prorata.
- **`GET /account/upgrade/status?charge_id=`**: polling (o redirect pode chegar antes do
  webhook) — revalida na AbacatePay e ativa quando pago.
- **`GET /account/payments`**: histórico de pagamentos do usuário.

## Bloco 1B — Webhook idempotente

O **webhook único** `POST /webhooks/abacatepay` (KL-27) foi estendido: além da compra de
relatório, no evento `.paid`/`.completed` chama `_confirm_subscription_payment(charge_id)`
— **idempotente** (`mark_subscription_payment` só transiciona de `pending`), que ativa o
plano via `plans.activate_paid` (status `active`, `trial_ends_at=NULL`, `last_payment_at`)
+ `_sync_user_vigilias` + e-mail de confirmação. Evento `.expired` → marca o pagamento
`expired` (plano intacto, o usuário pode tentar de novo). Responde sempre 200.

## Bloco 2 — Expiração de trial

- **`discovery/trial_worker.py`** (novo, no `asyncio.gather` do discovery + `worker_control`):
  ciclo de 1 h, **age 1×/dia** às `TRIAL_HOUR_UTC` (6h UTC). Avisa **7 dias** e **1 dia**
  antes (`get_trials_expiring_in`), e no vencimento (`get_expired_trials`) faz
  **downgrade silencioso p/ Free** (`change_plan` + `disable_user_vigilias_except([])`) +
  e-mail. Flag `TRIAL_EXPIRATION_ENABLED`. (Convive com a expiração *lazy* já existente em
  `plans.get_subscription`, que cobre o enforcement na leitura.)
- **E-mails** (`notifier/email_client.py`): `send_trial_warning(days)` (7d/1d),
  `send_trial_expired`, `send_upgrade_confirmed` — **transacionais** (`seguranca@klarim.net`,
  Reply-To `scan@klarim.net`, plain text, registrados no `email_log`).

## Bloco 3 — Página de preços pública

`web/src/pages/planos.astro`: 3 cards (Free/Pro/Agency), **Pro em destaque** ("Mais
popular"), lista de features, FAQ ("o que acontece após o trial", "como funciona o
pagamento", "posso cancelar"). Link **"Planos"** no nav (`Header.astro`). Botões:
deslogado → `/cadastrar?plan=`; logado (script) → `/dashboard?upgrade=`.

## Bloco 4 — UX do plano no dashboard

`web/src/components/account/PlanSection.jsx` (substitui o card estático):
- **Trial:** contagem regressiva + botões de upgrade. **Pago:** plano + downgrade.
  **Free:** upgrade Pro/Agency.
- **Upgrade:** modal → `POST /account/upgrade` → **QR PIX + copia-e-cola** → **polling**
  `/account/upgrade/status` a cada 5s → "✅ confirmado".
- **`?upgrade=pro`** abre o modal pré-selecionado; **`?upgraded=1`** mostra "em
  processamento" e faz polling de `/account/subscription` até `active`.
- **Histórico de pagamentos** inline.

## Bloco 5 — Signup com plano + admin

- **Signup `?plan=pro|agency`**: `SignupBody.plan` → trial do plano escolhido
  (`create_subscription`); propagado pelo fluxo com código. Texto do form adaptado
  ("Comece seu trial Pro/Agency"). `cadastrar.astro` lê o `?plan=`.
- **Admin:** `/painel/pagamentos` ganhou o bloco "Assinaturas" (receita, pagos por plano,
  recentes) via `GET /payments/subscription-stats`. MCP `get_subscription_payment_stats`.

## Testes (`tests/test_kl44_p6_payment.py`, 15)

Upgrade cria cobrança PIX (AbacatePay mockado); rejeita mesmo/inferior/ inválido; exige
auth. `_confirm_subscription_payment` **idempotente** (ativa 1×). Poller ativa. Webhook
ativa a assinatura + marca `expired`. Downgrade muda plano/desce. Histórico.
`plans.activate_paid` zera o trial. Trial worker: downgrade de expirado + aviso 7d +
desligado. **Suite: 955 passed, 1 skipped.** MCP tool registrada.

## Regras invioláveis

Nenhum dado de cartão/PIX armazenado; webhook idempotente; downgrade preserva dados;
`contact_email` nunca exposto; rate limit Redis+fallback; e-mails transacionais
(`seguranca@`, Reply-To `scan@`).

## Desvios

1. **PIX transparente (QR) em vez de `checkout_url`/redirect** — reuso da infra KL-27
   (AbacatePay v2 transparente). Melhor UX, código provado.
2. **Tabela `subscription_payments` separada** da `payments` (relatório) — semânticas
   distintas; a antiga é `charge_id`/`target_url`, sem `user_id`/`plan`.

## Deploy

Sem migration manual (`ensure_schema` cria `subscription_payments`). Registrar o webhook
na AbacatePay apontando para `/webhooks/abacatepay?webhookSecret=<secret>` (evento de
pagamento). Testar checkout PIX end-to-end com chave sandbox (`simulate_payment`).
