# KL-102 — List-Unsubscribe (RFC 8058) + endpoint `/remover`

**Card:** KL-102 (High) · **Status:** ✅

## Contexto
Testes de deliverability (mail-tester, 24/07) deram 10/10 nos 4 senders, mas apontaram a ausência
do header `List-Unsubscribe` nos 3 senders cold. Gmail/Yahoo exigem esse header (RFC 8058) para
volume > 5k/dia — ainda não atingimos (~200/dia), mas a ausência reduz reputação e bloqueia o
scale-up. O opt-out por resposta ("remover") já existia e continua em paralelo.

## Entregue

### 1. Token HMAC (`notifier/email_client.py`)
- `generate_unsubscribe_token(email, domain, secret, sender_domain)` / `verify_unsubscribe_token`:
  propósito **`unsubscribe`** (payload JSON com `typ`) — **não colide** com o `alert_session`/
  `alert_access` do KL-82. **SEM expiração** (um opt-out deve funcionar sempre). Formato
  `base64url(json).hmac[:32]` — URL-safe, seguro em header de e-mail. **Comparação constant-time**
  (`hmac.compare_digest`). Normaliza o e-mail (lowercase). Segredo:
  `UNSUBSCRIBE_SECRET`/`JWT_SECRET`/`HMAC_SECRET`.
- `build_cold_unsubscribe_headers(email, domain, sender_domain)` → `{"List-Unsubscribe":
  "<mailto:scan@klarim.net?subject=remover>, <https://klarim.net/remover?token=...>",
  "List-Unsubscribe-Post": "List-Unsubscribe=One-Click"}`. Sem segredo → cai para só o mailto
  (envio nunca quebra).

### 2. Headers nos senders cold (NÃO no transacional)
`send_cold_alert` (KL-91, alertas./aviso.) e `send_profile_view` (KL-101, perfil.) passam a montar
o header com `build_cold_unsubscribe_headers`. O transacional (`klarim@klarim.net`) não recebe.

### 3. Endpoint `/remover` (`api/main.py`, roteado pelo nginx → FastAPI)
- **`GET /remover?token=`**: página de confirmação (válido → botão "Confirmar remoção" num
  `<form method=POST>`; inválido → "Link inválido"; já removido → "Já removido"). Sempre **200**.
- **`POST /remover?token=`**: confirma — form do browser OU **one-click do Gmail** (body
  `List-Unsubscribe=One-Click`). Aplica `_remover_apply`: `mark_unsubscribed(email)` (todos os
  alvos do e-mail) + `block_email(email, 'unsubscribe')` + **evento `email_log`** (`type=unsubscribe`,
  `from_domain`=sender, `target_id`, `status='unsubscribe'` — não polui as contagens de sent/bounce;
  o setor sai por join `target_id`→`targets.sector`, KL-57). Idempotente (já removido → "Já removido").
- **nginx**: `location = /remover` proxia ao FastAPI sem strip de prefixo (http.conf +
  https.conf.template). HTML utilitário (estilos inline) → sem a CSP estrita, como o `/api/unsubscribe`.

## Segurança (revisão)
- **HMAC constant-time**; token `typ`-isolado (não vale como sessão/confirm).
- **Anti-enumeração:** inválido/ausente → mensagem genérica (GET 200 / POST 400), nunca revela se o
  e-mail/domínio existe. Um token válido só existe se a Klarim o gerou → sem oráculo.
- **Rate limit 10/min/IP SÓ nos tokens INVÁLIDOS** (anti brute-force). Um token **válido** é
  idempotente e escopado ao próprio e-mail → **nunca** é bloqueado: o one-click do Gmail vem de IP
  **compartilhado** do Google; bloqueá-lo faria a pessoa marcar spam (pior que o "abuso" evitado).
  Decisão documentada em `docs/SECURITY.md`.
- **Token URL-safe** (base64url, sem `=`/`+`/`/` que quebrariam em header/URL).

## Testes
`test_kl102_unsubscribe.py` (+14): token (determinístico/roundtrip/tamper/wrong-secret/URL-safe),
headers cold full RFC 8058 + fallback mailto, `send_cold_alert` com one-click, **transacional SEM
header**, `/remover` GET (confirm/invalid), POST (unsubscribe→banco+blocklist+evento / invalid 400 /
idempotente / rate limit só inválidos / válido nunca limitado). **Suite: 1627 passed.** nginx
validado no CI (`nginx -t`). Fluxo real (token→GET→POST→status no banco) confirmado pós-deploy na VM.

## Não quebrou
Opt-out por resposta segue funcionando; o `/unsubscribe` legado (KL-24, token por e-mail) intacto;
o transacional inalterado; templates cold seguem plain text (o header não altera o corpo).
