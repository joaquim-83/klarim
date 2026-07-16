# Klarim — Segurança

> Consolida as "Regras invioláveis" espalhadas pelo antigo `claude.md`, o hardening de
> auto-auditoria e as decisões das auditorias (`claude/reports/auditoria_seguranca_
> 2026-07-15.md`, `fix_security_hardening.md`, `fix_auditoria*.md`). Histórico íntegro em
> `docs/HISTORY.md`.

## 1. Postura legal — só varredura passiva

O Klarim é *Security Rating* / monitoramento de superfície de ataque. **NÃO é pentest.**

- ✅ **Faz:** `GET`/`HEAD` a URLs públicas, leitura de headers, certificados SSL
  públicos, consulta DNS pública, arquivos que o servidor entrega sem autenticação,
  APIs públicas de leitura (crt.sh, HIBP, Safe Browsing, RDAP, IBGE, BrasilAPI).
- ❌ **NUNCA:** payloads de injeção (SQLi/XSS), brute-force de credenciais, acesso a
  área autenticada, exploração de vulnerabilidade, extração de dados.
- **Rede:** timeout 10s/request; **rate limit 1 req/s por domínio** (centralizado em
  `checks/base.py` — não reimplemente); **User-Agent honesto** (não se passa por
  navegador, não se esconde). Um site que bloqueia o UA honesto **não pode ser
  crawleado** — é limitação, não bug (perfil fica esparso; loga o bloqueio).

Na dúvida, trate o alvo como site de terceiro que só autorizou olhar o que é público.

## 2. Regra de segurança de 2026-07-15 (inviolável)

- **Toda implementação ou fix deve incluir revisão de segurança.**
- **Nenhum endpoint, formulário ou fluxo de dados pode ficar sem proteção** (auth,
  validação, rate limit, sanitização).
- **Empresas de cibersegurança estão entre os alvos** e interagem ativamente com a
  plataforma — assuma que tudo será sondado.

## 3. Hardening da própria plataforma (auto-auditoria)

- **Docs da API off em produção:** `/docs`/`/redoc`/`/openapi.json` só existem com
  `KLARIM_DEV_MODE=true` (senão 404).
- **Rate limit no login:** `POST /auth/login` = 5 tentativas/min por IP (`X-Real-IP` do
  Nginx); 6ª → 429.
- **Anti stored-XSS no `/events`:** `_sanitize_str`/`_sanitize_metadata` removem tags e
  esquemas (`javascript:`/`data:`), limitam tamanho/profundidade. React escapa `{}` (sem
  `dangerouslySetInnerHTML`).
- **Inbox:** corpo de e-mail externo (não confiável) renderiza em `<iframe sandbox="">`
  + `srcDoc` — **NUNCA** `dangerouslySetInnerHTML` (evita roubo do JWT do operador).
- **Nginx bloqueia paths sensíveis:** dotfiles (`/.env`, `/.git`), extensões perigosas
  (`.php/.sql/.bak/.log/.yaml…`), paths de outros frameworks (`wp-admin`, `phpinfo`…) e
  diretórios suspeitos → 404. ACME (`/.well-known/acme-challenge/`) e `security.txt`
  usam `location ^~`/`=` para vencer o regex.
- **Security headers com `always`** (aparecem até em 4xx/5xx). ⚠️ Um `add_header` num
  `location` **quebra a herança** de todos os do `server` — **repita os de segurança**
  em cada `location` com `add_header` próprio (snippet `security_headers.conf`).
- **Resolver dinâmico no proxy** (`/api/`, `/mcp/`): evita o 502 quando o container `api`
  é recriado com IP novo. `resolver 127.0.0.11` + upstream em variável.
- **CSP:** público **estrito** (`script-src 'self'` + hashes SHA-256 dos scripts inline
  do Astro — ao mexer neles, recalcule os hashes); `/painel` **relaxado**
  (`unsafe-inline`, é noindex/operator-only). `style-src` mantém `unsafe-inline`. COOP
  `same-origin`, COEP `require-corp`, CORP `same-origin`, Permissions-Policy restritiva.

## 4. Autenticação e autorização

- **Duas superfícies distintas, mesmo `JWT_SECRET`, `typ` diferente:**
  - **Admin/operador** — `POST /auth/login` (`{username,password}` do `.env`/hash bcrypt
    no banco), JWT Bearer `typ=admin`, TTL 24h. `_verify_token` exige `typ=admin`.
  - **Usuário** — `/account/*`, senha bcrypt, JWT `typ=user` em cookie `klarim_session`
    (HttpOnly, Secure, SameSite=Lax), TTL 30d. `verify_user_token` exige `typ=user`.
  - Um cookie de usuário **jamais** passa no middleware admin (o `typ` nunca é ignorado).
