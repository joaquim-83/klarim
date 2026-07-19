# KL-82 Slice 2 — Signup sem código + confirmação por link (+ KL-85 P2/P3)

**Card:** KL-82 (Slice 2 de 3) + KL-85 (Partes 2 e 3) · **Data:** 2026-07-19
**Pré-requisito:** Slice 1 ✅ (scan anônimo result-first + níveis de acesso)

---

## Objetivo

Remover o gargalo de conversão na **criação de conta**: o signup exigia código de 6 dígitos por
e-mail antes de criar a conta. Agora: e-mail + senha → conta **na hora** (`email_confirmed=false`)
+ e-mail de boas-vindas com **link** de confirmação (uma vez, quando quiser). Mais as proteções
anti-abuso do KL-85 (rate limit de signup + blocklist de descartáveis).

## Backend

- **`POST /account/signup` reescrito:** cria a conta imediatamente (`email_confirmed=false`) e
  dispara (fire-and-forget) o e-mail de boas-vindas com link. Se o e-mail **já foi verificado no
  scan** (KL-25), nasce **confirmada** (não reenvia). O fluxo de código (`/account/verify` +
  `_store_pending_signup` + `send_signup_verification_code`) fica **dormente** como fallback
  (não removido — regra 2).
- **Token de confirmação** (`_make_confirm_token`/`_verify_confirm_token`): mesmo esquema HMAC do
  scan token (`base64(json).hmac256[:32]`), `typ='confirm'` (isolado — um scan token não valida
  como confirm), **30 dias**, stateless + **idempotente** (confirmar 2x é no-op → efeito de uso
  único). **Nunca logado** (regra de segurança do card).
- **`GET /account/confirm?token=`:** valida token + confere `uid`↔`email`, marca
  `email_confirmed=true`/`email_confirmed_at`/`confirmation_source='link'`. `{status:
  confirmed|already|invalid}`.
- **`POST /account/resend-confirmation`:** exige login, no-op se já confirmado, rate limit 3/h
  por conta.
- **`_user_public`** passou a expor `email_confirmed` (NULL legado = confirmado); alimenta o
  banner do dashboard e o nível de acesso do Slice 1.
- **Store:** `create_user(email_confirmed=)`, `confirm_user_email(user_id, source)` (idempotente),
  `delete_unconfirmed_inactive_accounts(older_than_days)`.

## KL-85 — anti-abuso

- **Parte 2 — rate limit de signup:** **3/h + 5/dia por IP** (`CF-Connecting-IP`, fix do Slice 1),
  429 "Limite de cadastros atingido". (Antes era 5/h num teto só.)
- **Parte 3 — blocklist de descartáveis:** `api/disposable_emails.py` (~190 domínios: mailinator,
  guerrillamail, yopmail, temp-mail, trashmail…), checada **antes** do rate limit no signup
  (não gasta cota com lixo). E-mail descartável → 400 "use um e-mail permanente". **Não** afeta o
  scan anônimo (que funciona sem e-mail).

## Cleanup (Bloco 2D)

`delete_unconfirmed_inactive_accounts` roda **1x/dia no worker `trial`** (já tem gate de hora).
Remove contas `email_confirmed=false`, criadas há +30 dias, **sem atividade**: sem site em
`user_sites` **e** sem re-login (`last_login_at` só o toque do próprio signup, ≤ created_at+1h).
FK `ON DELETE CASCADE` limpa as tabelas filhas. **Correção do runbook:** o card sugeria proteger
por `scans.user_id`, mas a tabela `scans` não tem `user_id` (só `scanned_by_email`) — usei
`user_sites` + heurística de re-login (o signup chama `touch_user_login`, então `last_login_at`
nunca é NULL).

## E-mail (isolamento de reputação)

**Correção do runbook:** a 1ª versão do card pedia `From: alerta@klarimscan.com`, mas esse é o
domínio **proativo em warmup** (`ALERT_DAILY_LIMIT=30`). Boas-vindas é **transacional** → enviado
por **`seguranca@klarim.net`** (Reply-To `scan@`, texto puro), conforme a regra inviolável de
isolamento de e-mail do CLAUDE.md. (A versão refinada do card já corrigiu para `seguranca@`.)

## Frontend

- **`SignupForm.jsx`:** removida a etapa de código de 6 dígitos → só e-mail + senha + confirmar
  → cria e redireciona ao dashboard. Trata 400 (descartável), 409 (duplicado), 429 (rate limit).
  Componentes de código (KL-25) ficam dormentes ao fim do `ScanFlow` (Slice 1).
- **`confirmar.astro` (novo, SSR):** lê `?token=`, chama `/api/account/confirm` server-side; no
  sucesso `redirect('/dashboard?confirmed=1')`; senão página de erro com CTA "Entrar". O token
  nunca chega ao cliente.
- **`Dashboard.jsx`:** banner gentil para conta não confirmada ("📧 Confirme seu e-mail…" +
  "Reenviar link") + toast `?confirmed=1`. Some ao confirmar.
- **Nginx:** `/confirmar` adicionado à allowlist do Astro (`http.conf` + `https.conf.template`).

## Migração de contas

As 3 colunas (`email_confirmed`/`email_confirmed_at`/`confirmation_source`) já foram criadas no
Slice 1 (sem DEFAULT + backfill idempotente `WHERE IS NULL`). As 14 contas existentes seguem
`email_confirmed=true` (`source='code'`). Nenhuma quebra de contrato.

## Testes

- **`tests/test_kl82_slice2_signup.py` (11):** blocklist (puro + endpoint), rate limit 3/h,
  confirmação válida/idempotente/inválida/e-mail-trocado, resend (auth + rate limit + no-op),
  `_user_public.email_confirmed`.
- **`test_accounts.py`:** reescritos os testes do fluxo antigo (agora signup cria unconfirmed;
  confirmação por link; descartável bloqueado). `_FakeMailer.send_welcome_confirmation` +
  `FakeStore.confirm_user_email` adicionados.
- **`test_kl44_p6_payment.py`:** novo teste — o ciclo do `trial` chama o cleanup.
- Fixtures de rate limit atualizadas (novos buckets `_signup_daily_attempts`/`_resend_confirm`)
  em test_accounts/kl57/kl82_progressive.
- **Suite:** `1049 passed, 1 skipped` (1035 → 1049). Build Astro **verde**.

## Deferido (Slice 3)

Blocos 3+4 do KL-82 (Fluxo 2 do alerta: `alert_sessions` + `signup-from-alert`). O nível
`alert_session` e `_get_alert_session` já estão cabeados (stub retornando None) desde o Slice 1.
