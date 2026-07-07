# KL-10 — Página de recuperação de relatórios por e-mail (token temporário)

- **Card Jira:** KL-10
- **Data:** 2026-07-07
- **Executor:** Claude CLI (Opus 4.8)
- **Depende de:** KL-7 (pagamentos c/ buyer_email), KL-8 (e-mail), KL-9 (cache)
- **Commit:** `feat(KL-10): add report recovery page with email token verification`

---

## Objetivo

Cliente que pagou mas não recebeu o relatório (spam, trocou de aparelho, perdeu o
link) recupera o acesso em `klarim.net/recuperar` via link temporário por e-mail —
sem contato manual.

## Backend

- **Tabela `recovery_tokens`** (`token`, `buyer_email`, `created_at`, `expires_at`,
  `used_at`) + índice por e-mail. Token `secrets.token_urlsafe(48)` (64 chars),
  **TTL 24h**, reutilizável até expirar. Migração no `ensure_schema`.
- **Store** (Postgres + memória): `list_paid_charges_by_email`, `create_recovery_token`,
  `get_valid_recovery_token` (checa `expires_at > NOW()`), `count_recent_recovery_requests`
  (rate limit, última hora).
- **Endpoints:**
  - `POST /recovery/request {email}` → **sempre** `200` com mensagem genérica.
    O trabalho real (rate limit → busca pagamentos PAID → gera token → envia
    e-mail) roda em **background** (`_spawn`), então o tempo de resposta não vaza
    se o e-mail existe.
  - `GET /recovery/validate?token=` → `{valid, email (mascarado), reports[]}` ou
    `{valid:false, error}`.
  - `GET /recovery/download?token=&charge_id=&type=executive|technical` → PDF (usa
    o cache do KL-9). **Validação cruzada:** token válido **e** o charge pertence
    ao `buyer_email` do token **e** está PAID, senão **401**.

## E-mail

`notifier/templates/recovery.html` (table-based, dark) + `KlarimMailer.send_recovery_link`.
Assunto "🔑 Acesso aos seus relatórios Klarim", CTA para o link com token, aviso
de expiração em 24h.

## Frontend

- **`/recuperar`** — input de e-mail → `POST /recovery/request` → mensagem
  genérica (não revela se achou).
- **`/recuperar/acesso?token=`** — valida o token; se válido, lista os relatórios
  (site, data, valor) com botões "Baixar Executivo/Técnico" (via `/recovery/download`);
  se inválido/expirado, "Link expirado" + "Solicitar novo link".
- **Footer** de todas as telas: link "Recuperar relatórios".

## Segurança (Parte 4)

| Requisito | Implementação |
|-----------|---------------|
| Não revelar e-mails | resposta genérica + trabalho em background |
| Rate limit | 3 solicitações por e-mail por hora (`recovery_tokens.created_at`) |
| Token seguro | `secrets.token_urlsafe(48)` |
| TTL 24h | `expires_at`, checado no `get_valid_recovery_token` |
| Validação cruzada | download exige `charge.buyer_email == token.buyer_email` + PAID |
| E-mail mascarado | `mask_email` → `h***l@example.com` |

## Validação

- `tests/test_payments.py` — `mask_email`, ciclo de token (válido/expirado/
  inexistente), `list_paid_charges_by_email`, `count_recent_recovery_requests`,
  `recovery_validate` (e-mail mascarado + lista) e `recovery_download` (401 cruzado).
  Suíte: **43 passed, 1 skipped**.
- **Produção (klarim.net):**
  - Tabela `recovery_tokens` criada.
  - `POST /recovery/request` (e-mail com 2 cobranças PAID) → mensagem genérica;
    log `[recovery] link enviado para k***n@gmail.com (id=…)` (e-mail real da
    `seguranca@klarim.net`).
  - **Anti-enumeração:** request com e-mail sem pagamento → mesma mensagem,
    **nenhum token** criado (confirmado no DB).
  - **Rate limit:** 4 requests seguidos → 3 enviados + `[recovery] rate limit
    atingido` no 4º; DB parou em 3 tokens.
  - `GET /recovery/validate` → `valid:true`, e-mail **mascarado** `k***n@gmail.com`,
    2 relatórios; token inválido → `valid:false`.
  - `GET /recovery/download` → **200** `application/pdf`; charge de **outro
    e-mail** → **401** (validação cruzada).
  - **Navegador:** `/recuperar/acesso?token=…` renderiza a lista dos relatórios
    (e-mail mascarado, valor/data, botões Executivo/Técnico); footer com
    "Recuperar relatórios".

## Critérios de aceite

- [x] Tabela `recovery_tokens`.
- [x] `POST /recovery/request` (gera token/envia ou não, sem revelar).
- [x] `GET /recovery/validate` (valida + lista relatórios).
- [x] `GET /recovery/download` (validação cruzada).
- [x] Template `recovery.html`.
- [x] Frontend `/recuperar` + `/recuperar/acesso`.
- [x] Link no footer.
- [x] Rate limit 3/e-mail/hora.
- [x] E-mail mascarado.
- [x] Token 24h.
- [x] Documentação atualizada.
- [x] Relatório em PT-BR.
- [x] Commit e push.

## Follow-ups

- Marcar `used_at` no primeiro uso (hoje o token é multiuso até expirar — por
  desenho, para baixar executivo e técnico separadamente).
- Rate limit também no `/recovery/validate`/`download` (hoje protegido por token).
