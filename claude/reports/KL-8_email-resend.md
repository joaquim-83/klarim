# KL-8 — Sistema de e-mail via Resend (alerta + entrega de relatório)

- **Card Jira:** KL-8
- **Data:** 2026-07-06
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-4 (PDFs), KL-7 (pagamentos)
- **Domínio:** `klarim.net`
- **Commit:** `feat(KL-8): add email system with Resend for alerts and report delivery`

---

## Objetivo

Enviar e-mail em dois momentos: **alerta gratuito** (semáforo — o anzol do funil)
e **entrega do relatório** pago (2 PDFs anexados), com envio automático após o
pagamento.

## `notifier/`

- **`email_client.py`** — `KlarimMailer` (`send_alert`, `send_report`,
  `send_test`). SDK `resend` (síncrono) em `asyncio.to_thread`. Templates Jinja2.
  Trata erros do Resend (`KlarimMailerError`).
- **Templates `alert.html` / `report_delivery.html`** — **table-based** com
  estilos inline (compatível com Gmail/Outlook), paleta dark do Klarim, semáforo,
  bloco LGPD (alerta), CTA, e a listagem dos anexos (entrega).

## API (`api/main.py`)

| Endpoint | Função |
|----------|--------|
| `POST /email/test` | e-mail de teste (valida a config) |
| `POST /email/send-alert` | escaneia o alvo e envia o alerta (sem anexo) |
| `POST /email/send-report` | envia os 2 PDFs (exige cobrança paga) |

**Envio automático:** ao confirmar pagamento (webhook **ou** polling), se a
cobrança tem `buyer_email` e ainda não enviou, dispara o relatório em background
(`asyncio.create_task`), idempotente via `report_email_sent`. Falha só é logada —
o cliente ainda baixa o PDF no site (fallback).

## Fluxo de compra + persistência

- A tela **`/pay`** agora pede o **e-mail** antes de gerar a cobrança.
- Colunas novas em `payments`: **`buyer_email`** e **`report_email_sent`**
  (via `ALTER TABLE ADD COLUMN IF NOT EXISTS` — migração idempotente).

## Variáveis de ambiente (`.env` da VM — nunca commitadas)

`RESEND_API_KEY`, `RESEND_FROM`. A chave real fica só na VM.

## Parte 7 — Verificação de domínio no Resend (ação do operador)

A chave fornecida é **send-only** (não gerencia domínios pela API — confirmado:
`Domains.list/create` retornam *"This API key is restricted to only send emails"*).
Portanto a verificação é **manual no painel**:

1. `resend.com/domains` → **Add Domain** → `klarim.net` (região `sa-east-1`).
2. Adicionar na **Hostinger** os registros **exatos** que o Resend exibir
   (o DKIM é gerado por domínio). Tipicamente:

   | Tipo | Nome | Valor |
   |------|------|-------|
   | MX | `send.klarim.net` | `feedback-smtp.sa-east-1.amazonses.com` (prio 10) |
   | TXT (SPF) | `send.klarim.net` | `v=spf1 include:amazonses.com ~all` |
   | TXT (DKIM) | `resend._domainkey.klarim.net` | `p=MIGfMA0GCSq…` (chave do painel) |
   | TXT (DMARC) | `_dmarc.klarim.net` | `v=DMARC1; p=quarantine;` |

   > Observação: versões antigas do Resend usam `include:_spf.resend.com`. Use
   > **sempre o que o painel mostrar** para o seu domínio.
3. Aguardar verificação (~1–5 min) e trocar `RESEND_FROM` na VM para
   `Klarim <seguranca@klarim.net>` (redeploy ou recriar o `api`).

Enquanto não verificado, `RESEND_FROM=Klarim <onboarding@resend.dev>` — que só
entrega para o **e-mail dono da conta Resend** (`klarimscan@gmail.com`).

## Validação

- `tests/test_notifier.py` — 6 testes offline (semáforo, `site_name`, render dos
  templates, `send_test`/`send_alert`/`send_report` com Resend mockado, incluindo
  os anexos base64). Suíte total: **36 passed, 1 skipped**.
- **Envio real (Resend, local):** `send_test`, `send_alert` e `send_report`
  (com os 2 PDFs de referência do Verdegreen anexados) enviados com sucesso para
  `klarimscan@gmail.com` — `email_id` retornado nos três.
- **Produção (klarim.net):** ver adendo (após deploy).

## Critérios de aceite

- [x] `email_client.py` (send_alert, send_report, send_test).
- [x] `alert.html` com semáforo, LGPD, CTA (table-based).
- [x] `report_delivery.html` com PDFs anexados.
- [x] `POST /email/send-report` (exige charge_id pago).
- [x] `POST /email/send-alert` (scan + envio).
- [x] `POST /email/test`.
- [x] `buyer_email` no fluxo de pagamento.
- [x] Envio automático após confirmação de pagamento.
- [x] Variáveis documentadas (chave nunca commitada).
- [x] Instruções de verificação de domínio (acima).
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- **Verificar `klarim.net` no Resend** para enviar a qualquer destinatário e sair
  do `onboarding@resend.dev` (hoje restrito ao dono da conta).
- Unsubscribe real (hoje `mailto:` placeholder) — necessário para envio proativo
  em escala (Discovery Worker).
- Ligar o `/email/send-alert` ao Discovery Worker (Fase 2).
