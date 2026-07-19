# KL-82 Slice 3 — Fluxo 2 do alerta (sessão temporária + signup só com senha)

**Card:** KL-82 (Slice 3 de 3 — Blocos 3+4) · **Data:** 2026-07-19
**Fecha o KL-82.** Pré-requisitos: Slice 1 (scan result-first + níveis de acesso) e Slice 2
(signup sem código + confirmação).

---

## Objetivo

Converter quem **recebe um alerta** em conta com o mínimo de atrito: o clique no link do alerta
(prova de posse do e-mail via HMAC) dá acesso **completo** ao resultado daquele site, e a conta
se cria com **só uma senha** — o e-mail já está provado.

## Fluxo

1. O e-mail de alerta agora tem, como CTA primário, um **link HMAC**
   `https://klarim.net/api/alert-access?token=…` (`email|target_id|domain`, 30 dias).
2. `GET /alert-access` valida o token → emite a **sessão temporária** (cookie `klarim_alert`,
   JWT-HMAC 24h, `typ=alert_session`, **escopada a 1 site**) + registra `alert_sessions` (funil)
   → redireciona para `/scan?url=https://{domain}`.
3. `/scan/result` vê o cookie → nível **`alert_session`** → resultado COMPLETO daquele site (48
   checks + evidência + benchmark + riscos + PDF). **Escopo:** se o domínio pedido não bate o da
   sessão, cai para `anonymous` (nunca vaza checks de outro site).
4. `ScanResultDetail` mostra o `AlertSignup` (só senha) → `POST /account/signup-from-alert` cria
   a conta (`email_confirmed=true`, `source='hmac'`), vincula o site + auto-verifica posse
   (Tier 1: e-mail == contact_email → `auto_email`), login automático → dashboard.

## Backend

- **Tokens (`api/main.py`):** `_make_alert_session_token`/`_verify_alert_session_token`
  (`typ=alert_session`, 24h) + `_verify_alert_access_token` (`typ=alert_access`, valida o link do
  e-mail). Helper genérico `_verify_token_typed` (assinatura + exp + `typ`). **Isolamento de `typ`:**
  um alert-access não vale como sessão, nem como confirm/scan token (testado).
- **`GET /alert-access`** (rate limit 30/h/IP): token inválido → redirect `/` (nunca 5xx). Cookie
  httponly/secure/samesite=lax.
- **`POST /account/signup-from-alert`** (rate limit 5/h/IP): lê o cookie, `_PW_MIN`, e-mail já com
  conta → `{existing_account:true}`; senão `_create_account_record(..., email_confirmed=True,
  confirmation_source='hmac', url=https://{domain})` → claim/link/Tier 1 + trial + lead. Marca
  `alert_sessions.converted`.
- **`/scan/result`:** escopo da sessão + `alert_signup`/`alert_email_hint` (mascarado — o
  `contact_email` **nunca** sai em claro). `_access_level` já retornava `alert_session` (cabeado
  no Slice 1); `_get_alert_session` agora lê e valida o cookie.
- **Store:** tabela `alert_sessions` (token_hash = SHA-256 do JWT; o JWT nunca é persistido) +
  `create_alert_session`/`mark_alert_session_converted`. `create_user`/`_create_account_record`
  ganharam `confirmation_source` (para gravar `'hmac'`).

## E-mail (contrato cross-módulo)

`notifier.email_client.build_alert_access_link(email, target_id, domain, secret)` produz o token
no **formato idêntico** ao que `api.main._verify_alert_access_token` valida (base64(json).hmac[:32],
mesmo segredo `JWT_SECRET|UNSUBSCRIBE_SECRET`) — como o `bonus_scan_token` faz. Um **teste de
contrato** (build no email_client → verify no api.main) trava contra drift. O CTA só vira link
HMAC para o alerta normal (não score 100) e quando há segredo + target_id; senão cai no perfil
público (retrocompatível). Sem HTML/tracking novo — segue texto puro.

## Frontend

`ScanResultDetail.jsx` ganhou `AlertSignup` (nível `alert_session`): form **só senha** →
`/account/signup-from-alert`; sucesso → `/dashboard?claimed={domain}`; `existing_account` →
`/entrar`. Eventos `account_created_alert`/`alert_session_converted`. Mobile-first (input h-12
≥16px, botão `w-full sm:w-auto`).

## Segurança

- Link do alerta HMAC-SHA256 (infalsificável, 128-bit sig). Cookie de sessão isolado do
  `klarim_session` (nome distinto; não dá acesso ao dashboard — só ao resultado daquele site).
- **Escopo por site** validado no backend (não confia no cookie para outros alvos).
- `contact_email` nunca exposto (só hint mascarado).
- Rate limits em ambos os endpoints. Risco conhecido (aceito, como todo magic-link): e-mail
  encaminhado permite a terceiro criar conta com o e-mail do dono — inerente ao modelo "o clique
  é a prova"; mitigado por ser e-mail de negócio + conta do próprio site.

## Testes

- **`tests/test_kl82_slice3_alert.py` (9):** contrato cross-módulo; `/alert-access` (cookie +
  redirect, inválido→home); `/scan/result` alert_session (acesso completo + hint mascarado;
  **escopo** — outro site cai p/ anonymous sem vazar checks); `signup-from-alert` (cria confirmed
  `hmac` + vincula + auto-verifica + converte sessão; e-mail existente; sem sessão→401; senha curta).
- Fixtures dos 5 FakeStores atualizadas (`create_user(confirmation_source=)`).
- **Suite:** `1058 passed, 1 skipped` (1049 → 1058). Build Astro **verde**.

## KL-82 — completo

Slice 1 (scan anônimo result-first + níveis) · Slice 2 (signup sem código + confirmação +
KL-85 anti-abuso) · **Slice 3 (Fluxo 2 do alerta)**. Os 9 blocos originais entregues.
