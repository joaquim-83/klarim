# KL-25 — Scan público com e-mail confirmado (código 6 dígitos) + 1 gratuito por e-mail

- **Card Jira:** KL-25
- **Data:** 2026-07-10
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-5 (frontend), KL-9 (cache), KL-17 (scans públicos), KL-21 (tracking)
- **Commit:** `feat(KL-25): add email verification with 6-digit code for public scans, 1 free per email`

---

## Objetivo

O scan público não exigia e-mail nem tinha limite — bots/curiosos/concorrentes
consumiam recursos sem virar lead. Agora: **e-mail confirmado por código de 6
dígitos** antes de escanear, e **1 scan gratuito por e-mail**. Do 2º scan (outra URL)
em diante, cobra o relatório. Mesma URL → resultado anterior sem gastar o crédito.

## Backend

### Tabelas (`discovery/store.py`)

- `scan_verifications` (`email, code, url, verified, expires_at` [10min], `ip_address`).
- `scan_credits` (`email` unique, `free_scans_used`, `first_scan_url`, `first_scan_at`).
- Coluna `scans.scanned_by_email` — liga o scan ao lead (quem pediu).
- Métodos: `create_scan_verification` (grava + apaga expirados), `count_verifications_since`,
  `verify_scan_code`, `get_scan_credit`, `record_free_scan`, `public_scan_stats`.

### Endpoints (`api/main.py`, públicos)

- **`POST /scan/request-code {email, url}`** — limpa/valida o e-mail; checa o crédito
  (→ `already_scanned` se mesma URL, `limit_reached` se já usou em outra); gera código
  **CSPRNG** (`secrets.randbelow(900000)+100000`), grava (TTL 10min), envia via Resend.
  **Rate limit** 3/e-mail/h + 5/IP/h (in-memory).
- **`POST /scan/verify-code {email, code, url}`** — valida (não usado, não expirado),
  consome o gratuito (`record_free_scan`), devolve **scan token** HMAC (email+url+exp,
  1h). Rate limit 5/e-mail/10min.
- **`POST /scan/check-credit`** — `{has_free_scan, same_url_scanned, free_scans_used}`.
- **`GET /scan/summary`** — exige `X-Scan-Token` (ou JWT admin) para **disparar** um
  scan novo; o token precisa casar a URL. Sem token, `get_recent_only` devolve só o
  resultado **já existente** (cache/banco, nunca reescaneia) ou `{"status":"auth_required"}`.
  O e-mail do token vira `scanned_by_email`.
- **`GET /analytics/public-scans`** (JWT) — métricas do funil.

### Scan token

HMAC-SHA256 assinado com `JWT_SECRET`, payload base64 (`email/url/exp`). Verificado em
tempo constante, com expiração — não guessable.

### E-mail

`KlarimMailer.send_verification_code(email, code, domain)` + template
`verification_code.html` (dark, código grande com tracking de letra).

## Frontend

- **`Landing.jsx`** — 3 estados: **form** (URL + e-mail) → **code** (código de 6
  dígitos + "reenviar" com contador de 45s) → **limit** (CTA de pagamento). Ao
  verificar, guarda o scan token no `sessionStorage` (`api.setScanToken`) e navega para
  `/scan` (a tela de loading roda o scan enviando `X-Scan-Token`).
- **`lib/api.js`** — `checkCredit`, `requestCode`, `verifyCode`, e `fetchSummary` que
  envia o `X-Scan-Token` e trata `auth_required`.
- **`Scan.jsx` / `useSummary`** — redirecionam à home em `auth_required`.
- **Tracking (KL-21):** `code_requested`, `code_verified`, `code_failed`,
  `scan_limit_reached`.
- **Dashboard:** card "Scans públicos" em `/painel/analytics` + `scanned_by_email` no
  detalhe do scan.

## Segurança (checklist)

- [x] Código CSPRNG (`secrets.randbelow`), 6 dígitos, TTL 10min, marcado `verified` (não reusa).
- [x] Rate limits: 3 códigos/e-mail/h, 5/IP/h, 5 verificações/e-mail/10min.
- [x] Scan token HMAC (não guessable, expira 1h, casa a URL).
- [x] E-mail limpo (`_clean_email` do KL-fix) e `scanned_by_email` gravado.
- [x] Limpeza de códigos expirados a cada gravação (sem cron).
- [x] Admin (JWT) faz bypass do token; sem `RESEND_API_KEY` o request-code dá 503.

## Validação

- **Testes** (`tests/test_scan_verification.py`, 16): token (roundtrip/tamper/expiry),
  `_norm_scan_url`, request-code (envia / limpa e-mail / limit_reached / already_scanned
  / rate limit / e-mail inválido), verify-code (ok+token / invalid / rate limit),
  check-credit, e o gating do `/scan/summary` (auth_required / com token escaneia +
  propaga o e-mail / cache sem token). `test_ingest`: `scanned_by_email` chega no
  `save_scan`. **Suite completa: 231 passed, 1 skipped.**
- **Frontend:** `npm run build` ok (693 módulos).

## Fluxo do usuário (resumo)

1. URL + e-mail → código no e-mail → digita → scan → resultado + CTA R$29.
2. 2º site (mesmo e-mail) → "Você já utilizou seu scan gratuito" + CTA pagamento.
3. Mesma URL → resultado anterior (sem gastar o crédito).

## Ação na VM (após deploy)

O `ensure_schema` cria as tabelas + a coluna automaticamente no boot da API. Confirmar
que `RESEND_API_KEY` e `JWT_SECRET` estão no `.env` (já estão). Nenhuma variável nova.
