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

### Verificação de propriedade (KL-68)

- **`contact_email` nunca é exposto.** A auto-verificação Tier 1 (`_ownership_method`,
  no servidor) valida por (1) e-mail == `contact_email` → `auto_email`, ou (2) **domínio do
  e-mail == domínio do site** → `auto_domain` (KL-71), **exceto provedores públicos**
  (`PUBLIC_EMAIL_PROVIDERS`: gmail/hotmail/…, pois `email@gmail.com` não prova ser dono de
  gmail.com). O Tier 2 envia um código de 6 dígitos **ao `contact_email`** e o frontend só
  recebe o `email_hint` **mascarado** (`con****@empresa.com.br`).
- **First-come-first-served:** um site tem no máximo 1 dono verificado; `site_has_owner`
  bloqueia (409) um segundo usuário e desliga o `auto_domain` — impede sequestro de perfil.
  O perfil público some com o CTA "Reivindicar" quando já há dono (`owner_verified` → só
  "Monitorar"); o dashboard informa `has_other_owner` sem expor quem é o dono.
- **Convite de técnico (KL-71):** valida conflito de papel (422 em auto-convite,
  dono-como-técnico e já-vinculado); o e-mail do dono ao técnico é sempre **mascarado**.
- **Remoção self-service** (`DELETE /account/sites/{id}`, JWT do usuário): o próprio dono
  remove seu site — revoga a posse e desativa as vigílias, **sem** notificação (diferente
  do remove-site admin, que notifica).
- **Código:** 6 dígitos CSPRNG, TTL 30 min, **3 tentativas** (depois `failed`),
  comparação constant-time (`hmac.compare_digest`).
- **Rate limit:** `POST /account/ownership/request-verification` 5/h/IP;
  `/verify` 10/10min/IP (Redis + fallback in-memory).
- **Domain guard** (`api/domain_guard.py`): domínios públicos/institucionais
  (gmail.com, google.com, `.gov.br`, `.edu.br`…) **não** podem ser monitorados nem
  reivindicados (422 no `POST /account/sites`; sem CTA no perfil público). O **scan
  continua livre** (vitrine da plataforma) — só o monitoramento/reivindicação é bloqueado.
- Email de verificação é **transacional** (`seguranca@klarim.net`), nunca proativo.

### Laudo compartilhável + técnico vinculado (KL-44 P3)

- **Laudo público `/public/laudo/{code}`**: **sem PII** — nunca `contact_email`, e-mail/dados
  do dono ou internos do alvo; só domínio + score + checks técnicos. Rate limit **30/h/IP**
  (anti-scraping). **Código CSPRNG** (`secrets.choice`), **TTL 30 dias** (dados de segurança
  não são eternos). Página Astro SSR com `noindex` (link privado, não é conteúdo de SEO).
- **E-mail do dono mascarado** para o técnico (`d***o@x.com.br`); `technician/search` só
  devolve `{found, user_id, name}` de quem é `role='technician'`, nunca outros dados.
- **Boletim do dono** = plain text via `alerta@klarimscan.com` (proativo, respeita blocklist);
  **laudo/convite ao técnico** = transacional via `seguranca@klarim.net`. Todos com
  Reply-To `scan@klarim.net` e registrados no `email_log`. Endpoints novos: rate limit
  Redis+fallback (invite 10/h, shared-report 20/h, laudo 30/h).

### Vigílias avançadas (KL-44 P4)

- **100% passivo:** `uptime` é um GET honesto (User-Agent `KlarimScanner/1.0`); `changes`
  faz 1 GET e compara um snapshot leve (hash de conteúdo/headers, contagem de scripts/forms);
  `phishing`/typosquat só **lê os CT logs públicos** que o discovery já consome — nunca
  registra domínio, nunca sonda o suspeito. Nenhum check novo altera o score de segurança.
- **Anti-spam:** uptime exige **3 falhas consecutivas** antes de alertar (anti-glitch) e
  **1 alerta de "fora do ar" por hora**; envia 1 alerta de recuperação ao voltar. `changes`
  só alerta em mudança **significativa** (conteúdo >30%, título, headers, scripts↑, forms↑).
- **Enforcement de plano** é servidor-autoritativo: `uptime` é Pro+, `changes`/`phishing`
  são Agency; o worker desativa a vigília se o plano deixa de permitir (nunca por erro
  transiente de lookup). E-mail de vigília é **proativo** → respeita a blocklist (KL-24/62),
  Reply-To `scan@klarim.net`, registrado no `email_log`.
- **Config ao vivo:** `BULLETIN_ENABLED` (bool) e `BULLETIN_HOUR_UTC` no painel
  (`admin_settings` > `.env`), relidos por ciclo — sem redeploy. `/admin/typosquat-alerts`
  exige JWT admin.

### Gestão de usuários (KL-69)

- **Enforcement de `is_active` no login:** `POST /account/login` retorna **403** para conta
  desativada (`is_active=false`), mesmo com senha correta — mensagem aponta para
  `seguranca@klarim.net`.
- **Ações admin** (`/admin/users/{id}/remove-site|deactivate|reactivate`, `/admin/clean-
  blocked-sites`) exigem **JWT admin** (prefixo `/admin`) + **rate limit** 30/min/IP
  (Redis + fallback). Remover um site **não** apaga a conta (segue ativa); revoga a posse
  (`ownership_verifications.status='revoked'`, auditoria).
- **Notificações** de site removido / conta desativada / reativada são **transacionais**
  (`seguranca@klarim.net`), registradas no `email_log` (`site_removed` / `account_deactivated`
  / `account_reactivated`).

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
- **Reply-To (KL-67):** `_send`/`_send_batch` injetam **`Reply-To: scan@klarim.net`**
  (`setdefault`) em TODO e-mail — `seguranca@`/`alerta@` são só-envio (Resend), as respostas
  caem no inbox Hostinger (`scan@`, painel Inbox). `send_contact` mantém o seu (visitante).

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
