# KL-7 — Integração de pagamento AbacatePay (PIX + webhook + liberação de PDF)

- **Card Jira:** KL-7
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-5 (web), KL-6 (HTTPS — obrigatório p/ webhook)
- **Domínio:** `klarim.net`
- **Commit:** `feat(KL-7): add AbacatePay PIX payment integration`

---

## Objetivo

Cobrar **R$ 29 via PIX** para liberar o relatório completo: semáforo grátis →
QR code PIX → polling → pago → download dos PDFs, com webhook de confirmação.

---

## Backend — `payments/`

- **`abacatepay.py`** — client httpx da AbacatePay **v2** (`Authorization: Bearer`,
  valores em centavos, respostas `{data, success, error}`). Métodos:
  `create_pix_charge`, `check_payment`, `create_webhook`, `simulate_payment`
  (dev), e `verify_webhook_signature` (HMAC-SHA256). Timeout 15s, retry/backoff
  em 5xx.
- **`store.py`** — persistência de cobranças em **PostgreSQL** (tabela `payments`
  do card, psycopg2 em thread) com **fallback em memória** (funciona sem DB, em
  dev/testes). Init no lifespan da API; degrada para memória se o Postgres falhar.
- **`models.py`** — `Charge`, `PaymentStatus` (PENDING/PAID/EXPIRED/CANCELLED/
  REFUNDED), `PRICING` (basic 19 / standard 29 / professional 39 / enterprise 49).
  MVP usa **standard (R$ 29)**.

## API — `api/main.py`

| Endpoint | Função |
|----------|--------|
| `POST /payment/create` | cria cobrança PIX; retorna `br_code`, `qr_code_base64` (data URI), `amount_display`, `expires_at`, `charge_id` |
| `GET /payment/status?charge_id=` | polling; revalida na AbacatePay se pendente; `{status, paid}` |
| `POST /webhooks/abacatepay` | confirmação server-side; valida query-secret + HMAC |
| `GET /report/{executive,technical}?url=&charge_id=` | **402** sem cobrança paga (exceto modo livre) |

**Gating (decisão de segurança):** acesso livre aos PDFs quando
`KLARIM_DEV_MODE=true` **ou** `ABACATEPAY_API_KEY` ausente. Racional: sem chave
não há como cobrar, então não bloquear mantém o site funcional (o deploy nunca
"quebra" a venda por falta de config). Com a chave presente + dev off, exige
`charge_id` pago → **402** caso contrário.

**Webhook — autenticação:** a AbacatePay usa **2 camadas**: (1) query-string
`?webhookSecret=` e (2) assinatura HMAC-SHA256 no header `X-Webhook-Signature`.
A camada 1 (que nós controlamos) é **obrigatória** (401 se não bater). A camada 2
é *defense-in-depth*: como a AbacatePay assina com chave própria, a rejeição por
HMAC só é fatal se `ABACATEPAY_HMAC_STRICT=true`; caso contrário é apenas
registrada (evita rejeitar webhooks legítimos).

## Frontend

- Nova tela **`/pay?url=`** (`Payment.jsx`): cria a cobrança, mostra **QR code**
  (base64) + **copia-e-cola** com botão "Copiar" + valor, e faz **polling a cada
  3s**. Ao confirmar → "Pagamento confirmado!" → redireciona para
  `/report?url=&charge_id=`.
- **Result** — CTA "Ver relatório completo — R$ 29" agora vai para `/pay`.
- **Report** — lê `charge_id` da query e o repassa aos downloads; 402 vira
  mensagem de erro no botão.
- A CSP do HTTPS já permite o QR (`img-src data:`) e as chamadas `/api`
  (`connect-src 'self'`).

## Variáveis de ambiente (`.env` da VM — nunca commitadas)

`ABACATEPAY_API_KEY`, `ABACATEPAY_WEBHOOK_SECRET`, `KLARIM_DEV_MODE`
(+ opcional `ABACATEPAY_HMAC_STRICT`). A chave real é colocada só na VM.

## Validação offline (feita)

- `tests/test_payments.py` — 11 testes: pricing/display, `verify_webhook_signature`
  (HMAC base64 e hex), `MemoryStore`, parsing do client (mock), `_extract_charge_id`,
  e o **gating** (`_require_paid`): dev livre, sem-chave livre, 402 sem charge,
  ok quando pago, 402 quando pendente. Suíte total: **30 passed, 1 skipped**.
- **Sandbox AbacatePay (chave `abc_dev_`):** confirmado ao vivo que
  `create_pix_charge` devolve `id`/`brCode`/`brCodeBase64`/`expiresAt`/`status=PENDING`,
  `check_payment` devolve o status, e `POST /transparents/simulate-payment?id=`
  marca **PAID**. O client bate com a API real (não exige `customer`).

## Validação em produção (klarim.net)

Ver adendo (preenchido após deploy + configuração da chave na VM).

## Critérios de aceite

- [x] `payments/abacatepay.py` (create_pix_charge, check_payment).
- [x] `POST /payment/create` retorna QR code PIX.
- [x] `GET /payment/status` retorna status.
- [x] Webhook valida (query-secret obrigatório + HMAC) e registra pagamento.
- [x] PDFs protegidos (402 sem charge_id pago).
- [x] Frontend com QR inline + polling.
- [x] Frontend redireciona ao confirmar.
- [x] Tabela `payments` no PostgreSQL.
- [x] Modo dev mantém acesso livre.
- [x] Variáveis documentadas (chave nunca commitada).
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- **Cartão (Stripe)** como segunda opção de pagamento.
- Confirmar empiricamente o esquema exato do HMAC do webhook e, se for o secret,
  ativar `ABACATEPAY_HMAC_STRICT=true`.
- Preço por setor (hoje fixo em `standard`); ligar à classificação de plataforma/setor.
- Cachear o `ScanReport` para não re-escanear na geração do PDF pós-pagamento.