- **Middleware** protege os prefixos: `/targets`, `/scans`, `/alerts`, `/rescans`,
  `/email`, `/payments`, `/config`, `/discovery`, `/admin`, `/system`, `/analytics`,
  `/leads`, `/monitoring/admin`. Exceção pública: `POST /email/webhook` (token próprio).
- **MCP** — auth própria (OAuth 2.1/PKCE S256 obrigatório + `MCP_API_KEY` estático),
  fail-closed, constant-time; `redirect_uri` sempre validada; code one-time 60s; refresh
  rotacionado; rate limits. Fora do JWT admin.
- **IDOR-safe:** `/account/vigilia*` filtra por `user_id`. **Enforcement de plano é
  servidor-autoritativo** (403 no `POST /account/sites`, nunca só no frontend).
- Trocar a senha do admin ou rotacionar o token MCP **invalida os refresh tokens OAuth**.

## 5. Privacidade de dados

- **`contact_email`, `cnpj`, `whatsapp` NUNCA são expostos** na API/perfil público
  (`_PUBLIC_PROFILE_FIELDS` filtra). O perfil público só mostra dado que o site já
  publica + o score — nunca detalhe PASS/FAIL dos checks.
- **Resultado gratuito nunca vaza** detalhe dos checks pagos (headline, evidência,
  impacto, correção) — só status ✅/❌ nos 15 grátis e 🔒 nos pagos.
- Score de site oculto (`public_visible=FALSE` ou descartado) → `/score` devolve `null`,
  some do sitemap e do `/public/profile`.
- Landing do perfil pode ser editada pelo admin (`edited_by_admin`) — o enrich
  automático **preserva** esses campos.

## 6. Reputação de e-mail (anti-bounce, KL-24/62)

- **Isolamento de reputação:** proativo de `alerta@klarimscan.com` (domínio separado, em
  warmup); transacional de `seguranca@klarim.net`. **Nunca misturar.**
- **Validação de MX na captação** (`contact.py`): só aceita e-mail com registro MX
  (tri-estado `ok|no_mx|unknown`, fail-open no timeout).
- **Blocklist central** (`email_blocklist`, por e-mail). **Webhook Resend** (Svix):
  bounce permanente → descarta + bloqueia; complaint → unsubscribe + bloqueia.
- **Auto-pause** se o bounce rate passa de `ALERT_MAX_BOUNCE_RATE` (com amostra mínima).
- **Cota mensal** (`ALERT_MONTHLY_LIMIT`, 45k dos 50k Resend Pro) — nunca estourar; reserva
  para transacionais.
- **Regra por construção (KL-62):** todo e-mail passa por `KlarimMailer._send` →
  registra em `email_log`. **Proativo respeita a blocklist; transacional pode ignorá-la
  mas SEMPRE registra.** O log é fire-and-forget; a checagem de blocklist é fail-open.

## 7. Segredos

- **Nunca commitar** `.env`, chaves SSH, service account keys. Tudo sensível vive em
  **GitHub Secrets** ou no `/opt/klarim/.env` da VM.
- CI autentica no GCP por **Workload Identity Federation (OIDC, keyless)** — org policy
  proíbe chaves de SA.
- Config editável: `admin_settings` (banco) > `.env` > default; nenhuma resposta expõe
  senha/hash/token inteiro (token MCP sempre mascarado).

## 8. Webhooks

| Webhook | Validação |
|---|---|
| `POST /webhooks/abacatepay` | query-secret obrigatório + HMAC (defense-in-depth) |
| `POST /webhooks/resend` | assinatura **Svix** (`RESEND_WEBHOOK_SECRET`); 401 se inválida |
| `POST /email/webhook` (Hostinger) | token próprio, **fail-closed** (sem env ⇒ 401); loga nomes de headers (nunca valores) em 401 |

## 9. Checklist de revisão de segurança (toda feature nova)

- [ ] O endpoint/fluxo tem **auth** apropriada (admin JWT / user JWT / token próprio / público consciente)?
- [ ] **Validação de input** (formato, tamanho, tipo) e **sanitização** onde há dado de terceiro?
- [ ] **Rate limit** (Redis `_redis_allow` com fallback)?
- [ ] Risco de **IDOR** (filtra por `user_id`/dono)? Enforcement server-side, não só no front?
- [ ] Expõe **dado sensível** (e-mail/CNPJ/WhatsApp/detalhe de check pago)? Se sim, remover.
- [ ] Se toca o scanner: **100% passivo** (só GET/HEAD/DNS público)?
- [ ] Se envia e-mail: passa por `KlarimMailer._send` (log + blocklist)? Proativo respeita blocklist?
- [ ] Se mexe no Nginx: `nginx -t` ok? Security headers preservados em cada `location`?
- [ ] Se muda scoring/check: **flush `scan:*` no Redis** após deploy?
- [ ] Corpo HTML de terceiro nunca no DOM (só `<iframe sandbox>`)?
